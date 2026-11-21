"""文件变更事件缝（listener 单例模式，克隆 touched_files）。

Tools 不 import Checkpoint：写文件工具在落盘后调 emit_file_change()，
是否有监听者、监听者是谁完全由宿主（Gateway）在装配期经
`Tools.api.create_tools(on_file_change=...)` 注入。默认 no-op。

线程安全：emit 可能被 handler 线程池、SubAgent 线程并发调用。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

_lock = threading.Lock()
_listener: Optional[Callable[[str, str, Optional[bytes], Optional[bytes], str], None]] = None


def set_file_change_listener(
    listener: Optional[Callable[[str, str, Optional[bytes], Optional[bytes], str], None]]
) -> None:
    """装配期注入。签名为 listener(path, op, old_bytes, new_bytes, source)。"""
    global _listener
    with _lock:
        _listener = listener


def emit_file_change(
    path: str,
    op: str,
    old_bytes: Optional[bytes],
    new_bytes: Optional[bytes],
    source: str,
) -> None:
    """写盘成功后调用。listener 异常不得打断工具主流程。"""
    with _lock:
        listener = _listener
    if listener is None:
        return
    try:
        listener(path, op, old_bytes, new_bytes, source)
    except Exception:  # noqa: BLE001 - 审计通道失败不能弄坏写工具
        import logging

        logging.getLogger(__name__).warning("file_change listener 异常", exc_info=True)
