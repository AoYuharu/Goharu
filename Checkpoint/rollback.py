"""倒带回退算法：rollback_to(K) = 逆序应用 (K, H] 全部 checkpoint。

纯函数式核心：manifest 列表进、结果 dict 出；文件写入、SQL、git 全部经参数注入，
Checkpoint 包外零依赖，便于契约测试。

多 checkpoint 链式正确性：任取路径 p，设 i 为链中触碰 p 的最旧 checkpoint。
逆序应用到 cp_i 时写入的首条 old 捕捉于 cp_i 首个 op 之前，cp_{i-1}..cp_H 对 p
的变更已被先行撤销，故恢复结果即目标时刻状态。反例（checkpoint 后第三方写 p）
由 post_hash 一致性校验捕获。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from .models import BlobRef, FileOp, MemoryOp, OP_CREATE, collapse_file_ops, sha256_of
from .storage import CheckpointStorage, _atomic_write

logger = logging.getLogger(__name__)


def _delete_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _read_current(path: str) -> Optional[bytes]:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def rollback_chain(
    chain: list[dict],
    storage: CheckpointStorage,
    memory_restore: Optional[Callable[[Optional[dict], int], Optional[dict]]] = None,
    git_restore: Optional[Callable[[str, str, str], bool]] = None,
    conflict_policy: str = "warn",
) -> dict:
    """逆序应用 chain（newest-first）。返回 {conflicts, op_results, rollback_files, rollback_memory}。

    rollback_files / rollback_memory 是"本次回退造成的变更"（供回退自身生成 checkpoint，
    redo 无特殊代码路径）。
    """
    collapsed_per_cp: list[tuple[dict, list[FileOp]]] = [
        (cp, collapse_file_ops([FileOp.from_dict(f) for f in (cp.get("files") or [])]))
        for cp in chain
    ]

    # ---- 1. 一致性校验：当前盘内容应等于链中该路径最后一次写入的 new blob ----
    expected: dict[str, FileOp] = {}  # path -> 最新一次触碰它的 op（chain 已 newest-first）
    for _, collapsed in collapsed_per_cp:
        for op in collapsed:
            if op.path not in expected:
                expected[op.path] = op
    conflicts = []
    for path, op in expected.items():
        cur = _read_current(path)
        if op.new.present:
            if cur is None:
                conflicts.append({"path": path, "reason": "文件已被外部删除"})
            elif sha256_of(cur) != op.new.sha256:
                conflicts.append({"path": path, "reason": "checkpoint 之后有外部修改"})
        elif cur is not None:
            conflicts.append({"path": path, "reason": "文件被外部重建"})
    if conflicts and conflict_policy == "abort":
        return {"conflicts": conflicts, "op_results": [], "rollback_files": [], "rollback_memory": [], "aborted": True}

    op_results: list[dict] = []
    rollback_files: list[dict] = []
    rollback_memory: list[dict] = []

    # ---- 2. 逆序应用（预捕捉与写回同事务段：每 op 先记当前状态再改）----
    for cp, collapsed in collapsed_per_cp:
        base_ref = (cp.get("git") or {}).get("base_ref") or ""
        for op in reversed(collapsed):
            try:
                cur = _read_current(op.path)
                entry = _undo_file_op(op, cur, storage, git_restore, base_ref)
                op_results.append({"path": op.path, "status": "ok", "action": entry["op"]})
                rollback_files.append(entry)
            except Exception as e:
                logger.exception("回退失败: %s", op.path)
                op_results.append({"path": op.path, "status": "error", "error": str(e)})
        for mem in reversed((cp.get("memory") or {}).get("l1") or []):
            if memory_restore is None:
                op_results.append({"atom_id": mem.get("atom_id"), "status": "error", "error": "memory facade 未注入"})
                continue
            try:
                prev = memory_restore(mem.get("old_row"), int(mem["atom_id"]))
                op_results.append({"atom_id": mem["atom_id"], "status": "ok", "action": "restore_l1"})
                rollback_memory.append(
                    MemoryOp(layer="l1", op="restore", atom_id=int(mem["atom_id"]),
                             old_row=prev, new_row=mem.get("old_row")).to_dict()
                )
            except Exception as e:
                logger.exception("L1 回退失败: atom=%s", mem.get("atom_id"))
                op_results.append({"atom_id": mem.get("atom_id"), "status": "error", "error": str(e)})

    return {
        "conflicts": conflicts,
        "op_results": op_results,
        "rollback_files": rollback_files,
        "rollback_memory": rollback_memory,
        "aborted": False,
    }


def _undo_file_op(
    op: FileOp,
    cur: Optional[bytes],
    storage: CheckpointStorage,
    git_restore: Optional[Callable[[str, str, str], bool]],
    base_ref: str,
) -> dict:
    """撤销单个坍缩 op，返回本次变更的 FileOp dict（预捕捉的当前态为 old）。"""
    cur_ref = {"present": cur is not None, "sha256": sha256_of(cur) if cur is not None else None,
               "size": len(cur) if cur is not None else 0}
    if cur is not None:
        storage.write_blob(cur)

    if op.op == OP_CREATE:
        # 本 turn 新建 → 撤销即删除
        _delete_file(op.path)
        return FileOp(path=op.path, rel_path=op.rel_path, op="delete",
                      source="rollback",
                      old=_blobref(cur_ref), new=_blobref({"present": False})).to_dict()
    # modify / delete → 恢复旧内容
    data: Optional[bytes] = None
    if op.old.present and op.old.sha256:
        data = storage.read_blob(op.old.sha256)
    if data is None and op.old.present and git_restore and base_ref:
        # blob 缺失兜底：从该 checkpoint 的 git 基线恢复
        git_restore(base_ref, op.rel_path, op.path)
        return FileOp(path=op.path, rel_path=op.rel_path,
                      op="modify" if cur is not None else "create", source="rollback+git",
                      old=_blobref(cur_ref), new=BlobRef(present=True)).to_dict()
    if data is None:
        if op.old.present:
            raise FileNotFoundError(f"旧内容 blob 缺失且无 git 兜底: {op.path}")
        # old 不存在（理论不到这里：op!=create 时 old 必 present）
        _delete_file(op.path)
        return FileOp(path=op.path, rel_path=op.rel_path, op="delete", source="rollback",
                      old=_blobref(cur_ref), new=BlobRef(present=False)).to_dict()
    _atomic_write(Path(op.path), data)
    return FileOp(path=op.path, rel_path=op.rel_path,
                  op="modify" if cur is not None else "create", source="rollback",
                  old=_blobref(cur_ref),
                  new=BlobRef(present=True, sha256=sha256_of(data), size=len(data))).to_dict()


def _blobref(d: dict):
    from .models import BlobRef

    return BlobRef(present=d["present"], sha256=d.get("sha256"), size=d.get("size") or 0)
