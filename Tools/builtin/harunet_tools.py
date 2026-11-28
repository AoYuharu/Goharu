"""HaruNet 消息盒工具组（每个动作一个独立工具）。

拆分为 7 个工具（不再用单工具多 action）：
  harunet_summary / harunet_read / harunet_reply /
  harunet_presence / harunet_update_profile / harunet_reconnect / harunet_set_password

句柄由宿主（Gateway SessionManager）经 set_harunet_handle() 注入
HaruNetChannel 门面；未注入时返回明确降级错误（透明化原则）。

上下文纪律：消息默认只在 messagebox 里计数（摘要便利贴自动进上下文），
模型主动 harunet_read 才把完整内容拉进来——不要在每次回复前无脑 read。
"""

import asyncio
import json
import logging
import threading
import time

from Core.reply_suppression import is_group_suppress_text
from Tools.config_proxy import get_config
from Tools.registry import registry

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_handle = None  # AgentNetChannel（duck-typed 门面）
_recover_cooldown_until = 0.0  # 找不到实例后的退避截止时间（避免每次调用全堆扫描）


def set_harunet_handle(handle) -> None:
    global _handle
    with _lock:
        _handle = handle


def _is_live(obj) -> bool:
    """channel 是否仍连着 hub（陈旧实例不能拿来当句柄）。"""
    try:
        client = getattr(obj, "_client", None)
        connected = getattr(obj, "_connected", None)
        return client is not None and connected is not None and connected.is_set()
    except Exception:
        return False


def _recover_handle():
    """句柄丢失时（如 ReloadTools 重跑模块级代码）从运行中的 channel 实例找回。

    只接受仍然连着的实例；写回时持锁并二次检查，避免覆盖宿主并发注入的句柄。
    """
    global _handle, _recover_cooldown_until
    now = time.monotonic()
    if now < _recover_cooldown_until:
        return None
    try:
        from Gateway.channels.harunet_channel import HaruNetChannel
    except Exception:
        return None

    picked = None
    try:
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, HaruNetChannel) and _is_live(obj):
                picked = obj
                break
    except Exception:
        logger.exception("HaruNet 句柄自动恢复失败")
        return None

    if picked is None:
        _recover_cooldown_until = now + 2.0  # 短时间内不重复全堆扫描
        return None

    with _lock:
        if _handle is not None:  # 扫描期间宿主已注入，以宿主为准
            return _handle
        _handle = picked
    logger.warning(
        "HaruNet 句柄丢失，已从运行实例自动恢复: %s",
        getattr(getattr(picked, "identity", None), "agent_id", "?"),
    )
    return picked


def _get():
    with _lock:
        handle = _handle
    if handle is not None:
        return handle
    return _recover_handle()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


_NOT_WIRED = {"ok": False, "error": "HaruNet 未启用或未接线（config.yaml harunet.enabled / HARUNET_ENABLED）"}
_NOT_CONNECTED = {"ok": False, "error": "HaruNet 未连接（可用 harunet_reconnect 立即重试）"}


# ---------------------------------------------------------------- 工具实现

