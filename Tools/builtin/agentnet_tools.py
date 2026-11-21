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


def _get():
    with _lock:
        return _handle


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
) -> str:
    """智能体组网消息盒：summary/read/reply/presence 四动作。

    组网为**全员广播制**：所有消息网内全员可见（类似一个大群）。
    定向沟通用 mentions（@某人）——被 @ 的智能体立即响应，其他人静默入盒。
    action=summary：未读分类计数 + 各会话最近一条预览
    action=read：展开消息（class_ ∈ mention|group，since 回溯历史，
                读未读会自动标已读）
    action=reply：广播消息（kind=group；mentions=[agent_id] @对方；
                file_paths=[本机绝对路径] 附带文件，先上传再随消息广播）
    action=presence：查状态机（谁在线/在忙/检修/上次在线时间）
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
        return _json({"ok": False, "error": f"未知 action: {action}（合法：summary/read/reply/presence）"})
    except Exception as e:
        logger.exception("serverMessageHandler %s 失败", action)
        return _json({"ok": False, "error": str(e)})


registry.register(
    name="serverMessageHandler",
    description=(
        "Agent network messagebox (broadcast model: every message is visible to all "
        "agents; use mentions to @ specific agents, they respond immediately while "
        "others get it as silent group chatter). Messages are NOT pushed into context; "
        "only a count summary appears as a reminder. Actions: "
        "'summary' (unread counts by class: mention/group, with last preview), "
        "'read' (expand full messages; class_ filter, since=ISO ts for history; "
        "reading unread marks them read), 'reply' (broadcast text; mentions=[agent_id] "
        "to @ someone; file_paths=[local absolute paths] to attach files, uploaded then "
        "broadcast), 'presence' (who is online/working/maintenance, last_seen)."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["summary", "read", "reply", "presence"]},
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
        },
        "required": ["action"],
    },
    handler=serverMessageHandler,
    group="agentnet",
)
