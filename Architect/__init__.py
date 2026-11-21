"""Architect —— Goharu 幕后助手子系统。

主 Goharu 智能体只做对话；Architect 在背后辅佐：遥测聚合（TelemetryDigest）、
唤醒路由（WakeRequest/WakeSource 接口）、代码更新审计（AuditPipeline）。
接入见 README.md，维护规范见 CLAUDE.md。
"""

from Architect.hub import ArchitectHub, create_architect_hub, get_default_hub
from Architect.telemetry import TelemetryDigest
from Architect.wake import WakeRequest, WakeSource
from Architect.audit import AuditPipeline

__all__ = [
    "ArchitectHub", "create_architect_hub", "get_default_hub",
    "TelemetryDigest", "WakeRequest", "WakeSource", "AuditPipeline",
]
