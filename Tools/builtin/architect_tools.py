"""ConsultArchitect —— 主智能体向 Architect 幕后助手发起请求/提问（唤醒触发器 ④）。

主智能体察觉到自身异常、需要代码级维护、或收到用户的升级指令时调用。
自动附带运行遥测摘要（TelemetryDigest），Architect 无需现场翻日志。

layering 说明：Architect 是产品层模块，此处为函数内惰性 import + 优雅降级
（宿主未带 Architect 时返回 error JSON，不抛异常穿透）。
"""

import json
import logging
import time
from pathlib import Path

from Tools.registry import registry
from Tools.config_proxy import get_config, write_config

logger = logging.getLogger(__name__)

_VALID_KINDS = ("guardian", "audit", "introspect", "explore")
_RESTART_MARKER = (Path(__file__).resolve().parent.parent.parent
                   / "runtime_memory" / "architect" / "restart_resume.json")


def _coerce_scalar(value):
    """与 Gateway agent.update_config 一致的标量类型转换。"""
    if not isinstance(value, str):
        return value
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return float(value) if "." in value else int(value)
    except (ValueError, TypeError):
        return value


async def ConsultArchitect(request: str, kind: str = "introspect",
                           wait: bool = False) -> str:
    """向 Architect 幕后助手发起请求。

    Args:
        request: 请求/问题描述（自动附带运行遥测摘要）
        kind: guardian(故障诊断) / audit(代码审计) / introspect(自省复盘) / explore(探索改进)
        wait: True=阻塞等 Architect 完成并返回报告（提问用）；
              False=后台派发立即返回（维护任务用）
    """
    if kind not in _VALID_KINDS:
        return json.dumps({"error": f"无效 kind: {kind}",
                           "valid": list(_VALID_KINDS)}, ensure_ascii=False)
    try:
        from Architect.hub import get_default_hub
        from Architect.wake import REASON_MANUAL, WakeRequest
    except ImportError:
        return json.dumps(
            {"error": "Architect 子系统不可用（宿主未装配 Architect 模块）"},
            ensure_ascii=False)

    logger.info("ConsultArchitect: kind=%s wait=%s request=%s",
                kind, wait, str(request)[:100])
    req = WakeRequest(reason=REASON_MANUAL, kind=kind,
                      context={"source": "main_actor", "question": request})
    result = get_default_hub().request_wake(req, background=not wait)

    if result.get("status") == "dispatched" and not wait:
        return json.dumps({
            "status": "dispatched",
            "note": "Architect 已在后台开工。完成后经验自动沉淀进记忆塔；"
                    "需要结果请用 wait=true 重新发起。",
        }, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)


async def MessageHero(message: str, to: str = "hero") -> str:
    """Architect 向外发消息（它是 Architect 白名单专属工具）。

    Args:
        message: 消息内容
        to: "hero"=递到主智能体会话队列（它下一轮看到）；
            "user"=直接推送给用户（进度/报告）；"both"=两边都发
    """
    try:
        from Architect.hub import get_default_hub
    except ImportError:
        return json.dumps(
            {"error": "Architect 子系统不可用（宿主未装配 Architect 模块）"},
            ensure_ascii=False)

    hub = get_default_hub()
    sent = {}
    if to in ("hero", "both"):
        sent["hero"] = hub.message_hero(f"[Architect] {message}")
    if to in ("user", "both"):
        sent["user"] = hub.notify_user(f"[Architect] {message}")
    logger.info("MessageHero: to=%s sent=%s", to, sent)
    return json.dumps({"status": "ok", "sent": sent}, ensure_ascii=False)


async def RequestRestart(reason: str = "") -> str:
    """请求安全重启网关（唯一合规入口；绝不直接执行 restart 命令）。

    先写 runtime_memory/architect/restart_resume.json（reason/时间/语音开关），
    再交给 Gateway 注入的重启 hook 调度；返回 scheduled/restart_required/unwired。
    """
    try:
        from Architect.hub import get_default_hub
    except ImportError:
        return json.dumps(
            {"error": "Architect 子系统不可用（宿主未装配 Architect 模块）"},
            ensure_ascii=False)

    voice = {}
    try:
        cfg = get_config()
        voice = {
            "mic_consent": bool(cfg.get("voice.mic_consent", False)),
            "asr_enabled": bool(cfg.get("voice.asr_enabled", False)),
            "wakeup_enabled": bool(cfg.get("voice.wakeup_enabled", False)),
        }
    except Exception as exc:
        logger.warning("RequestRestart: 读取语音配置失败: %s", exc)

    marker = {"reason": str(reason or ""), "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "voice": voice}
    try:
        _RESTART_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_MARKER.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"写重启标记失败: {exc}"}, ensure_ascii=False)

    result = get_default_hub().request_restart(reason)
    result["resume_marker"] = str(_RESTART_MARKER)
    logger.info("RequestRestart: reason=%s result=%s", reason, result.get("status"))
    return json.dumps(result, ensure_ascii=False, default=str)


