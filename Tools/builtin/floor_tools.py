"""网络话语权（Floor）工具 —— 会话权转交/收回。

心智模型：组网账号背后坐着"人"。话语权 = 当前哪个账号的哪个人格
（main 主智能体 / arch 架构师）在跟主人对话。持有期间绝对优先：
全网只有持有者响应主人消息，其他人静默旁观。

动作：
- handoff：把话语权转交给目标（target=智能体名/id；"arch" 简写 = 宿主 arch）
- release：收回话语权（恢复正常发言判断）
- status：查询当前持有者

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
    logger.info("FloorControl 句柄已注入: %s", getattr(ch, "channel_id", ch))


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


async def floor_control(action: str, target: str = "", persona: str = "main",
                        note: str = "") -> str:
    """网络话语权：转交 / 收回 / 查询。"""
    h = _handle
    if h is None:
        return _json({"ok": False,
                      "error": "话语权未接线（agentnet 未启用或未挂载）"})
    if action == "status":
        snap = h.floor.snapshot()
        if not snap.held:
            return _json({"ok": True, "floor": None})
        return _json({"ok": True, "floor": {
            "holder_agent_id": snap.holder_agent_id,
            "holder_name": snap.holder_name,
            "persona": snap.persona,
            "since": snap.since,
        }})
    if action == "release":
        fut = h.net_set_floor_fut("clear")
        if fut is None:
            return _json({"ok": False, "error": "agentnet 未连接"})
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
        logger.info("FloorControl: 话语权已收回")
        return _json({"ok": True, "released": True,
                      "note": "全网广播已发出，恢复正常发言判断"})
    if action == "handoff":
        target = (target or "").strip()
        if not target:
            return _json({"ok": False, "error": "handoff 需要 target（智能体名/id）"})
        persona = "arch" if str(persona).lower() == "arch" else "main"
        if persona == "arch":
            target = "arch"   # hub 解析到 hosts_architect 的宿主账号
        fut = h.net_set_floor_fut("set", target=target, persona=persona, note=note)
        if fut is None:
            return _json({"ok": False, "error": "agentnet 未连接"})
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
        logger.info("FloorControl: 话语权 → %s（%s）", target, persona)
        return _json({"ok": True, "handoff": target, "persona": persona,
                      "note": "已广播全网；持有期间只有对方响应主人的消息"})
    return _json({"ok": False,
                  "error": f"未知 action: {action}（handoff/release/status）"})


registry.register(
    name="FloorControl",
    description=(
        "HaruNet speaking-right (floor) handoff. In this agent network every account "
        "may be operated by different personas (main agent / architect). While a floor "
        "is held, ONLY the holder responds to the owner's messages — everyone else "
        "stays silent (absolute priority, even over @mentions). Use it when the owner "
        "wants to talk to a specific agent/persona directly, or to hand the "
        "conversation over and take it back. Actions: "
        "'handoff' (target=<agent name/id> or 'arch' shorthand, persona='main'|'arch'), "
        "'release' (take the floor back / end the handoff), "
        "'status' (who currently holds the floor)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["handoff", "release", "status"]},
            "target": {"type": "string",
                       "description": "handoff: 目标智能体名或 agent_id；persona=arch "
                                      "时可直接写 'arch'"},
            "persona": {"type": "string", "enum": ["main", "arch"],
                        "description": "handoff: main=主智能体人格（默认），arch=架构师人格"},
            "note": {"type": "string", "description": "可选附注（记入运维流水）"},
        },
        "required": ["action"],
    },
    handler=floor_control,
    group="agentnet",
)
