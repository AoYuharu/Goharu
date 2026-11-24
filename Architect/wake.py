"""唤醒接口层 —— WakeSource 协议与 WakeRequest。

设计决议（2026-11-21）：唤醒条件（定时/空闲/健康判据）**暂留接口不实现**。
当前可用入口只有 push 式 `ArchitectHub.request_wake()`（人工指令、git 钩子、
进程外哨兵都走它）。拉式源（定时器、空闲检测、HealthMonitor）未来实现
`WakeSource.poll()` 并 `hub.register_wake_source(...)` 即可生效，hub 无需改动。

优先级语义：P0 立即（致命故障/人工）、P1 尽快（软故障/代码更新）、
P2 低峰（定时自省）、P3 空闲（探索）。排序与抢占由未来的调度器负责，
当前 hub 只做冷却过滤 + 顺序派发。
"""

from dataclasses import dataclass, field
from typing import Protocol

# reason 字符串即冷却配置的键（config architect_hub.wake.cooldown_seconds）
REASON_FATAL = "fatal"               # 哨兵：心跳丢失且重启无效
REASON_SOFT_FAILURE = "soft_failure" # HealthMonitor：重试风暴/连续错误/空转
REASON_CODE_UPDATE = "code_update"   # git push 钩子：staging 有待审计更新
REASON_SCHEDULED = "scheduled"       # 定时自省（未实现）
REASON_IDLE = "idle"                 # 空闲探索（未实现）
REASON_MANUAL = "manual"             # 人工点名

# kind 决定任务模板：守护 / 审计 / 自省 / 探索 / 话语权对话
KIND_GUARDIAN = "guardian"
KIND_AUDIT = "audit"
KIND_INTROSPECT = "introspect"
KIND_EXPLORE = "explore"
KIND_FLOOR_CHAT = "floor_chat"       # 话语权=arch：主人直接对话


@dataclass
class WakeRequest:
    reason: str
    kind: str
    context: dict = field(default_factory=dict)
    priority: int = 2                 # 0=P0 最高 … 3=P3 最低


class WakeSource(Protocol):
    """拉式唤醒源协议（接口预留，当前无实现）。"""

    name: str

    def poll(self) -> "WakeRequest | None":
        """被调度器周期调用；有唤醒需求返回 WakeRequest，否则 None。"""
        ...
