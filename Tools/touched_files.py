"""记录主 Agent 本轮（turn）通过 Write/Edit 修改过的项目文件路径。

用于把 Verify 子 agent 的预填充 git diff 收敛到「主 Agent 本轮实际改动的文件」，
避免在大量未提交改动的脏仓库里，把无关的历史改动一起塞进 Verify 上下文。

主 Agent 侧的 Write/Edit 工具在成功落盘后调用 record()；
turn 开始时由 Gateway 调用 reset() 清空。
"""

import threading
from pathlib import Path

_lock = threading.Lock()
_touched: set[str] = set()


def record(path: str) -> None:
    """记录一个被主 Agent 修改的项目文件绝对路径。"""
    with _lock:
        _touched.add(str(Path(path).expanduser().resolve()))


def get_touched_files() -> list[str]:
    """返回本轮被修改的项目文件绝对路径列表（有序）。"""
    with _lock:
        return sorted(_touched)


def reset() -> None:
    """清空记录（turn 开始时调用）。"""
    with _lock:
        _touched.clear()