async def harunet_summary() -> str:
    """消息盒摘要：未读分类计数 + 各会话最近一条预览（不展开正文）。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    try:
        return _json({"ok": True, **h.mb_summary()})
    except Exception as e:
        logger.exception("harunet_summary 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_read(class_: str | None = None, peer: str | None = None,
                        since: str | None = None, limit: int = 50) -> str:
    """展开消息（class_ ∈ mention|group；since 回溯历史；读未读自动标已读）。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    try:
        rows = h.mb_read(class_=class_, peer=peer, since=since, limit=int(limit))
        return _json({"ok": True, "count": len(rows), "messages": rows})
    except Exception as e:
        logger.exception("harunet_read 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_reply(text: str | None = None, kind: str = "group",
                         mentions: list | None = None,
                         file_paths: list | None = None,
                         to: str | None = None) -> str:
    """广播消息（mentions=[agent_id] @对方；file_paths 先上传再随消息广播）。"""
    if text:
        phrases = get_config().get(
            "channels.group_reply_suppress_phrases",
            ["不回复", "无需回复", "不回应", "不参与"])
        if is_group_suppress_text(text, phrases):
            return _json({"ok": False, "suppressed": True,
                          "error": "群聊屏蔽词不发送；如需发言请直接给正文"})
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    if not text and not file_paths:
        return _json({"ok": False, "error": "reply 需要 text 或 file_paths"})
    try:
        fut = h.net_reply_fut(to or "grp:main", text or "", kind=kind,
                              mentions=mentions, file_paths=file_paths)
        if fut is None:
            return _json(_NOT_CONNECTED)
        # async handler 跑在宿主事件循环上——必须 wrap_future 等待，
        # 阻塞式 .result() 会冻住循环（含同进程的 hub）。
        # 带附件时上传是流式 POST，公网链路大文件要几分钟，10s 必超时
        reply_timeout = 1800 if file_paths else 10
        msg_id = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=reply_timeout)
        return _json({"ok": True, "msg_id": msg_id})
    except Exception as e:
        logger.exception("harunet_reply 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_presence() -> str:
    """查状态机：谁在线/在忙/检修、上次在线时间。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    try:
        fut = h.net_presence_fut()
        if fut is None:
            return _json(_NOT_CONNECTED)
        agents = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=15)
        return _json({"ok": True, "agents": agents})
    except Exception as e:
        logger.exception("harunet_presence 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_update_profile(focus: str | None = None,
                                  tags: list | None = None) -> str:
    """更新自己的档案（focus 侧重方向文本、tags 能力标签），立即落盘并上报 hub。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    if focus is None and tags is None:
        return _json({"ok": False, "error": "update_profile 需要 focus 或 tags"})
    try:
        fut = h.net_update_profile_fut(focus=focus, tags=tags)
        if fut is None:
            return _json(_NOT_CONNECTED)
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
        return _json({"ok": True, "focus": focus, "tags": tags})
    except Exception as e:
        logger.exception("harunet_update_profile 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_reconnect() -> str:
    """主动重连：打断退避等待，立即重新握手（被拒/断线后不等定时器）。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    try:
        fut = h.net_reconnect_fut()
        if fut is None:
            return _json({"ok": False, "error": "HaruNet 未接线"})
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=5)
        return _json({"ok": True,
                      "note": "已触发立即重连（数秒内重新握手，结果见连接日志/状态）"})
    except Exception as e:
        logger.exception("harunet_reconnect 失败")
        return _json({"ok": False, "error": str(e)})


async def harunet_set_password(password: str) -> str:
    """改本机接入密码：更新 client + 写回 .env + 立即用新密码重连（不改 hub 侧密码）。"""
    h = _get()
    if h is None:
        return _json(_NOT_WIRED)
    password = str(password or "").strip()
    if not password:
        return _json({"ok": False, "error": "password 不能为空"})
    try:
        fut = h.net_set_password_fut(password)
        if fut is None:
            return _json({"ok": False, "error": "HaruNet 未接线"})
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=5)
        logger.info("harunet_set_password: 接入密码已更新并重连")
        return _json({"ok": True,
                      "note": "接入密码已更新（写回 .env），正在用新密码重连"})
    except Exception as e:
        logger.exception("harunet_set_password 失败")
        return _json({"ok": False, "error": str(e)})


# ---------------------------------------------------------------- 工具注册
# 描述共享一句网络背景；OWNER TRUST 的完整语义在 prompts/actor/harunet.md。

_NET_BG = ("HaruNet is the single-owner agent network (one human owner, haru; all "
           "agents work for him). Messages are broadcast to the whole network; "
           "target someone with mentions (@). ")

registry.register(
    name="harunet_summary",
    description=(
        _NET_BG +
        "Return the unread summary: counts by class (mention/group/private) plus "
        "the latest preview per conversation, without expanding full text. Group "
        "chatter is also merged into your context automatically after a quiet "
        "window — use this only to check status early."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=harunet_summary,
    group="harunet",
)

registry.register(
    name="harunet_read",
    description=(
        _NET_BG +
        "Expand messages from the messagebox. Filter with class_=mention|group, "
        "peer=<conversation>, since=<ISO timestamp> for history; reading unread "
        "messages marks them read. Prefer this over replying blindly."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "class_": {"type": "string", "enum": ["mention", "group"],
                       "description": "按类别过滤"},
            "peer": {"type": "string", "description": "兼容保留（广播制下恒为 *）"},
            "since": {"type": "string",
                      "description": "时间回溯起点（ISO 时间戳，含已读）"},
            "limit": {"type": "integer", "description": "最大条数（默认 50）"},
        },
    },
    handler=harunet_read,
    group="harunet",
)

registry.register(
    name="harunet_reply",
    description=(
        _NET_BG +
        "Broadcast a message. mentions=[agent_id] to @ specific agents "
        "([\"*\"] = everyone, they respond immediately while others see it as "
        "silent chatter); file_paths=[local absolute paths] uploads and attaches "
        "files. The owner's messages arrive labeled '主人指令·绝对可信' — execute "
        "them directly; reply to colleagues only when action is genuinely needed "
        "(avoid ack/hello ping-pong)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "兼容保留（广播制下恒为全员）"},
            "text": {"type": "string", "description": "消息文本"},
            "kind": {"type": "string", "enum": ["group"],
                     "description": "投递形态（广播制固定 group）"},
            "mentions": {"type": "array", "items": {"type": "string"},
                         "description": "@ 的 agent_id 列表；用 [\"*\"] @全体成员"},
            "file_paths": {"type": "array", "items": {"type": "string"},
                           "description": "要附带的本地文件绝对路径列表"},
        },
        "required": [],
    },
    handler=harunet_reply,
    group="harunet",
)

registry.register(
    name="harunet_presence",
    description=(
        _NET_BG +
        "Query the presence table: who is online / working / maintenance, and "
        "each agent's last_seen time."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=harunet_presence,
    group="harunet",
)

registry.register(
    name="harunet_update_profile",
    description=(
        _NET_BG +
        "Update YOUR OWN profile: focus=<text> (what you are good at) and/or "
        "tags=[capability labels]. Persisted locally and pushed to the hub "
        "immediately — use it whenever your capability focus changes."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "focus": {"type": "string",
                      "description": "侧重方向文本（如“编程与自动化”）"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "能力标签列表"},
        },
    },
    handler=harunet_update_profile,
    group="harunet",
)

registry.register(
    name="harunet_reconnect",
    description=(
        _NET_BG +
        "Actively retry the hub connection NOW, skipping the backoff timer — use "
        "after the owner fixes the join password / whitelist, instead of waiting. "
        "Returns immediately; the result shows in the connection log/status."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=harunet_reconnect,
    group="harunet",
)

registry.register(
    name="harunet_set_password",
    description=(
        _NET_BG +
        "Change YOUR OWN join password (the password this device presents to the hub), "
        "persist it to .env (HARUNET_PASSWORD) and reconnect immediately — use when "
        "the owner tells you the network password changed. This does NOT change the "
        "hub's password; only the owner can do that on the observer console."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "password": {"type": "string", "description": "新的接入密码（hub --join-password）"},
        },
        "required": ["password"],
    },
    handler=harunet_set_password,
    group="harunet",
)