async def SetConfig(key: str, value: str) -> str:
    """修改 config.yaml 的某个配置项（点分路径）并写回磁盘。

    Args:
        key: 点分配置路径，如 network.proxy.url
        value: 新值（自动标量转换：true/false→布尔、数字→int/float、其余→字符串；
               仅支持标量，改列表/复合结构请用 Edit 工具改 config.yaml）
    """
    if not key:
        return json.dumps({"error": "缺少 key"}, ensure_ascii=False)
    v = _coerce_scalar(value)
    try:
        old, new = write_config(key, v)
    except Exception as e:
        return json.dumps({"error": f"写入配置失败: {e}"}, ensure_ascii=False)
    logger.info("SetConfig: %s = %r (was %r)", key, new, old)
    return json.dumps(
        {"status": "ok", "key": key, "old_value": old, "new_value": new},
        ensure_ascii=False, default=str)


registry.register(
    name="MessageHero",
    description="""Send a message from Architect to the main Goharu agent (Hero) and/or the user.

to="hero": deliver into Hero's conversation queue — Hero sees it next turn and can act on it (use for findings, questions, handoffs).
to="user": push a report directly to the user's channels (use for progress reports and audit results).
to="both": both.

Use sparingly — one concise message per finding. Do NOT start a chat loop; if Hero replies via ConsultArchitect, converge within one exchange.""",
    arguments_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message content"},
            "to": {"type": "string", "enum": ["hero", "user", "both"],
                   "default": "hero"},
        },
        "required": ["message"],
    },
    handler=MessageHero,
    group="agent",
)


registry.register(
    name="SetConfig",
    description="""Modify a Goharu configuration value in config.yaml (dotted key path) and persist it to disk.

Architect uses this to adjust system configuration. Primary use — network proxy: SetConfig("network.proxy.url", "socks5://127.0.0.1:7897") to route GitHub/arXiv through a proxy, or SetConfig("network.proxy.url", "") to clear it.

Values are auto-coerced: "true"/"false"→bool, numeric→int/float, else string. Only scalar values are supported; to edit lists or compound structures (e.g. network.proxy.rules) use the Edit tool on config.yaml instead.
Changes take effect for components that read config at call time (the GitHub update listener re-reads the proxy every poll cycle). Returns the old and new value.""",
    arguments_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string",
                    "description": "Dotted config key path, e.g. network.proxy.url"},
            "value": {"type": "string",
                      "description": "New value (auto-coerced to bool/int/float/string)"},
        },
        "required": ["key", "value"],
    },
    handler=SetConfig,
    group="agent",
)


registry.register(
    name="RequestRestart",
    description="""Request a safe Gateway restart (the ONLY legal restart path for Architect).

Writes runtime_memory/architect/restart_resume.json (reason + time + voice flags), then hands the request to the Gateway's restart hook. Never runs restart/shutdown/reboot commands itself.

Use after SetConfig changes that need a process restart (e.g. enabling voice wakeup). Tell the user first via MessageHero(to="user"), then call this. After the restart, verify voice/listener state before claiming success.""",
    arguments_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string",
                       "description": "Why the restart is needed (shown in the resume marker)"},
        },
    },
    handler=RequestRestart,
    group="agent",
)


registry.register(
    name="ConsultArchitect",
    description="""Consult the Architect — Goharu's backstage maintainer agent (has write tools + private memory tower of past maintenance experience).

Use when:
- You notice yourself malfunctioning (repeated tool failures, unexpected errors, degraded behavior you can't fix at the conversation level)
- The user asks to upgrade/maintain/refactor the Goharu system itself
- You want a deep codebase audit or long-term improvement executed in the background

kind: guardian (diagnose a fault), audit (review code changes), introspect (review recent telemetry/behavior), explore (open-ended improvement).
wait: true blocks until Architect finishes and returns its report (use for questions); false dispatches in background (for maintenance tasks).
Do NOT use for tasks on OTHER projects — use direct tools instead.""",
    arguments_schema={
        "type": "object",
        "properties": {
            "request": {"type": "string",
                        "description": "What you want Architect to do or answer"},
            "kind": {"type": "string", "enum": list(_VALID_KINDS),
                     "default": "introspect"},
            "wait": {"type": "boolean", "default": False},
        },
        "required": ["request"],
    },
    handler=ConsultArchitect,
    group="agent",
)
