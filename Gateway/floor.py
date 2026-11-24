"""网络话语权（Floor）值对象。

话语权 = 网络内唯一的状态：(持有账号 agent_id, 人格 persona∈{main,arch})。
hub 是权威；各端由 AgentNetChannel 的 floor_state 事件同步一份本地快照，
Gateway 的入队关卡（AgentSession）与 FloorControl 工具读它做路由决策。

空快照（holder_agent_id == ""）= 无话语权 = 功能关闭（如未接 agentnet）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FloorSnapshot:
    holder_agent_id: str = ""
    holder_name: str = ""
    persona: str = "main"          # main | arch
    since: float = 0.0
    note: str = ""
    epoch: int = 0                 # 每次变化 +1（持有期计数重置的依据）

    @property
    def held(self) -> bool:
        return bool(self.holder_agent_id)

    def held_by(self, agent_id: str, persona: str | None = None) -> bool:
        return (self.holder_agent_id == agent_id
                and (persona is None or self.persona == persona))


@dataclass
class FloorState:
    """线程安全的本地快照容器（channel 事件线程写、enqueue/工具线程读）。"""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _snap: FloorSnapshot = field(default_factory=FloorSnapshot, repr=False)

    def snapshot(self) -> FloorSnapshot:
        with self._lock:
            return self._snap

    def update(self, floor: dict | None) -> FloorSnapshot:
        """应用 hub 下发的 floor 载荷（None/空 dict = 无话语权）。

        载荷形如 {holder_agent_id, holder_name, persona, since, note}；
        epoch 在本地自增（hub 载荷不必带），用于持有期计数重置。
        """
        f = dict(floor or {})
        with self._lock:
            epoch = self._snap.epoch
            if f.get("holder_agent_id"):
                epoch += 1
            elif self._snap.held:
                epoch += 1
            self._snap = FloorSnapshot(
                holder_agent_id=str(f.get("holder_agent_id", "") or ""),
                holder_name=str(f.get("holder_name", "") or ""),
                persona=str(f.get("persona", "main") or "main"),
                since=float(f.get("since") or (time.time() if f.get("holder_agent_id") else 0.0)),
                note=str(f.get("note", "") or ""),
                epoch=epoch,
            )
            return self._snap
