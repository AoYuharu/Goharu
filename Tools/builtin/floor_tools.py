"""网络话语权（Floor）工具组 —— 每个动作一个独立工具。

心智模型：组网账号背后坐着"人"。话语权 = 当前哪个账号的哪个人格
（main 主智能体 / arch 架构师）在跟主人对话。持有期间绝对优先：
全网只有持有者响应主人消息，其他人静默旁观。

拆分为 3 个工具：
- floor_handoff：把话语权转交给目标（target=智能体名/id；"arch" 简写 = 宿主 arch）
- floor_release：收回话语权（恢复正常发言判断）
- floor_status ：查询当前持有者

句柄由 SessionManager 装配期注入（set_floor_handle）——
对齐 agentnet_tools.set_agentnet_handle 的解耦模式。
"""

from __future__ import annotations

import asyncio
import json
import logging

from Tools.registry import registry

logger = logging.getLogger(__name__)

_handle = None


def set_floor_handle(ch) -> None:
    """SessionManager 注入 agentnet channel 句柄（提供 net_set_floor_fut）。"""
    global _handle
    _handle = ch
    logger.info("Floor 工具句柄已注入: %s", getattr(ch, "channel_id", ch))


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


_NOT_WIRED = {"ok": False, "error": "话语权未接线（agentnet 未启用或未挂载）"}
_NOT_CONNECTED = {"ok": False, "error": "agentnet 未连接"}


async def floor_handoff(target: str = "", persona: str = "main",
                        note: str = "") -> str:
    """把话语权转交给目标（persona=arch 时 target 可直接写 'arch'）。"""
    h = _handle
    if h is None:
        return _json(_NOT_WIRED)
    target = (target or "").strip()
    if not target:
        return _json({"ok": False, "error": "handoff 需要 target（智能体名/id）"})
    persona = "arch" if str(persona).lower() == "arch" else "main"
    if persona == "arch":
        target = "arch"   # hub 解析到 hosts_architect 的宿主账号
    fut = h.net_set_floor_fut("set", target=target, persona=persona, note=note)
    if fut is None:
        return _json(_NOT_CONNECTED)
    await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
    logger.info("floor_handoff: 话语权 → %s（%s）", target, persona)
    return _json({"ok": True, "handoff": target, "persona": persona,
                  "note": "已广播全网；持有期间只有对方响应主人的消息"})


async def floor_release() -> str:
    """收回话语权（恢复正常发言判断）。"""
    h = _handle
    if h is None:
        return _json(_NOT_WIRED)
    fut = h.net_set_floor_fut("clear")
    if fut is None:
        return _json(_NOT_CONNECTED)
    await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
    logger.info("floor_release: 话语权已收回")
    return _json({"ok": True, "released": True,
                  "note": "全网广播已发出，恢复正常发言判断"})


async def floor_status() -> str:
    """查询当前话语权持有者。"""
    h = _handle
    if h is None:
        return _json(_NOT_WIRED)
    snap = h.floor.snapshot()
    if not snap.held:
        return _json({"ok": True, "floor": None})
    return _json({"ok": True, "floor": {
        "holder_agent_id": snap.holder_agent_id,
        "holder_name": snap.holder_name,
        "persona": snap.persona,
        "since": snap.since,
    }})


# ---------------------------------------------------------------- 工具注册

_FLOOR_BG = ("HaruNet speaking-right (floor). While a floor is held, ONLY the holder "
             "responds to the owner's messages — everyone else stays silent (absolute "
             "priority, even over @mentions). ")

registry.register(
    name="floor_handoff",
    description=(
        _FLOOR_BG +
        "Hand the floor to another agent/persona so the owner talks to it directly. "
        "target=<agent name or id>, or 'arch' with persona='arch' to let the architect "
        "persona of the arch host take over (it answers in the chat)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string",
                       "description": "目标智能体名或 agent_id；persona=arch 时"
                                      "可直接写 'arch'"},
            "persona": {"type": "string", "enum": ["main", "arch"],
                        "description": "main=主智能体人格（默认），arch=架构师人格"},
            "note": {"type": "string", "description": "可选附注（记入运维流水）"},
        },
        "required": ["target"],
    },
    handler=floor_handoff,
    group="agentnet",
)

registry.register(
    name="floor_release",
    description=(
        _FLOOR_BG +
        "Take the floor back / end the handoff — normal reply dynamics resume for "
        "everyone. If you hold the floor as the architect persona and are no longer "
        "needed, call this to give it back."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=floor_release,
    group="agentnet",
)

registry.register(
    name="floor_status",
    description=(
        _FLOOR_BG +
        "Query who currently holds the floor (holder name/id, persona, since)."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=floor_status,
    group="agentnet",
)
