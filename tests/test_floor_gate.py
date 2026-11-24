"""网络话语权入队关卡（AgentSession floor gate）契约测试。

直接实例化 AgentSession（仅字段初始化，dummy 依赖），注入 FloorState 与
假 arch_dispatch，验证 channel 之前的拦截决策与 dispatch worker 行为。

运行：python tests/test_floor_gate.py
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Gateway.channels.base import Channel, ChannelMessage
from Gateway.floor import FloorState
from Gateway.session import AgentSession

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


class FakeChannel(Channel):
    """记录出站文本的哑 channel。"""

    def __init__(self, channel_id="fake_main", channel_type="tui"):
        super().__init__(channel_id)
        self.channel_type = channel_type
        self.sent: list[str] = []

    def send_answer(self, text, meta=None):
        self.sent.append(str(text))


def make_session(floor: FloorState, agent_id: str, arch_dispatch=None):
    s = AgentSession(session_id="floor_test", memory_view=None, actor=None,
                     pipeline_manager=None, bg_tasks=None, ctx_manager=None)
    s.set_floor_context(floor, agent_id, arch_dispatch)
    s._start_processing = lambda: None   # 不真起处理线程
    return s


def msg(text, channel_type="tui", channel_id="fake_main"):
    return ChannelMessage(text=text, channel_type=channel_type,
                          channel_id=channel_id)


def wait(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def floor_of(holder, persona="main", since=None, name="haru-box"):
    return {"holder_agent_id": holder, "holder_name": name,
            "persona": persona, "since": since or time.time()}


def main():
    # ---------------------------------------------------------- 1. FloorState
    print("\n[1] FloorState 值对象")
    st = FloorState()
    check("空快照", not st.snapshot().held)
    snap = st.update(floor_of("agt_b"))
    check("update 生效", snap.held and snap.holder_agent_id == "agt_b"
          and snap.persona == "main")
    e1 = snap.epoch
    snap = st.update(None)
    check("清空 + epoch 增", not snap.held and snap.epoch > e1)
    check("held_by", st.update(floor_of("agt_b", "arch")).held_by("agt_b", "arch"))

    # 相同语义快照必须幂等：presence 周期广播不得反复重置持有期
    st2 = FloorState()
    first = st2.update(floor_of("agt_b", "arch", since=123.0))
    again = st2.update(floor_of("agt_b", "arch", since=123.0))
    check("相同快照幂等", again.epoch == first.epoch and again.since == first.since,
          f"{first.epoch} -> {again.epoch}")
    no_since = st2.update({"holder_agent_id": "agt_b", "holder_name": "haru-box",
                           "persona": "arch", "note": ""})
    check("缺省 since 不刷新", no_since.epoch == first.epoch
          and no_since.since == 123.0, f"{no_since.since}/{no_since.epoch}")
    changed = st2.update(floor_of("agt_b", "main", since=123.0))
    check("人格变化才增 epoch", changed.epoch == first.epoch + 1,
          f"{first.epoch} -> {changed.epoch}")
    st2.update(None)
    epoch_cleared = st2.snapshot().epoch
    st2.update(None)
    check("重复清空幂等", st2.snapshot().epoch == epoch_cleared)

    # ------------------------------------------------- 2. 无话语权 → process
    print("\n[2] 无话语权透传")
    floor = FloorState()
    ch = FakeChannel()
    s = make_session(floor, "agt_a")
    s.channels.append(ch)
    check("gate=process", s._floor_gate(msg("hi")) == "process")

    # ------------------------------------- 3. 话语权在别处 + 本地 TUI 输入
    print("\n[3] 话语权在别处：本地输入透明提示")
    floor.update(floor_of("agt_b", "arch", name="haru-box"))
    ch.sent.clear()
    decision = s._floor_gate(msg("你好", "tui", "fake_main"))
    check("gate=handled", decision == "handled", decision)
    check("收到透明提示", any("话语权当前在" in x and "haru-box" in x and "arch" in x
                          for x in ch.sent), str(ch.sent))
    check("提示带收回指引", any("FloorControl" in x or "观察台" in x
                          for x in ch.sent), str(ch.sent))

    # ------------------------------------- 4. 话语权在别处 + agentnet 广播副本
    print("\n[4] 话语权在别处：组网副本静默")
    ch.sent.clear()
    check("gate=handled", s._floor_gate(msg("群消息副本", "agentnet", "agentnet_main"))
          == "handled")
    check("无提示发送", not ch.sent, str(ch.sent))

    # ------------------------------------- 5. 持有=自己·main
    print("\n[5] 持有=自己·main")
    floor.update(floor_of("agt_a", "main"))
    check("gate=process", s._floor_gate(msg("hi")) == "process")

    # ------------------------------------- 6. 持有=自己·arch → dispatch
    print("\n[6] 持有=自己·arch 派发")
    dispatched = []

    def fake_arch(question, reminder=None):
        dispatched.append((question, reminder))
        return f"答复：{question}"

    floor.update(floor_of("agt_a", "arch"))
    s2 = make_session(floor, "agt_a", fake_arch)
    ch2 = FakeChannel()
    s2.channels.append(ch2)
    check("gate=dispatch", s2._floor_gate(msg("问题一")) == "dispatch")
    t0 = time.time()
    s2.enqueue(msg("问题一"))
    elapsed = time.time() - t0
    check("enqueue 立即返回", elapsed < 0.5, f"{elapsed:.2f}s")
    check("worker 完成", wait(lambda: any("答复：问题一" in x for x in ch2.sent)),
          str(ch2.sent))
    check("ack 先于结果", ch2.sent and ch2.sent[0].startswith("（arch 智能体接手"),
          str(ch2.sent[:2]))
    check("结果带 arch 标记", any(x.startswith("【arch 智能体】") for x in ch2.sent),
          str(ch2.sent))
    check("主队列未被占用", not s2.pending_items, str(s2.pending_items))

    # ------------------------------------- 7. 串行 + 非阻塞（慢派发）
    print("\n[7] 串行派发（arch 慢时排队不阻塞）")
    release = threading.Event()
    order = []

    def slow_arch(question, reminder=None):
        order.append(("start", question))
        release.wait(timeout=5)
        order.append(("end", question))
        return f"done:{question}"

    s3 = make_session(floor, "agt_a", slow_arch)
    ch3 = FakeChannel()
    s3.channels.append(ch3)
    t0 = time.time()
    s3.enqueue(msg("A"))
    s3.enqueue(msg("B"))
    check("两条入队总耗时极短", time.time() - t0 < 0.5, f"{time.time()-t0:.2f}s")
    check("A 先开跑", wait(lambda: ("start", "A") in order))
    check("B 未抢先", ("start", "B") not in order, str(order))
    release.set()
    check("两条都完成", wait(lambda: len([x for x in order if x[0] == "end"]) == 2),
          str(order))
    check("串行顺序 A→B", order == [("start", "A"), ("end", "A"),
                                     ("start", "B"), ("end", "B")], str(order))

    # ------------------------------------- 8. long-hold reminder
    print("\n[8] long-hold reminder")
    rem_seen = []

    def rem_arch(question, reminder=None):
        rem_seen.append(reminder)
        return "ok"

    floor.update(floor_of("agt_a", "arch", since=time.time() - 700))
    s4 = make_session(floor, "agt_a", rem_arch)
    s4.channels.append(FakeChannel())
    s4.enqueue(msg("已持有超10分钟"))
    check("超时注入 reminder", wait(lambda: len(rem_seen) == 1)
          and rem_seen[0] and "长期持有" in rem_seen[0], str(rem_seen))

    # 新一轮持有（epoch 变化 → 计数/计时重置 → 无 reminder）
    floor.update(floor_of("agt_a", "arch", since=time.time()))
    s5 = make_session(floor, "agt_a", rem_arch)
    s5.channels.append(FakeChannel())
    rem_seen.clear()
    s5.enqueue(msg("刚接手"))
    check("新持有期无 reminder", wait(lambda: len(rem_seen) == 1)
          and rem_seen[0] is None, str(rem_seen))

    # 连续 >3 轮 → reminder
    s6 = make_session(floor, "agt_a", rem_arch)
    s6.channels.append(FakeChannel())
    rem_seen.clear()
    for i in range(4):
        s6.enqueue(msg(f"第{i}轮"))
    check(">3 轮后提醒", wait(lambda: len(rem_seen) == 4)
          and all(r is None for r in rem_seen[:3])
          and rem_seen[3] and "长期持有" in rem_seen[3], str(rem_seen))

    # ------------------------------------- 9. arch 返回 None → 降级文本
    print("\n[9] arch 不可用降级")

    def dead_arch(question, reminder=None):
        return None

    floor.update(floor_of("agt_a", "arch"))
    s7 = make_session(floor, "agt_a", dead_arch)
    ch7 = FakeChannel()
    s7.channels.append(ch7)
    s7.enqueue(msg("在吗"))
    check("降级提示", wait(lambda: any("暂时不可用" in x for x in ch7.sent)),
          str(ch7.sent))

    # ------------------------------------- 10. 无装配（state=None）一切照旧
    print("\n[10] 未装配话语权 → 原路径")
    s8 = AgentSession(session_id="no_floor", memory_view=None, actor=None,
                      pipeline_manager=None, bg_tasks=None, ctx_manager=None)
    s8._start_processing = lambda: None
    s8.channels.append(FakeChannel())
    s8.enqueue(msg("普通消息"))
    check("正常入队", len(s8.pending_items) == 1, str(s8.pending_items))

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
