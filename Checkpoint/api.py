"""Checkpoint 子系统门面。

用法（宿主装配，唯一入口）::

    from Checkpoint.api import create_checkpoint_manager
    ckpt = create_checkpoint_manager(config=global_config, memory=memory_bridge,
                                     event_sink=session.broadcast)
    ckpt.begin_turn(session_id, turn_count)   # turn 开始
    ...                                        # Tools/Memory 事件经 listener 缝流入
    ckpt.end_turn(outcome)                     # turn 结束（含异常路径，放 finally）

边界：Checkpoint 永不 import Tools/Memory；记忆回退只经注入 memory 对象的
restore_l1_atom 方法；事件出口只有注入的 event_sink。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .manager import CheckpointManager

__all__ = ["create_checkpoint_manager", "CheckpointManager"]


def create_checkpoint_manager(
    config: Any = None,
    memory: Any = None,
    repo_root: Optional[str] = None,
    event_sink: Optional[Callable[[str, dict], None]] = None,
) -> CheckpointManager:
    """创建 CheckpointManager。

    - config: dict（扁平点号键或嵌套）或带 .get(dotted, default) 的配置对象
    - memory: duck-typed，需提供 restore_l1_atom(old_row, atom_id) -> dict|None
    - repo_root: git 基线仓库根，默认 cwd
    - event_sink: 事件出口（checkpoint.created / rollback.* / degraded），可选
    """
    return CheckpointManager(config=config, memory=memory, repo_root=repo_root, event_sink=event_sink)
