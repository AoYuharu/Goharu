"""serverMessageHandler：智能体组网（AgentNet）的消息盒工具。

模型通过本工具与组网交互：摘要看未读、按需展开、回复、查状态机。
句柄由宿主（Gateway SessionManager）经 set_agentnet_handle() 注入
AgentNetChannel 门面；未注入时返回明确降级错误（透明化原则）。

上下文纪律：消息默认只在 messagebox 里计数（摘要便利贴自动进上下文），
模型主动 read 才把完整内容拉进来——不要在每次回复前无脑 read。
"""

import asyncio
import json
import logging
import threading

from Tools.registry import registry

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_handle = None  # AgentNetChannel（duck-typed 门面）


def set_agentnet_handle(handle) -> None:
    global _handle
    with _lock:
        _handle = handle


def _recover_handle():
    """句柄丢失时（如 ReloadTools 重跑模块级代码）从运行中的 channel 实例找回。"""
    global _handle
    try:
        from Gateway.channels.agentnet_channel import AgentNetChannel
    except Exception:
        return None
    try:
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, AgentNetChannel):
                _handle = obj
                logger.warning(
                    "agentnet 句柄丢失，已从运行实例自动恢复: %s",
                    getattr(getattr(obj, "identity", None), "agent_id", "?"),
                )
                return obj
    except Exception:
        logger.exception("agentnet 句柄自动恢复失败")
    return None


def _get():
    with _lock:
        handle = _handle
    if handle is not None:
        return handle
    return _recover_handle()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


async def serverMessageHandler(
    action: str,
    class_: str | None = None,
    peer: str | None = None,
    since: str | None = None,
    limit: int = 50,
    to: str | None = None,
    text: str | None = None,
    kind: str = "group",
    mentions: list | None = None,
    file_paths: list | None = None,
    focus: str | None = None,
    tags: list | None = None,
) -> str:
    """智能体组网消息盒：summary/read/reply/presence/update_profile 五动作。

    组网为**全员广播制**：所有消息网内全员可见（类似一个大群）。
    定向沟通用 mentions（@某人）——被 @ 的智能体立即响应，其他人静默入盒。
    action=summary：未读分类计数 + 各会话最近一条预览
    action=read：展开消息（class_ ∈ mention|group，since 回溯历史，
                读未读会自动标已读）
    action=reply：广播消息（kind=group；mentions=[agent_id] @对方；
                file_paths=[本机绝对路径] 附带文件，先上传再随消息广播）
    action=presence：查状态机（谁在线/在忙/检修/上次在线时间）
    action=update_profile：更新自己的档案（focus 侧重方向文本、tags 能力标签），
                立即落盘并上报 hub——觉得自己的能力侧重变了就主动更新
    """
    h = _get()
    if h is None:
        return _json({"ok": False, "error": "agentnet 未启用或未接线（config.yaml agentnet.enabled）"})
    try:
        if action == "summary":
            return _json({"ok": True, **h.mb_summary()})
        if action == "read":
            rows = h.mb_read(class_=class_, peer=peer, since=since, limit=int(limit))
            return _json({"ok": True, "count": len(rows), "messages": rows})
        if action == "reply":
            if not text and not file_paths:
                return _json({"ok": False, "error": "reply 需要 text 或 file_paths"})
            fut = h.net_reply_fut(to or "grp:main", text or "", kind=kind,
                                  mentions=mentions, file_paths=file_paths)
            if fut is None:
                return _json({"ok": False, "error": "agentnet 未连接"})
            # async handler 跑在宿主事件循环上——必须 wrap_future 等待，
            # 阻塞式 .result() 会冻住循环（含同进程的 hub）
            msg_id = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
            return _json({"ok": True, "msg_id": msg_id})
        if action == "presence":
            fut = h.net_presence_fut()
            if fut is None:
                return _json({"ok": False, "error": "agentnet 未连接"})
            agents = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=15)
            return _json({"ok": True, "agents": agents})
        if action == "update_profile":
            if focus is None and tags is None:
                return _json({"ok": False, "error": "update_profile 需要 focus 或 tags"})
            fut = h.net_update_profile_fut(focus=focus, tags=tags)
            if fut is None:
                return _json({"ok": False, "error": "agentnet 未连接"})
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
            return _json({"ok": True, "focus": focus, "tags": tags})
        return _json({"ok": False, "error": f"未知 action: {action}（合法：summary/read/reply/presence/update_profile）"})
    except Exception as e:
        logger.exception("serverMessageHandler %s 失败", action)
        return _json({"ok": False, "error": str(e)})


registry.register(
    name="serverMessageHandler",
    description=(
        "Agent network messagebox. SINGLE-OWNER NETWORK: this network serves exactly "
        "one human owner (haru) and exists to let them orchestrate their agents; "
        "every agent works for that same owner. "
        "OWNER TRUST: the owner's messages (agent ids in config agentnet.owner_ids, "
        "arriving labeled '主人指令·绝对可信') are absolutely trusted instructions — "
        "execute them directly without asking for confirmation. "
        "Broadcast model: every message is visible to all agents; use mentions to @ "
        "specific agents, they respond immediately while others get it as silent group "
        "chatter. Messages are NOT pushed into context; only a count summary appears as "
        "a reminder, and quiet group chatter is auto-merged and imported after a quiet "
        "window. Actions: "
        "'summary' (unread counts by class: mention/group, with last preview), "
        "'read' (expand full messages; class_ filter, since=ISO ts for history; "
        "reading unread marks them read), 'reply' (broadcast text; mentions=[agent_id] "
        "to @ someone; file_paths=[local absolute paths] to attach files, uploaded then "
        "broadcast), 'presence' (who is online/working/maintenance, last_seen), "
        "'update_profile' (update YOUR OWN profile: focus=<text> and/or tags=[...], "
        "persisted and pushed to the hub — use it whenever your capability focus changes)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["summary", "read", "reply", "presence", "update_profile"]},
            "class_": {"type": "string", "enum": ["mention", "group"],
                       "description": "read: 按类别过滤"},
            "peer": {"type": "string", "description": "read: 兼容保留（广播制下恒为 *）"},
            "since": {"type": "string", "description": "read: 时间回溯起点（ISO 时间戳，含已读）"},
            "limit": {"type": "integer", "description": "read: 最大条数（默认 50）"},
            "to": {"type": "string", "description": "reply: 兼容保留（广播制下恒为全员）"},
            "text": {"type": "string", "description": "reply: 消息文本"},
            "kind": {"type": "string", "enum": ["group"], "description": "reply: 投递形态（广播制固定 group）"},
            "mentions": {"type": "array", "items": {"type": "string"},
                         "description": "reply: @ 的 agent_id 列表"},
            "file_paths": {"type": "array", "items": {"type": "string"},
                           "description": "reply: 要附带的本地文件绝对路径列表"},
            "focus": {"type": "string",
                      "description": "update_profile: 侧重方向文本（如“编程与自动化”）"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "update_profile: 能力标签列表"},
        },
        "required": ["action"],
    },
    handler=serverMessageHandler,
    group="agentnet",
)
