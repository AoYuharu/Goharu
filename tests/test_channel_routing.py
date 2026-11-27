"""channel 跟随路由契约测试：活跃 channel 跟随 + 切换提醒 + TUI 常驻副本。

直接运行：python tests/test_channel_routing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Gateway.channels.base import Channel, ChannelMessage  # noqa: E402
from Gateway.session import AgentSession  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


class FakeChannel(Channel):
    def __init__(self, channel_id, channel_type, caps=()):
        super().__init__(channel_id)
        self.channel_type = channel_type
        self.capabilities = frozenset(caps)
        self.answers = []
        self.thinkings = []
        self.tool_uses = []
        self.events = []
        self.text_outputs = []

    def send_answer(self, text, meta=None):
        self.answers.append(str(text))

    def send_thinking(self, text, meta=None):
        self.thinkings.append(text)

    def send_tool_use(self, name, args, call_id="", meta=None):
        self.tool_uses.append(name)

    def send_event(self, event_type, payload):
        self.events.append((event_type, payload))

    def send_text_output(self, text, meta=None):
        self.text_outputs.append(str(text))


def make_session():
    s = AgentSession("default", None, None, None, None, None)
    s._start_processing = lambda: None   # 不起处理线程
    return s


def msg(channel, text="你好"):
    return ChannelMessage(text=text, channel_type=channel.channel_type,
                          channel_id=channel.channel_id)


def main():
    tui = FakeChannel("tui_main", "tui", {"thinking", "tool_use", "tool_result", "event"})
    qq = FakeChannel("qq_main", "qqbot", {"thinking", "text_output", "file"})
    tg = FakeChannel("tg_main", "telegram", {"text_output"})

    print("== 无消息历史 → 全量广播 ==")
    s = make_session()
    s.attach_channel(tui)
    s.attach_channel(qq)
    s._broadcast_answer("hello")
    check("TUI 收到", tui.answers == ["hello"])
    check("QQ 收到", qq.answers == ["hello"])

    print("== QQ 来消息 → 路由到 QQ + TUI 常驻 ==")
    s.enqueue(msg(qq, "qq 上问一句"))
    tui.answers.clear(); qq.answers.clear()
    s._broadcast_answer("回答A")
    check("QQ 收到回答", qq.answers == ["回答"] or qq.answers == ["回答A"])
    check("TUI 常驻副本", tui.answers == ["回答A"])

    print("== 同 channel 再发 → 无切换提醒 ==")
    s.enqueue(msg(qq, "再问"))
    texts = [it["data"] for it in s.pending_items if it.get("type") == "message"]
    check("无切换提醒", not any("switched chat channel" in t for t in texts))

    print("== 切到 TUI → 注入切换提醒 + 路由跟随 ==")
    s.enqueue(msg(tui, "回电脑前了"))
    texts = [it["data"] for it in s.pending_items if it.get("type") == "message"]
    last = texts[-1]
    check("切换提醒注入", "switched chat channel from qqbot to tui" in last)
    check("提醒无需回应", "don't need to react" in last.lower() or "You don't need to react" in last)
    qq.answers.clear(); tui.answers.clear()
    s._broadcast_answer("回答B")
    check("TUI 收到（活跃）", tui.answers == ["回答B"])
    check("QQ 不再收到", qq.answers == [])

    print("== 合成来源（architect）不抢路由 ==")
    s.enqueue(ChannelMessage(text="[Architect] 报告", channel_type="architect",
                             channel_id="architect_hub"))
    check("active 仍是 tui", s._active_channel == ("tui", "tui_main"))

    print("== capability 过滤仍生效 ==")
    tui.tool_uses.clear(); qq.tool_uses.clear()
    s._broadcast_tool_use("run_cmd", {}, "c1")
    check("tool_use 只到 TUI（QQ 无此能力）",
          tui.tool_uses == ["run_cmd"] and qq.tool_uses == [])

    print("== 中间 text 只进 TUI event，不进聊天消息 ==")
    tui.events.clear(); qq.text_outputs.clear()
    s._broadcast_progress_text("阶段性推测", 3, True)
    check("TUI 收到 agent.text_content",
          tui.events == [("agent.text_content", {"step": 3, "text": "阶段性推测"})])
    check("QQ 不收任意中间 text", qq.text_outputs == [])
    s._broadcast_progress_text("被隐藏的进展", 4, False)
    check("show_text=false 不广播", len(tui.events) == 1)
    s._send("text_output", "send_text_output", "后台任务已启动", {"kind": "bg_started"})
    check("真实后台通知仍发 QQ", qq.text_outputs == ["后台任务已启动"])

    print("== 第三通道消息 → 路由切换 + 提醒 ==")
    s.attach_channel(tg)
    s.enqueue(msg(tg, "电报上问"))
    texts = [it["data"] for it in s.pending_items if it.get("type") == "message"]
    check("tui→telegram 提醒",
          "from tui to telegram" in texts[-1])
    tui.answers.clear(); qq.answers.clear(); tg.answers.clear()
    s._broadcast_answer("回答C")
    check("telegram 收到", tg.answers == ["回答C"])
    check("TUI 常驻副本", tui.answers == ["回答C"])
    check("QQ 收不到", qq.answers == [])

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
