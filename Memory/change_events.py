"""记忆写路径审计缝（listener 单例模式，对齐 Tools/file_change_hook）。

Memory 不 import Checkpoint：L1 仓储与场景/persona 文件写在变更后调
emit_*，监听者由宿主（Gateway）在装配期注入。默认 no-op。

线程模型：L1 写发生在 PipelineManager daemon 线程、L2 在 Timer 线程——
emit 路径无锁读 + listener 自管锁（CheckpointManager 入口持 RLock）。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_l1_listener: Optional[Callable[[str, int, Optional[dict], Optional[dict]], None]] = None
_file_listener: Optional[Callable[[str, str, Optional[bytes], Optional[bytes], str], None]] = None


def set_l1_listener(fn: Optional[Callable[[str, int, Optional[dict], Optional[dict]], None]]) -> None:
    """fn(op, atom_id, old_row, new_row)，op ∈ {insert, update, deactivate}。"""
    global _l1_listener
    with _lock:
        _l1_listener = fn


def set_memory_file_listener(
    fn: Optional[Callable[[str, str, Optional[bytes], Optional[bytes], str], None]]
) -> None:
    """fn(path, op, old_bytes, new_bytes, source)；scene_blocks/persona 文件写用。"""
    global _file_listener
    with _lock:
        _file_listener = fn


def has_l1_listener() -> bool:
    with _lock:
        return _l1_listener is not None


def emit_l1_change(op: str, atom_id: int, old_row: Optional[dict], new_row: Optional[dict]) -> None:
    with _lock:
        fn = _l1_listener
    if fn is None:
        return
    try:
        fn(op, atom_id, old_row, new_row)
    except Exception:  # noqa: BLE001 - 审计通道失败不能弄坏记忆管线
        logger.warning("l1 change listener 异常", exc_info=True)


def emit_memory_file_change(
    path: str, op: str, old_bytes: Optional[bytes], new_bytes: Optional[bytes], source: str
) -> None:
    with _lock:
        fn = _file_listener
    if fn is None:
        return
    try:
        fn(path, op, old_bytes, new_bytes, source)
    except Exception:  # noqa: BLE001
        logger.warning("memory file listener 异常", exc_info=True)
