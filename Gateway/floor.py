"""网络话语权（Floor）值对象。

话语权 = 网络内唯一的状态：(持有账号 agent_id, 人格 persona∈{main,arch})。
hub 是权威；各端由 AgentNetChannel 的 floor_state 事件同步一份本地快照，
Gateway 的入队关卡（AgentSession）与 floor_* 工具读它做路由决策。

空快照（holder_agent_id == ""）= 无话语权 = 功能关闭（如未接入 HaruNet）。
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

        相同语义快照幂等：presence 周期广播不得反复重置持有期 epoch。
        仅首次持有、语义字段变化或清除时 epoch +1；缺少 since 的相同快照沿用旧值。
        """
        f = dict(floor or {})
        with self._lock:
            old = self._snap
            since_raw = f.get("since")
            if since_raw:
                since = float(since_raw)
            elif f.get("holder_agent_id") and f.get("holder_agent_id") == old.holder_agent_id:
                since = old.since
            else:
                since = time.time() if f.get("holder_agent_id") else 0.0
            candidate = (
                str(f.get("holder_agent_id", "") or ""),
                str(f.get("holder_name", "") or ""),
                str(f.get("persona", "main") or "main"),
                since,
                str(f.get("note", "") or ""),
            )
            previous = (old.holder_agent_id, old.holder_name, old.persona,
                        old.since, old.note)
            if candidate == previous:
                return old
            self._snap = FloorSnapshot(
                holder_agent_id=candidate[0], holder_name=candidate[1],
                persona=candidate[2], since=candidate[3], note=candidate[4],
                epoch=old.epoch + 1,
            )
            return self._snap
