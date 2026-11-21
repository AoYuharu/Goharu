"""Checkpoint 审计/回退工具（ListCheckpoints / ShowCheckpoint / RollbackCheckpoint）。

句柄经 set_checkpoint_handle() 由宿主（Gateway）在装配期注入 Checkpoint facade
（duck-typed：list()/show()/rollback_to()）；本模块不 import Checkpoint。
未注入时工具返回明确的降级错误（透明化原则）。
"""

import json
import logging
import threading

from Tools.registry import registry

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_handle = None  # CheckpointManager（duck-typed）


def set_checkpoint_handle(handle) -> None:
    global _handle
    with _lock:
        _handle = handle


def _get_handle():
    with _lock:
        return _handle


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _no_handle() -> str:
    return _json({
        "ok": False,
        "error": "checkpoint 未启用或未接线（config.yaml checkpoint.enabled）",
    })


async def ListCheckpoints(limit: int = 20) -> str:
    """列出最近的 checkpoint（新→旧）。每个 checkpoint 对应一轮对话的文件/记忆变更。"""
    h = _get_handle()
    if h is None:
        return _no_handle()
    try:
        limit = max(1, min(int(limit), 100))
        return _json({"ok": True, "checkpoints": h.list(limit)})
    except Exception as e:
        logger.exception("ListCheckpoints failed")
        return _json({"ok": False, "error": str(e)})


async def ShowCheckpoint(seq: int) -> str:
    """查看某个 checkpoint 的明细：文件 op 表（路径/操作/来源）+ 记忆 op 表 + 警告。"""
    h = _get_handle()
    if h is None:
        return _no_handle()
    try:
        m = h.show(int(seq))
    except Exception as e:
        logger.exception("ShowCheckpoint failed")
        return _json({"ok": False, "error": str(e)})
    if m is None:
        return _json({"ok": False, "error": f"checkpoint #{seq} 不存在"})
    return _json({
        "ok": True,
        "seq": m.get("seq"),
        "checkpoint_id": m.get("checkpoint_id"),
        "kind": m.get("kind"),
        "outcome": m.get("outcome"),
        "ended_at": m.get("ended_at"),
        "files": [
            {"path": f["path"], "op": f["op"], "source": f["source"],
             "oversized": f.get("oversized", False)}
            for f in (m.get("files") or [])
        ],
        "memory_l1": [
            {"op": op["op"], "atom_id": op["atom_id"],
             "old_text": (op.get("old_row") or {}).get("canonical_text"),
             "new_text": (op.get("new_row") or {}).get("canonical_text")}
            for op in ((m.get("memory") or {}).get("l1") or [])
        ],
        "warnings": m.get("warnings") or [],
    })


async def RollbackCheckpoint(seq: int, confirm: bool = False) -> str:
    """倒带到 checkpoint seq 结束时刻的状态（撤销 seq 之后的全部 checkpoint）。

    注意语义：撤销的是 seq **之后**的变更；要撤销 seq 自身的变更，回退到 seq-1。
    confirm=false 时只返回 dry-run 计划（将撤销哪些 checkpoint），不实际执行；
    确认后必须以 confirm=true 再次调用。回退自身会生成新 checkpoint（可 redo）。
    """
    h = _get_handle()
    if h is None:
        return _no_handle()
    try:
        result = h.rollback_to(int(seq), dry_run=not confirm)
    except Exception as e:
        logger.exception("RollbackCheckpoint failed")
        return _json({"ok": False, "error": str(e)})
    if result.get("dry_run"):
        result["hint"] = "确认无误后用 confirm=true 再调一次以实际执行"
    return _json(result)


registry.register(
    name="ListCheckpoints",
    description=(
        "List recent checkpoints (newest first). Each checkpoint records one turn's "
        "file and memory changes with old contents. Use to audit what the agent changed."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max entries (default 20, cap 100)."},
        },
    },
    handler=ListCheckpoints,
    group="checkpoint",
)

registry.register(
    name="ShowCheckpoint",
    description=(
        "Show one checkpoint's details: file ops (path/op/source), memory atom ops, warnings."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "seq": {"type": "integer", "description": "Checkpoint seq number."},
        },
        "required": ["seq"],
    },
    handler=ShowCheckpoint,
    group="checkpoint",
)

registry.register(
    name="RollbackCheckpoint",
    description=(
        "Rewind to the state at the end of checkpoint <seq>, undoing ALL checkpoints "
        "after it. To undo checkpoint N's own changes, roll back to N-1. "
        "Call with confirm=false first to get a dry-run plan, then confirm=true to execute. "
        "The rollback itself is recorded as a new checkpoint (redo-able)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "seq": {"type": "integer", "description": "Target checkpoint seq to rewind to."},
            "confirm": {
                "type": "boolean",
                "description": "false=dry-run plan only; true=execute rollback.",
            },
        },
        "required": ["seq"],
    },
    handler=RollbackCheckpoint,
    group="checkpoint",
)
