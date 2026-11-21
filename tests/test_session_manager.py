"""Session/Channel 体系契约测试：注册表 / 轮发 / 入站标注 / turn 循环。

- registry：注册/实例化/未知 type 说明性报错/插件加载
- broadcast：capability 过滤 + 单 channel 异常隔离 + answer 必要能力
- enqueue：来源标注 [来自 {channel_type}]
- turn 循环：假 Actor 驱动真实 AgentSession——enqueue → 处理 → answer 轮发
- TuiChannel：send_* 映射为现有 JSON-RPC 事件形状（协议兼容）

运行：python tests/test_session_manager.py
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Gateway.channels.base import Channel, ChannelMessage
from Gateway.channels.registry import (
    create_channel, register_channel, registered_types,
)
from Gateway.channels.tui_channel import TuiChannel
from Gateway.session import AgentSession
from Gateway.session_memory import SessionMemoryView
from Memory.bridge import create_memory
from Prompting.api import create_context_manager

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


# ── 1. registry ──────────────────────────────────────────────────────
@register_channel("dummy")
class DummyChannel(Channel):
    channel_type = "dummy"

    def __init__(self, channel_id, params=None):
        super().__init__(channel_id, params)
        self.received = []

    def send_answer(self, text, meta=None):
        self.received.append(("answer", text))


check("dummy" in registered_types(), "register_channel 注册成功")
ch = create_channel("dummy", "d1", {"k": 1})
check(isinstance(ch, DummyChannel) and ch.channel_id == "d1" and ch.params["k"] == 1,
      "create_channel 实例化+参数传递")
try:
    create_channel("nonexistent_type", "x")
    check(False, "未知 type 应报错")
except ValueError as e:
    check("未知 channel type" in str(e) and "tui" in str(e),
          "未知 type 报说明性错误（含已注册列表）")

# ── 2. broadcast：capability 过滤 + 异常隔离 ─────────────────────────
class RichChannel(Channel):
    channel_type = "rich"
    capabilities = frozenset({"thinking", "tool_use", "tool_result", "event"})

    def __init__(self, channel_id, params=None):
        super().__init__(channel_id, params)
        self.calls = []

    def send_answer(self, text, meta=None):
        self.calls.append(("answer", text))

    def send_thinking(self, text, meta=None):
        self.calls.append(("thinking", text))

    def send_tool_use(self, name, args, call_id="", meta=None):
        self.calls.append(("tool_use", name))

    def send_event(self, event_type, payload):
        self.calls.append(("event", event_type))


class BrokenChannel(RichChannel):
    channel_type = "broken"

    def send_answer(self, text, meta=None):
        raise RuntimeError("boom")


TMP = Path(tempfile.mkdtemp(prefix="session_mgr_test_"))
bridge = create_memory(overrides={
    "memory.root_dir": str(TMP / "mem"),
    "memory.retrieval.embedding_model_path": str(TMP / "empty_model"),
    "memory.retrieval.use_reranker": False,
})
view = SessionMemoryView("test_s", bridge, max_messages=10)


class FakeBgTasks:
    reactivation_count = 0

    def drain_pending(self):
        return []

    def has_pending(self):
        return False

    def inject_into_memory(self, memory, results):
        pass


class FakeActor:
    """剧本化假 Actor：act 直接给答案，记录收到的消息。"""

    def __init__(self):
        self.reset_count = 0

    async def act(self, **kwargs):
        return {"type": "answer", "answer": "假回答：收到"}

    def reset_retrieval_cache(self):
        self.reset_count += 1

    def get_last_usage(self):
        return {}

    def build_messages_with_document(self, extra=None):
        return {"messages": []}

    def prepare_messages_for_provider(self, messages):
        return None

    def query(self, messages, **kw):
        return "假摘要"


session = AgentSession(
    "test_s", view, FakeActor(), pipeline_manager=None,
    bg_tasks=FakeBgTasks(), ctx_manager=create_context_manager(),
)
rich = RichChannel("rich1")
broken = BrokenChannel("broken1")
plain = DummyChannel("plain1")  # 无可选能力，只能收 answer
session.attach_channel(rich)
session.attach_channel(broken)
session.attach_channel(plain)

session._broadcast_thinking("想…", meta={"step": 1})
session._broadcast_tool_use("Read", {"p": 1}, "c1")
session._broadcast_event("token.stats", {"x": 1})
session._broadcast_answer("你好")

check(("thinking", "想…") in rich.calls, "能力齐全的 channel 收到 thinking")
check(any(c == ("answer", "你好") for c in rich.calls),
      "异常 channel 不阻断：rich 在 broken 之后仍收到 answer")
check(any(c == ("answer", "你好") for c in plain.received),
      "无可选能力的 channel 仍收到 answer（必要能力）")
check(not any(c[0] in ("thinking", "tool_use", "event") for c in plain.received),
      "无可选能力的 channel 不收 thinking/tool_use/event（capability 过滤）")

# ── 3. enqueue 来源标注 ──────────────────────────────────────────────
# 暂停处理驱动，避免竞态消费掉待断言的队列项
_start_processing_orig = session._start_processing
session._start_processing = lambda: None
session.enqueue(ChannelMessage(text="在吗", channel_type="qqbot", channel_id="qq1",
                               meta={"group": "123"}))
item = session.pending_items[-1]
check("[来自 qqbot" in item["data"] and "在吗" in item["data"],
      f"入站消息带来源标注（{item['data'][:40]}…）")
session.drain_pending_items()
session._start_processing = _start_processing_orig

# ── 4. turn 循环端到端（假 Actor）────────────────────────────────────
session.enqueue(ChannelMessage(text="你好", channel_type="tui", channel_id="t1"))
deadline = time.time() + 10
baseline = len(rich.calls)
while time.time() < deadline:
    if sum(1 for c in rich.calls if c[0] == "answer" and c[1] == "假回答：收到") >= 1:
        break
    time.sleep(0.05)
check(any(c == ("answer", "假回答：收到") for c in rich.calls),
      "enqueue → turn 循环 → answer 轮发全链路")
ctx = session.memory.get_context()
check(any("你好" in str(m.get("content", "")) for m in ctx),
      "用户消息已写入本 session 记忆")

# ── 5. TuiChannel 协议形状 ───────────────────────────────────────────
sent = []
tui = TuiChannel("t1", {"transport": sent.append})
tui.send_answer("最终回答")
tui.send_thinking(None)
tui.send_thinking("思考内容", meta={"step": 2})
tui.send_tool_use("run_cmd", {"command": "ls"}, "c9", meta={"step": 2})
tui.send_tool_result("run_cmd", "ok", "c9")
tui.send_event("token.stats", {"current_tokens": 5})

shapes = [(s.get("params", {}).get("type"), s.get("method")) for s in sent]
check(shapes == [
    ("agent.answer", "event"),
    ("agent.thinking", "event"),
    ("agent.thinking_content", "event"),
    ("tool.call", "event"),
    ("tool.result", "event"),
    ("token.stats", "event"),
], "TuiChannel 事件类型序列与现有协议一致")
check(sent[0]["params"]["payload"] == {"answer": "最终回答"},
      "agent.answer payload 形状兼容")
check(sent[3]["params"]["payload"]["call_id"] == "c9"
      and sent[3]["params"]["payload"]["step"] == 2,
      "tool.call payload 带 call_id 与 step")

# ── 6. 优先级：high 插队 + 打断，normal 排队 ──
from Gateway.channels.base import ChannelMessage as CM
_start_orig2 = session._start_processing
_starts = []
session._start_processing = lambda: _starts.append(1)
session.drain_pending_items()
session.enqueue(CM(text='普通消息', channel_type='record', channel_id='rec1'))
session.enqueue(CM(text='高优先', channel_type='record', channel_id='rec1',
                priority='high'))
items = session.pending_items
check(len(items) == 2 and '高优先' in items[0]['data'],
      '高优先级消息插到队首')
check(not session._interrupt_event.is_set(), '空闲时高优先级不设中断标记')
session._processing = True
session._interrupt_event.clear()
session.enqueue(CM(text='打断！', channel_type='record', channel_id='rec1',
                priority='high'))
check(session._interrupt_event.is_set(), '处理中收到高优先级 → 设置中断标记')
check('打断！' in session.pending_items[0]['data'], '打断消息位于队首')
session._processing = False
session._interrupt_event.clear()
session._start_processing = _start_orig2
session.drain_pending_items()


bridge.close()

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
