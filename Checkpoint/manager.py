"""CheckpointManager：turn 对齐的 checkpoint 编排。

线程模型：L1 写发生在 daemon worker 线程、L2 在 Timer 线程、L3 在事件循环线程——
所有公开入口持同一把 RLock；emit 路径极短（内存 append + blob 落盘）。

turn 归属规则：事件归发生时处于 open 状态的 checkpoint；无 open checkpoint
（turn 间隙的后台管线写入）→ pending 缓冲，下一个 begin_turn 并入。
方向是"多收"而非"漏收"，对可回退系统是安全侧。
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .gitbase import GitBase
from .models import (
    BlobRef,
    FileOp,
    MemoryOp,
    manifest_summary,
)
from .reconcile import make_excluder, reconcile_candidates, rel_to_root
from .rollback import rollback_chain
from .storage import CheckpointStorage

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "checkpoint.enabled": True,
    "checkpoint.dir": "./runtime_memory/checkpoints",
    "checkpoint.max_count": 200,
    "checkpoint.retention_days": 30,
    "checkpoint.max_total_mb": 500,
    "checkpoint.max_blob_mb": 20,
    "checkpoint.git_baseline_enabled": True,
    "checkpoint.rollback_on_conflict": "warn",
    "checkpoint.exclude": ["runtime_memory/**", ".goharu/**", "**/__pycache__/**", "*.pyc", ".git/**"],
}


def _cfg(config: Any, key: str, default: Any) -> Any:
    """支持 Config 风格 .get(dotted)、扁平 dict、嵌套 dict 三种配置形态。"""
    if config is None:
        return default
    if hasattr(config, "get") and not isinstance(config, dict):
        try:
            return config.get(key, default)
        except TypeError:
            pass
    if isinstance(config, dict):
        if key in config:
            return config[key]
        node: Any = config
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node
    return default


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(path: str) -> str:
    import os

    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _sanitize(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:40] or "x"


class CheckpointManager:
    def __init__(
        self,
        config: Any = None,
        memory: Any = None,
        repo_root: Optional[str] = None,
        event_sink: Optional[Callable[[str, dict], None]] = None,
    ):
        self._cfg_src = config
        self._enabled = bool(_cfg(config, "checkpoint.enabled", True))
        root = Path(_cfg(config, "checkpoint.dir", _DEFAULTS["checkpoint.dir"]))
        self._storage = CheckpointStorage(root)
        self._memory = memory  # duck-typed：restore_l1_atom(old_row, atom_id) -> dict|None
        self._repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self._event_sink = event_sink
        self._git_enabled = bool(_cfg(config, "checkpoint.git_baseline_enabled", True))
        self._git = GitBase(self._repo_root) if self._git_enabled else None
        self._git_ok: Optional[bool] = None  # probe 结果缓存
        self._conflict_policy = str(_cfg(config, "checkpoint.rollback_on_conflict", "warn"))
        self._max_blob_bytes = int(_cfg(config, "checkpoint.max_blob_mb", 20)) * 1024 * 1024
        self._excluder = make_excluder(list(_cfg(config, "checkpoint.exclude", _DEFAULTS["checkpoint.exclude"])))
        self._lock = threading.RLock()
        self._open: Optional[dict] = None       # 构建中的 checkpoint
        self._pending: list[dict] = []          # open 窗口外到达的事件
        self._last_summary: Optional[dict] = None
        logger.info("CheckpointManager 初始化: dir=%s git=%s repo=%s", root, self._git_enabled, self._repo_root)

    # ------------------------------------------------------------- 事件出口
    def _emit(self, event: str, payload: dict) -> None:
        if self._event_sink:
            try:
                self._event_sink(event, payload)
            except Exception as e:
                logger.warning("checkpoint 事件广播失败 %s: %s", event, e)

    # ------------------------------------------------------------- git 基线
    def _probe_git(self) -> bool:
        if self._git is None:
            return False
        if self._git_ok is None:
            self._git_ok = self._git.probe()
        return bool(self._git_ok)

    def _git_restore(self, base_ref: str, rel_path: str, abs_path: str) -> bool:
        if not self._git:
            return False
        data = self._git.show_file(base_ref, rel_path)
        if data is None:
            return False
        from .storage import _atomic_write

        _atomic_write(Path(abs_path), data)
        return True

    # -------------------------------------------------------------- turn 界
    def begin_turn(self, session_id: str, turn_count: int) -> Optional[int]:
        """turn 开始：建 checkpoint 骨架 + git 基线 + 并入 pending。返回 seq。"""
        if not self._enabled:
            return None
        with self._lock:
            if self._open is not None:
                logger.warning("上一 checkpoint %s 未关闭（异常路径？），按 error 收口",
                               self._open["checkpoint_id"])
                self._close_locked("error")
            seq = self._storage.next_seq()
            cid = f"{seq:04d}_{_sanitize(session_id)}_t{turn_count}"
            git_info: dict[str, Any] = {"enabled": self._git_enabled, "degraded": False,
                                        "degrade_reason": None, "base_ref": None, "ref_protected": False}
            status_start: dict[str, str] = {}
            if self._git_enabled:
                if self._probe_git():
                    try:
                        base_ref = self._git.create_base_ref() if self._git else None
                        if base_ref:
                            git_info["base_ref"] = base_ref
                            git_info["ref_protected"] = bool(self._git and self._git.protect_ref(seq, base_ref))
                            status_start = self._git.status_porcelain() if self._git else {}
                        else:
                            git_info["degraded"] = True
                            git_info["degrade_reason"] = "base_ref 创建失败"
                    except Exception as e:
                        git_info["degraded"] = True
                        git_info["degrade_reason"] = str(e)
                else:
                    git_info["degraded"] = True
                    git_info["degrade_reason"] = "非 git 仓库或 git 不可用"
            if git_info["degraded"]:
                self._emit("checkpoint.degraded", {"seq": seq, "reason": git_info["degrade_reason"]})
            self._open = {
                "version": 1,
                "seq": seq,
                "checkpoint_id": cid,
                "kind": "turn",
                "session_id": session_id,
                "turn_count": turn_count,
                "started_at": _now(),
                "ended_at": None,
                "outcome": None,
                "rollback_of": None,
                "git": git_info,
                "files": [],
                "memory": {"l1": []},
                "reconcile": {"hooked": 0, "git_caught": 0, "excluded": 0},
                "warnings": [],
                "_status_start": status_start,   # 内部字段，落盘前剔除
            }
            if self._pending:
                merged_files = sum(1 for e in self._pending if e["_kind"] == "file")
                logger.info("checkpoint: 并入 %d 条 turn 间隙事件 (%d 文件)", len(self._pending), merged_files)
                for evt in self._pending:
                    self._append_locked(evt)
                self._pending.clear()
            # retention（锁内）。当前 turn 的 manifest 尚未落盘，为即将到来的它
            # 预留一个槽位：max_count-1
            try:
                self._storage.apply_retention(
                    max_count=max(1, int(_cfg(self._cfg_src, "checkpoint.max_count", 200)) - 1),
                    retention_days=int(_cfg(self._cfg_src, "checkpoint.retention_days", 30)),
                    max_total_mb=int(_cfg(self._cfg_src, "checkpoint.max_total_mb", 500)),
                    on_drop_seq=(self._git.drop_ref if self._probe_git() and self._git else None),
                )
            except Exception as e:
                logger.warning("checkpoint retention 失败: %s", e)
            return seq

    def end_turn(self, outcome: str) -> Optional[dict]:
        """turn 结束：finalize（重哈希）+ git 对账 + 落盘。返回摘要。"""
        if not self._enabled:
            return None
        with self._lock:
            return self._close_locked(outcome)

    def _close_locked(self, outcome: str) -> Optional[dict]:
        ckpt = self._open
        self._open = None
        if ckpt is None:
            return None
        ckpt["outcome"] = outcome
        ckpt["ended_at"] = _now()
        self._finalize_locked(ckpt)
        self._reconcile_locked(ckpt)
        ckpt.pop("_status_start", None)
        ckpt["reconcile"]["hooked"] = len(ckpt["files"]) - ckpt["reconcile"]["git_caught"]
        self._storage.save_manifest(ckpt)
        summary = manifest_summary(ckpt)
        self._last_summary = summary
        self._emit("checkpoint.created", summary)
        return summary

    # ------------------------------------------------------------ 事件入口
    def on_file_event(self, path: str, op: str, old_bytes: Optional[bytes],
                      new_bytes: Optional[bytes], source: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._append_locked({"_kind": "file", "path": path, "op": op,
                                 "old_bytes": old_bytes, "new_bytes": new_bytes, "source": source})

    def on_memory_event(self, layer: str, op: str, atom_id: int,
                        old_row: Optional[dict], new_row: Optional[dict]) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._append_locked({"_kind": "memory", "layer": layer, "op": op,
                                 "atom_id": atom_id, "old_row": old_row, "new_row": new_row})

    def _append_locked(self, evt: dict) -> None:
        if self._open is None:
            self._pending.append(evt)
            return
        if evt["_kind"] == "file":
            fop = self._make_file_op(evt["path"], evt["op"], evt["old_bytes"], evt["new_bytes"], evt["source"])
            self._open["files"].append(fop.to_dict())
        else:
            self._open["memory"]["l1"].append(
                MemoryOp(layer=evt["layer"], op=evt["op"], atom_id=int(evt["atom_id"]),
                         old_row=evt["old_row"], new_row=evt["new_row"]).to_dict()
            )

    def _make_file_op(self, path: str, op: str, old_bytes: Optional[bytes],
                      new_bytes: Optional[bytes], source: str) -> FileOp:
        abs_path = _norm(path)
        old_ref = self._blob_ref(old_bytes)
        new_ref = self._blob_ref(new_bytes)
        oversized = (old_bytes is not None and len(old_bytes) > self._max_blob_bytes) or \
                    (new_bytes is not None and len(new_bytes) > self._max_blob_bytes)
        if oversized and self._open is not None:
            self._open["warnings"].append(f"超大文件仅记元数据，回退跳过: {abs_path}")
        return FileOp(path=abs_path, rel_path=rel_to_root(abs_path, self._repo_root),
                      op=op, source=source, old=old_ref, new=new_ref, oversized=oversized)

    def _blob_ref(self, data: Optional[bytes]) -> BlobRef:
        if data is None:
            return BlobRef(present=False)
        if len(data) > self._max_blob_bytes:
            return BlobRef(present=True, sha256=None, size=len(data))  # oversized：不存 blob
        return BlobRef(present=True, sha256=self._storage.write_blob(data), size=len(data))

    # ------------------------------------------------------- finalize / 对账
    def _finalize_locked(self, ckpt: dict) -> None:
        """对 hooked 路径重读盘重哈希：hook 后被 run_cmd 二次修改的更新 new blob。"""
        from .models import sha256_of

        seen_latest: dict[str, dict] = {}
        for f in ckpt["files"]:
            seen_latest[f["path"]] = f  # 保留每路径最后一条
        for path, f in seen_latest.items():
            if f.get("oversized"):
                continue
            try:
                cur = Path(path).read_bytes()
            except OSError:
                cur = None
            if cur is None:
                if f["new"]["present"]:
                    f["new"] = {"present": False, "sha256": None, "size": 0}
                continue
            if len(cur) > self._max_blob_bytes:
                f["new"] = {"present": True, "sha256": None, "size": len(cur)}
                f["oversized"] = True
                continue
            sha = sha256_of(cur)
            if f["new"].get("sha256") != sha:
                f["new"] = {"present": True, "sha256": self._storage.write_blob(cur), "size": len(cur)}

    def _reconcile_locked(self, ckpt: dict) -> None:
        """git 对账兜底：挂钩未捕获的变更以 source=git 入账，old 来自 base_ref。"""
        git_info = ckpt["git"]
        if not self._git or git_info.get("degraded") or not git_info.get("base_ref"):
            if self._git_enabled:
                ckpt["warnings"].append("git 降级：run_cmd/MCP 造成的文件变更无法被对账捕获")
            return
        hooked_abs = {f["path"] for f in ckpt["files"]}
        try:
            candidates = reconcile_candidates(
                hooked_abs=hooked_abs,
                git=self._git,
                base_ref=git_info["base_ref"],
                status_start=ckpt.get("_status_start") or {},
                is_excluded=self._excluded,
            )
        except Exception as e:
            logger.warning("checkpoint 对账失败: %s", e)
            ckpt["warnings"].append(f"git 对账失败: {e}")
            return
        for rel in candidates:
            abs_path = _norm(str(self._repo_root / rel))
            old_bytes = self._git.show_file(git_info["base_ref"], rel)  # None → turn 前不存在
            try:
                new_bytes: Optional[bytes] = Path(abs_path).read_bytes()
            except OSError:
                new_bytes = None
            op = "create" if old_bytes is None else ("delete" if new_bytes is None else "modify")
            fop = self._make_file_op(abs_path, op, old_bytes, new_bytes, "git")
            ckpt["files"].append(fop.to_dict())
            ckpt["reconcile"]["git_caught"] += 1

    def _excluded(self, rel: str, ab: str) -> bool:
        return self._excluder(rel, ab)

    # ---------------------------------------------------------------- 回退
    def rollback_to(self, seq: int, dry_run: bool = False) -> dict:
        """倒带到 checkpoint seq 结束时刻（撤销 seq 之后的全部 checkpoint）。

        回退自身生成 kind=rollback 的 checkpoint（含回退前状态），对其再回退即 redo。
        """
        if not self._enabled:
            return {"ok": False, "error": "checkpoint 未启用"}
        with self._lock:
            manifests = [m for m in self._storage.list_manifests() if (m.get("seq") or 0) > seq]
            if not manifests:
                return {"ok": True, "undone": 0, "message": f"checkpoint #{seq} 之后没有变更"}
            manifests.sort(key=lambda m: m["seq"], reverse=True)  # newest first
            if dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "undone": len(manifests),
                    "plan": [manifest_summary(m) for m in manifests],
                }
            self._emit("checkpoint.rollback.started", {"target": seq, "undone": len(manifests)})
            memory_restore = None
            if self._memory is not None and hasattr(self._memory, "restore_l1_atom"):
                memory_restore = self._memory.restore_l1_atom
            result = rollback_chain(
                chain=manifests,
                storage=self._storage,
                memory_restore=memory_restore,
                git_restore=self._git_restore if self._probe_git() else None,
                conflict_policy=self._conflict_policy,
            )
            if result.get("aborted"):
                return {"ok": False, "error": "存在外部修改冲突，已中止", "conflicts": result["conflicts"]}
            # 回退自身入账（redo 能力）
            rb_seq = self._storage.next_seq()
            rb_id = f"{rb_seq:04d}_rollback_to_{seq}"
            rb_manifest = {
                "version": 1,
                "seq": rb_seq,
                "checkpoint_id": rb_id,
                "kind": "rollback",
                "session_id": (self._open or {}).get("session_id"),
                "turn_count": (self._open or {}).get("turn_count"),
                "started_at": _now(),
                "ended_at": _now(),
                "outcome": "rollback",
                "rollback_of": seq,
                "git": {"enabled": self._git_enabled, "degraded": not self._probe_git(),
                        "degrade_reason": None, "base_ref": None, "ref_protected": False},
                "files": result["rollback_files"],
                "memory": {"l1": result["rollback_memory"]},
                "reconcile": {"hooked": len(result["rollback_files"]), "git_caught": 0, "excluded": 0},
                "warnings": [c["path"] + ": " + c["reason"] for c in result["conflicts"]],
                "op_results": result["op_results"],
            }
            self._storage.save_manifest(rb_manifest)
            failed = [r for r in result["op_results"] if r.get("status") != "ok"]
            summary = {
                "ok": not failed,
                "rollback_seq": rb_seq,
                "undone": len(manifests),
                "applied": len(result["op_results"]) - len(failed),
                "failed": failed,
                "conflicts": result["conflicts"],
            }
            self._last_summary = manifest_summary(rb_manifest)
            self._emit("checkpoint.rollback.completed", summary)
            logger.info("回退完成 → checkpoint #%d: applied=%d failed=%d",
                        rb_seq, summary["applied"], len(failed))
            return summary

    # ---------------------------------------------------------------- 查询
    def list(self, limit: int = 20) -> list[dict]:
        with self._lock:
            manifests = self._storage.list_manifests()
            return [manifest_summary(m) for m in manifests[-limit:]][::-1]

    def show(self, seq: int) -> Optional[dict]:
        with self._lock:
            for m in self._storage.list_manifests():
                if m.get("seq") == seq:
                    m = dict(m)
                    m.pop("_status_start", None)
                    return m
            return None

    def get_status_line(self) -> str:
        with self._lock:
            if not self._enabled:
                return ""
            s = self._last_summary
            if not s:
                return "cp: —"
            flag = " ⚠degraded" if s.get("degraded") else ""
            return f"cp#{s['seq']} · Δ{s['files']} files · {s['l1_ops']} mem{flag}"
