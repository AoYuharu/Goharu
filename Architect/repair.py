"""Architect 自动修复的护栏：工作区快照 → 自检 → 失败精确回滚。

设计要点：
- 快照必须覆盖"修复前工作区"，包括用户已修改的 tracked 文件与 untracked 文件。
  `git stash create` 只覆盖 tracked；因此另用临时 GIT_INDEX_FILE 执行
  read-tree/add -A/write-tree/commit-tree，生成含 untracked 的完整树 commit。
- 失败恢复用 `git read-tree --reset -u <full_ref>` 回写工作区，再删除修复期间
  新增、快照中不存在的 untracked 文件，最后 `git reset --mixed HEAD` 还原索引。
- 不碰用户真实 index；临时 index 与临时对象引用总在 finally 清理（失败时保留
  ref 供人工恢复）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RepairGuard:
    def __init__(self, repo_root: str | Path, self_check_commands: Optional[list] = None,
                 timeout: int = 600, max_snapshot_mb: int = 200,
                 python: str | None = None):
        self.repo = Path(repo_root).resolve()
        self.self_check_commands = list(self_check_commands or [])
        self.timeout = int(timeout or 600)
        self.max_snapshot_bytes = int(max_snapshot_mb or 200) * 1024 * 1024
        self.python = python or sys.executable
        self._snapshot: Optional[dict] = None

    # ---------------------------------------------------------------- git
    def _run(self, args: list[str], *, env: dict | None = None,
             check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(self.repo), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
            env=env, check=check,
        )

    def _is_git_repo(self) -> bool:
        try:
            r = self._run(["rev-parse", "--is-inside-work-tree"])
            return r.returncode == 0 and r.stdout.strip() == "true"
        except Exception:
            return False

    @staticmethod
    def _status_paths(z: str) -> set[str]:
        """解析 `git status --porcelain -z` 路径集合（rename 取新路径）。"""
        paths: set[str] = set()
        parts = z.split("\0")
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if not entry or len(entry) < 4:
                continue
            xy, path = entry[:2], entry[3:]
            if "R" in xy or "C" in xy:
                # rename/copy: 下一个字段是新路径
                if i < len(parts) and parts[i]:
                    path = parts[i]
                    i += 1
            if path:
                paths.add(path.replace("\\", "/"))
        return paths

    def _status(self) -> tuple[set[str], str]:
        r = self._run(["status", "--porcelain", "-z", "-uall"])
        z = r.stdout or ""
        return self._status_paths(z), z

    def _untracked(self) -> list[str]:
        r = self._run(["ls-files", "--others", "--exclude-standard", "-z"])
        return [p for p in (r.stdout or "").split("\0") if p]

    def _full_tree_ref(self, head: str) -> tuple[str, set[str]]:
        """临时 index：HEAD + 全工作区（含 untracked）→ 完整树 commit ref。"""
        tmpdir = Path(tempfile.mkdtemp(prefix="goharu_repair_index_"))
        index_path = tmpdir / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index_path)
        env.setdefault("GIT_AUTHOR_NAME", "goharu-repair")
        env.setdefault("GIT_AUTHOR_EMAIL", "goharu-repair@local")
        env.setdefault("GIT_COMMITTER_NAME", "goharu-repair")
        env.setdefault("GIT_COMMITTER_EMAIL", "goharu-repair@local")
        before, _ = self._status()
        try:
            for args in (["read-tree", head], ["add", "-A"], ["write-tree"]):
                r = self._run(args, env=env)
                if r.returncode != 0:
                    raise RuntimeError(f"git {args[0]} 失败: {r.stderr.strip()[:300]}")
            tree = r.stdout.strip()
            msg = f"goharu repair snapshot {uuid.uuid4().hex[:8]}"
            r = self._run(["commit-tree", tree, "-p", head, "-m", msg], env=env)
            if r.returncode != 0:
                raise RuntimeError(f"git commit-tree 失败: {r.stderr.strip()[:300]}")
            full_ref = r.stdout.strip()
            ref = f"refs/goharu/repair/{int(time.time())}_{uuid.uuid4().hex[:6]}"
            u = self._run(["update-ref", ref, full_ref])
            if u.returncode != 0:
                raise RuntimeError(f"git update-ref 失败: {u.stderr.strip()[:300]}")
            return ref, before
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def snapshot(self) -> dict:
        if not self._is_git_repo():
            return {"ok": False, "error": "not a git work tree"}
        try:
            head = self._run(["rev-parse", "HEAD"]).stdout.strip()
            if not head:
                return {"ok": False, "error": "git HEAD 不存在"}
            untracked = self._untracked()
            size = 0
            for p in untracked:
                try:
                    size += (self.repo / p).stat().st_size
                except OSError:
                    pass
            if size > self.max_snapshot_bytes:
                return {"ok": False,
                        "error": f"untracked 快照过大（{size} bytes > "
                                 f"{self.max_snapshot_bytes}）"}
            tracked_ref = self._run(["stash", "create"]).stdout.strip() or head
            full_ref, before = self._full_tree_ref(head)
            self._snapshot = {
                "ok": True, "head": head, "tracked_ref": tracked_ref,
                "full_ref": full_ref, "before": before,
            }
            logger.info("RepairGuard 快照完成: %s（%d 个既有变更）",
                        full_ref, len(before))
            return dict(self._snapshot)
        except Exception as e:
            logger.exception("RepairGuard 快照失败")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---------------------------------------------------------------- 验证
    @staticmethod
    def _ignored_change(path: str) -> bool:
        return "__pycache__" in path or path.endswith((".pyc", ".pyo"))

    def changed_files(self) -> list[str]:
        """相对快照的变化：已跟踪文件差异 + 状态集合差（新增/删除/未跟踪）。"""
        if not self._snapshot:
            return []
        diff = self._run(["diff", "--name-only",
                          str(self._snapshot.get("full_ref") or "")]).stdout
        tracked = {p.strip().replace("\\", "/") for p in diff.splitlines() if p.strip()}
        after, _ = self._status()
        before = set(self._snapshot.get("before") or [])
        changed = tracked | (after ^ before)
        return sorted(p for p in changed if p and not self._ignored_change(p))

    def verify(self) -> dict:
        """改动 .py 全量 py_compile + config.yaml 解析 + 配置的自检命令。"""
        changed = self.changed_files()
        errors: list[str] = []
        py_files = [p for p in changed
                    if p.endswith(".py") and (self.repo / p).is_file()]
        if py_files:
            r = subprocess.run(
                [self.python, "-m", "py_compile", *py_files],
                cwd=str(self.repo), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout)
            if r.returncode != 0:
                errors.append("py_compile: " + (r.stderr or r.stdout).strip()[:2000])
        if "config.yaml" in changed:
            try:
                import yaml

                yaml.safe_load((self.repo / "config.yaml").read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"config.yaml 解析失败: {type(e).__name__}: {e}")
        command_results = []
        for command in self.self_check_commands:
            raw = str(command or "").strip()
            if not raw:
                continue
            try:
                r = subprocess.run(raw, shell=True, cwd=str(self.repo),
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=self.timeout)
                ok = r.returncode == 0
                command_results.append({"command": raw, "ok": ok,
                                        "output": (r.stdout or r.stderr or "")[-2000:]})
                if not ok:
                    errors.append(f"自检失败: {raw}")
            except Exception as e:
                command_results.append({"command": raw, "ok": False,
                                        "output": f"{type(e).__name__}: {e}"})
                errors.append(f"自检异常: {raw}: {e}")
        return {"ok": not errors, "changed": changed,
                "commands": command_results, "error": "; ".join(errors)[:4000]}

    # ---------------------------------------------------------------- 恢复
    def restore(self, snapshot: dict | None = None) -> dict:
        snap = snapshot or self._snapshot
        if not snap or not snap.get("ok"):
            return {"ok": False, "error": "无可用快照"}
        full_ref = snap.get("full_ref")
        before = set(snap.get("before") or [])
        try:
            self._run(["read-tree", "--reset", "-u", full_ref], check=True)
            # 删除修复期间新增、快照中不存在的 untracked 文件
            after, _ = self._status()
            for p in sorted(after - before):
                path = self.repo / p
                try:
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    logger.warning("回滚时无法删除新增路径: %s", p)
            self._run(["reset", "--mixed", "HEAD"])
            final, _ = self._status()
            ok = final == before
            if ok:
                try:
                    self._run(["update-ref", "-d", full_ref])
                except Exception:
                    pass
            logger.warning("RepairGuard 回滚%s（ref=%s）", "成功" if ok else "不完整", full_ref)
            return {"ok": ok, "full_ref": full_ref,
                    "remaining": sorted(final - before) if not ok else []}
        except Exception as e:
            logger.exception("RepairGuard 回滚失败（保留 ref=%s）", full_ref)
            return {"ok": False, "full_ref": full_ref,
                    "error": f"{type(e).__name__}: {e}",
                    "hint": f"git read-tree --reset -u {full_ref}"}

    def finish(self, snapshot: dict | None = None) -> None:
        snap = snapshot or self._snapshot
        if snap and snap.get("full_ref"):
            try:
                self._run(["update-ref", "-d", snap["full_ref"]])
            except Exception:
                pass
