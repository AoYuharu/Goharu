"""Channel 提问三范式契约测试：基类文本退化 / QQ 原生按钮 / 答案拦截。

覆盖：
- 基类 ask_single/ask_multi/ask_text/批量：文本渲染 + 下一条消息解析为答案
  （编号→label、多选逗号、批量分号、自由文本、q 取消、格式错误重提示、TTL）
- 答案契约：单问单选=label 字符串；含多选或批量=JSON {"answers":[...]}
- QQBot：单选/多选原生 keyboard 构造、INTERACTION_CREATE 回调状态机、
  填空/批量退化文本
- session.enqueue：ask 挂起时消息被拦截不进队列；无挂起正常入队

运行：python tests/test_channel_ask.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Gateway.channels.base import Channel
from Gateway.channels.qqbot_channel import QQBotChannel
from Gateway.channels.registry import register_channel

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


class RecordChannel(Channel):
    """纯文本退化的测试 channel（不声明任何 ask 能力——全走基类）。"""

    channel_type = "record"

    def __init__(self, channel_id, params=None):
        super().__init__(channel_id, params)
        self.sent = []
        self.resolved = []

    def send_answer(self, text, meta=None):
        self.sent.append(str(text))

    def bind_answer(self, resolve_fn):
        self._resolve_answer = resolve_fn

    def _record_resolve(self, request_id, answer):
        self.resolved.append((request_id, answer))

    def inbound(self, text):
        """模拟用户回复。"""
        from Gateway.channels.base import ChannelMessage
        return self.try_consume_as_answer(ChannelMessage(
            text=text, channel_type=self.channel_type, channel_id=self.channel_id))


ch = RecordChannel("rec1")
ch.bind_answer(ch._record_resolve)

# ── 1. 单选文本退化 ──────────────────────────────────────────────
ch.ask_questions("r1", [{"question": "喝什么", "options": [
    {"label": "拿铁"}, {"label": "美式"}, {"label": "手冲"}]}])
check(any("1. 拿铁" in s for s in ch.sent), "单选渲染编号列表")
check(ch.inbound("2") is True, "编号回复被拦截消费")
check(ch.resolved and ch.resolved[-1] == ("r1", "美式"), "编号→label 解析并回送")
check(ch.inbound("随便") is False, "无挂起时消息正常放行")

# 自由文本作答单选
ch.ask_questions("r2", [{"question": "去哪", "options": [{"label": "北京"}]}])
ch.inbound("我想去上海")
check(ch.resolved[-1] == ("r2", "我想去上海"), "未匹配选项→自由文本作答")

# ── 2. 多选（批量契约）──────────────────────────────────────────
ch.ask_questions("r3", [{"question": " toppings", "multi_select": True, "options": [
    {"label": "奶泡"}, {"label": "糖"}, {"label": "肉桂"}]}])
ch.inbound("1,3")
rid, ans = ch.resolved[-1]
parsed = json.loads(ans)
check(rid == "r3" and parsed["answers"][0]["answer"] == ["奶泡", "肉桂"],
      "多选逗号解析→JSON 批量契约")

# ── 3. 填空 ──────────────────────────────────────────────────────
ch.ask_questions("r4", [{"question": "你的名字"}])
ch.inbound("黄师傅")
check(ch.resolved[-1] == ("r4", "黄师傅"), "填空：下一条消息即答案")

# ── 4. 批量（分号分隔）──────────────────────────────────────────
ch.ask_questions("r5", [
    {"question": "单", "options": [{"label": "A"}, {"label": "B"}]},
    {"question": "多", "multi_select": True, "options": [{"label": "X"}, {"label": "Y"}]},
    {"question": "空"},
])
ok = ch.inbound("1; 2; 你好")
rid, ans = ch.resolved[-1]
parsed = json.loads(ans)
check(ok and [a["answer"] for a in parsed["answers"]] == ["A", ["Y"], "你好"],
      "批量三题分号解析（单选/多选/填空混合）")

# ── 5. q 取消 / 格式错误 / TTL ──────────────────────────────────
ch.ask_questions("r6", [{"question": "q", "options": [{"label": "A"}, {"label": "B"}]}])
ch.inbound("q")
check(ch.resolved[-1] == ("r6", None), "q 取消→答案为 None")

ch.ask_questions("r7", [
    {"question": "1", "options": [{"label": "A"}]},
    {"question": "2", "options": [{"label": "B"}]},
])
n_sent = len(ch.sent)
consumed = ch.inbound("只有一段")
check(consumed is True and len(ch.sent) == n_sent + 1
      and "没看懂" in ch.sent[-1] and not any(r[0] == "r7" for r in ch.resolved),
      "批量格式错误：消息被消费+格式提示重发，不误送答案")

ch.ask_questions("r8", [{"question": "过期", "options": [{"label": "A"}]}])
ch._ask_pending["expires"] = time.time() - 1
check(ch.inbound("1") is False, "TTL 过期后不再拦截")

# ── 6. QQBot 原生单选按钮 ───────────────────────────────────────
sent_api = []

class FakeQQClient:
    async def send_with_keyboard(self, scene, target, text, keyboard, msg_id=None):
        sent_api.append({"scene": scene, "target": target, "text": text,
                         "keyboard": keyboard, "msg_id": msg_id})
        return [{"id": "out1"}]

    async def acknowledge_interaction(self, iid, code=0):
        sent_api.append({"ack": iid, "code": code})

qq = QQBotChannel("qq_t", {})
qq.client = FakeQQClient()
qq._loop = asyncio.new_event_loop()
qq._last_conversation = {"scene": "c2c", "id": "U1", "msg_id": "M1",
                         "ts": time.time()}
qq.bind_answer(lambda rid, ans: qq.__dict__.setdefault("_ans", []).append((rid, ans)))

qq.ask_questions("q1", [{"question": "选一个", "options": [
    {"label": "苹果"}, {"label": "香蕉"}, {"label": "梨"}]}])
qq._loop.run_until_complete(asyncio.sleep(0.05))
kb = next((s for s in sent_api if "keyboard" in s), None)
check(kb is not None and kb["msg_id"] == "M1", "QQ 单选：keyboard 消息走被动 msg_id")
btns = kb["keyboard"]["content"]["rows"][0]["buttons"] if kb else []
check(len(btns) == 3 and btns[0]["action"]["data"] == "askuser:q1:1"
      and btns[0]["action"]["click_limit"] == 1
      and btns[1]["group_id"] == btns[0]["group_id"],
      "QQ 单选按钮形状（data 编码/限点一次/同组互斥）")

# 模拟点击第 2 个按钮的 INTERACTION_CREATE
qq._on_interaction({"id": "ix1", "data": {"resolved": {
    "button_data": "askuser:q1:2", "button_id": "opt2"}}})
qq._loop.run_until_complete(asyncio.sleep(0.05))
check(qq._ans[-1] == ("q1", "香蕉"), "按钮回调→resolve（label 契约）")
check(any(s.get("ack") == "ix1" for s in sent_api), "交互已 ack")

# ── 7. QQBot 原生多选状态机 ─────────────────────────────────────
sent_api.clear()
qq.ask_questions("q2", [{"question": "选多个", "multi_select": True, "options": [
    {"label": "红"}, {"label": "绿"}, {"label": "蓝"}]}])
qq._loop.run_until_complete(asyncio.sleep(0.05))
kb = next((s for s in sent_api if "keyboard" in s), None)
rows = kb["keyboard"]["content"]["rows"] if kb else []
check(rows and rows[-1]["buttons"][0]["id"] == "done"
      and rows[-1]["buttons"][0]["action"]["data"] == "askuser:q2:done"
      and rows[0]["buttons"][0]["action"]["data"] == "askuser:q2:t:1",
      "QQ 多选：选项 toggle 按钮 + 提交/重选行")

for i in (1, 3, 1):   # 勾选 红、蓝，再取消 红
    qq._on_interaction({"id": f"ix{i}", "data": {"resolved": {
        "button_data": f"askuser:q2:t:{i}", "button_id": f"opt{i}"}}})
qq._on_interaction({"id": "ixd", "data": {"resolved": {
    "button_data": "askuser:q2:done", "button_id": "done"}}})
qq._loop.run_until_complete(asyncio.sleep(0.05))
rid, ans = qq._ans[-1]
check(rid == "q2" and json.loads(ans)["answers"][0]["answer"] == ["蓝"],
      "多选 toggle（含取消勾选）→提交→JSON 契约")

# 空选提交被拒
qq.ask_questions("q3", [{"question": "m", "multi_select": True,
                         "options": [{"label": "A"}]}])
qq._loop.run_until_complete(asyncio.sleep(0.05))
qq._on_interaction({"id": "ixd3", "data": {"resolved": {
    "button_data": "askuser:q3:done", "button_id": "done"}}})
qq._loop.run_until_complete(asyncio.sleep(0.05))
check(not any(r[0] == "q3" for r in qq._ans), "空选不允许提交")

# ── 8. QQBot 填空/批量退化文本 ──────────────────────────────────
sent_api.clear()
qq.sent_probe = []
qq.send_answer = lambda text, meta=None: qq.sent_probe.append(text)
qq.ask_questions("q4", [{"question": "名字"}])
check(any("请直接回复" in s for s in qq.sent_probe), "QQ 填空退化文本")
qq.ask_questions("q5", [{"question": "a", "options": [{"label": "1"}]},
                        {"question": "b", "options": [{"label": "2"}]}])
check(any("问题 1" in s for s in qq.sent_probe), "QQ 批量退化文本")

# ── 9. enqueue 拦截 ──────────────────────────────────────────────
from Gateway.session import AgentSession
from Gateway.session_memory import SessionMemoryView
from Memory.bridge import create_memory
from Prompting.api import create_context_manager
import tempfile

class FakeActor:
    async def act(self, **kw):
        return {"type": "answer", "answer": "x"}
    def reset_retrieval_cache(self): pass
    def get_last_usage(self):
        return {}
    def build_messages_with_document(self, extra=None):
        return {"messages": []}
    def prepare_messages_for_provider(self, m):
        return None

class FakeBg:
    reactivation_count = 0
    def drain_pending(self):
        return []
    def has_pending(self):
        return False
    def inject_into_memory(self, *a):
        pass

TMP = Path(tempfile.mkdtemp(prefix="ask_test_"))
bridge = create_memory(overrides={
    "memory.root_dir": str(TMP / "m"),
    "memory.retrieval.embedding_model_path": str(TMP / "empty"),
    "memory.retrieval.use_reranker": False,
})
view = SessionMemoryView("ask_t", bridge, max_messages=10)
sess = AgentSession("ask_t", view, FakeActor(), None, FakeBg(),
                    create_context_manager())
rec = RecordChannel("rec1")
sess.attach_channel(rec)
rec.bind(sess.enqueue)
rec.bind_answer(rec._record_resolve)

rec.ask_questions("r10", [{"question": "选", "options": [{"label": "甲"}, {"label": "乙"}]}])
rec.emit_inbound("2")     # 用户在 channel 上回复
check(not sess.has_pending(), "ask 挂起时回复被 enqueue 拦截（不进队列）")
check(rec.resolved[-1] == ("r10", "乙"), "拦截后答案正确回送")
# 暂停处理驱动，避免竞态消费掉待断言的队列项
_start_orig = sess._start_processing
sess._start_processing = lambda: None
rec.emit_inbound("普通消息")
check(sess.has_pending(), "无挂起时消息正常入队")
sess.drain_pending_items()
sess._start_processing = _start_orig
bridge.close()
qq._loop.close()

# ── 10. 高危命令确认：基类文本 y/n 退化 + QQ 按钮 ──
ch_conf = RecordChannel('conf1')
_conf_resolved = []
ch_conf.bind_confirm(lambda cid, ok: _conf_resolved.append((cid, ok)))
ch_conf.confirm_request('C1', 'rm -rf build/', '清理构建目录', timeout=60)
check(any('高危' in s for s in ch_conf.sent), '基类确认请求渲染文本提示')
check(ch_conf.try_consume_as_confirmation(type('M', (), {'text': '随便聊聊'})() ) is False,
      '非 y/n 消息不消费')
check(ch_conf.try_consume_as_confirmation(type('M', (), {'text': 'y'})() ) is True,
      'y 消息被消费为确认')
check(_conf_resolved[-1] == ('C1', True), '确认放行回调（approved=True）')
ch_conf.confirm_request('C2', 'x', '', timeout=60)
ch_conf.try_consume_as_confirmation(type('M', (), {'text': '拒绝'})())
check(_conf_resolved[-1] == ('C2', False), '拒绝回调（approved=False）')
check(ch_conf.try_consume_as_confirmation(type('M', (), {'text': 'y'})() ) is False,
      '无挂起确认时不消费')

# ── 11. QQBot 高危确认按钮 ──
sent_api.clear()
qq_conf_resolved = []
qq.bind_confirm(lambda cid, ok: qq_conf_resolved.append((cid, ok)))
qq._loop2 = asyncio.new_event_loop()
qq._loop.close()
qq._loop = qq._loop2
qq.confirm_request('cf1', 'rm -rf build/', '清理构建目录', timeout=60)
qq._loop.run_until_complete(asyncio.sleep(0.05))
kb = next((s for s in sent_api if 'keyboard' in s), None)
btns = kb['keyboard']['content']['rows'][0]['buttons'] if kb else []
check(kb is not None and btns[0]['action']['data'] == 'confirm:cf1:1'
      and btns[1]['action']['data'] == 'confirm:cf1:0',
      'QQ 确认按钮形状（✅/❌ 回调 data）')
qq._on_interaction({'id': 'ixc1', 'data': {'resolved': {
    'button_data': 'confirm:cf1:1', 'button_id': 'allow'}}})
qq._loop.run_until_complete(asyncio.sleep(0.05))
check(qq_conf_resolved[-1] == ('cf1', True), '确认按钮回调 → approved=True')
qq._loop.close()

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
