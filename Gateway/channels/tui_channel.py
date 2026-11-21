"""TUI channel —— stdio JSON-RPC 传输（Ink TUI 前端）。

把 session 的 broadcast 翻译成现有 TUI 事件协议（逐字节兼容，
前端零改动）。传输函数经 params["transport"] 注入（生产环境是
gateway_entry.write_json，含 stdout 锁与断管处理）；未注入时退化为
自带的最小 stdout 写入（测试/独立运行用）。
"""

import json
import logging
import sys
import threading

from Gateway.channels.base import Channel
from Gateway.channels.registry import register_channel

logger = logging.getLogger(__name__)

_fallback_lock = threading.Lock()


def _default_transport(obj: dict) -> bool:
    """未注入 transport 时的兜底：直接写 stdout（JSON-RPC 一行一消息）。"""
    try:
        with _fallback_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return True
    except Exception as e:
        logger.warning("tui channel transport write failed: %s", e)
        return False


@register_channel("tui")
class TuiChannel(Channel):
    """stdio JSON-RPC channel。capabilities 全量（富客户端）。"""

    channel_type = "tui"
    capabilities = frozenset({"thinking", "tool_use", "tool_result", "event"})

    def __init__(self, channel_id, params=None):
        super().__init__(channel_id, params)
        self._transport = self.params.get("transport") or _default_transport

    def _emit(self, event_type: str, payload: dict):
        self._transport({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": event_type, "payload": payload},
        })

    # ── 出站（session → TUI 前端，协议形状与旧 Gateway 一致）─────────

    def send_answer(self, text: str, meta: dict | None = None):
        self._emit("agent.answer", {"answer": text})

    def send_thinking(self, text: str | None, meta: dict | None = None):
        if text is None:
            self._emit("agent.thinking", {})          # spinner
        else:
            self._emit("agent.thinking_content", {
                "step": (meta or {}).get("step", 0),
                "thinking": str(text).strip(),
            })

    def send_tool_use(self, name: str, args: dict, call_id: str = "",
                      meta: dict | None = None):
        self._emit("tool.call", {
            "tool": name,
            "arguments": args,
            "call_id": str(call_id),
            "step": (meta or {}).get("step", 0),
        })

    def send_tool_result(self, name: str, result: str, call_id: str = "",
                         is_error: bool = False, meta: dict | None = None):
        self._emit("tool.result", {
            "tool": name,
            "call_id": str(call_id),
            "result": str(result),
        })

    def send_event(self, event_type: str, payload: dict):
        self._emit(event_type, payload or {})
