"""QQBot channel 协议契约测试（假传输层，不打真实 QQ API）。

- token：获取 + 缓存 + Authorization "QQBot " 前缀 + expires_in 字符串容错
- WS 握手：HELLO → IDENTIFY（intents/shard/token 形状）→ READY 存 session_id
- 入站：C2C_MESSAGE_CREATE → ChannelMessage（meta 带 scene/user/msg_id）
- 入站群聊：require_mention 过滤 + @ 清洗
- 出站：send_answer → 被动回复带 msg_id + msg_seq 递增 + REST URL 正确
- 长文本 5000 字切分；msg_id 过期降级主动消息
- last_seq 追踪与 RESUME 帧

运行：python tests/test_qqbot_channel.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Gateway.channels.qqbot_channel import (
    QQBotChannel, QQBotClient, INTENT_GROUP_AND_C2C, INTENT_INTERACTION,
)

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


# ── 假 HTTP ──────────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self):
        self.posts = []      # (url, json, headers)
        self.gets = []
        self.token_calls = 0

    async def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json, headers))
        if "getAppAccessToken" in url:
            self.token_calls += 1
            return FakeResponse({"access_token": f"tok_{self.token_calls}",
                                 "expires_in": "7200"})   # 字符串！容错点
        return FakeResponse({"id": "m_out", "timestamp": "t"})

    async def get(self, url, headers=None, **kw):
        self.gets.append((url, headers))
        return FakeResponse({"url": "wss://fake.gateway/ws"})

    async def aclose(self):
        pass


# ── 假 WebSocket ─────────────────────────────────────────────────────
class FakeWS:
    """async context manager + async iterator，回放预置帧，录制发送帧。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def recv(self):
        return self._frames.pop(0)

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def run(coro):
    return asyncio.run(coro)


# ── 1. token 管理与前缀 ──────────────────────────────────────────────
http = FakeHTTP()
client = QQBotClient("app1", "secret1", http=http)
tok1 = run(client.get_token())
tok2 = run(client.get_token())   # 应命中缓存
check(tok1 == "tok_1" and tok2 == "tok_1" and http.token_calls == 1,
      "token 获取+缓存（expires_in 字符串容错）")
headers = run(client._headers())
check(headers["Authorization"] == "QQBot tok_1",
      "Authorization 前缀是 QQBot（非 Bearer）")
check(http.posts[0][1] == {"appId": "app1", "clientSecret": "secret1"},
      "token 请求体形状")

# ── 2. send_text：被动回复 + msg_seq 递增 + URL ─────────────────────
run(client.send_text("c2c", "U123", "你好", msg_id="M1"))
url, body, _ = http.posts[-1]
check(url.endswith("/v2/users/U123/messages"), "C2C endpoint 正确")
check(body["content"] == "你好" and body["msg_type"] == 0 and body["msg_id"] == "M1",
      "被动回复带 msg_id 纯文本")
run(client.send_text("group", "G9", "群回答", msg_id="M1"))
url, body, _ = http.posts[-1]
check("/v2/groups/G9/messages" in url, "群 endpoint 正确")
check(body["msg_seq"] == 2, "同 msg_id 再发 msg_seq 递增（去重）")

# markdown 模式
run(client.send_text("c2c", "U123", "**粗体**", markdown=True))
check(http.posts[-1][1]["msg_type"] == 2
      and http.posts[-1][1]["markdown"]["content"] == "**粗体**",
      "markdown 模式 msg_type=2")

# 长文本切分
long_text = "x" * 12000
run(client.send_text("c2c", "U123", long_text))
u123_posts = [p for p in http.posts if p[0].endswith("/v2/users/U123/messages")]
last3 = u123_posts[-3:]
check(len(last3) == 3 and all(len(p[1].get("content", "")) <= 5000 for p in last3)
      and sum(len(p[1]["content"]) for p in last3) == 12000,
      "12000 字切成 3 段（≤5000）")

# ── 3. typing ────────────────────────────────────────────────────────
run(client.send_typing("U123"))
check(http.posts[-1][1]["msg_type"] == 6
      and http.posts[-1][1]["input_notify"]["input_type"] == 1,
      "typing 指示 msg_type=6")

# ── 4. WS 握手 + 入站 + 出站全链路 ──────────────────────────────────
frames_in = [
    {"op": 10, "d": {"heartbeat_interval": 41250}},                     # HELLO
    {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "sess_abc"}},   # READY
    {"op": 0, "s": 2, "t": "C2C_MESSAGE_CREATE", "d": {
        "id": "M100", "content": "你好机器人",
        "author": {"user_openid": "USER_A"}}},
    {"op": 0, "s": 3, "t": "GROUP_AT_MESSAGE_CREATE", "d": {
        "id": "M200", "group_openid": "GRP_1",
        "content": "<@!BOTID> 在吗",
        "author": {"member_openid": "MEMBER_B"},
        "mentions": [{"is_you": True, "member_openid": "BOTID"}]}},
    {"op": 0, "s": 4, "t": "GROUP_AT_MESSAGE_CREATE", "d": {
        "id": "M201", "group_openid": "GRP_1",
        "content": "没@机器人的群消息",
        "author": {"member_openid": "MEMBER_C"},
        "mentions": []}},
]

ws = FakeWS([json.dumps(f) for f in frames_in])
http2 = FakeHTTP()
ch = QQBotChannel("qq1", {"app_id": "a", "app_secret": "s"})
ch.client._http = http2
ch._ws_factory = lambda url: ws

received = []
ch.bind(received.append)

# 直接跑一轮 _connect_once（不走重连循环）
run(ch._connect_once())

check(ch._session_id == "sess_abc", "READY 后保存 session_id")
check(ch._last_seq == 4, "last_seq 追踪到最后一帧")
identify = ws.sent[0]
check(identify["op"] == 2 and identify["d"]["token"] == "QQBot tok_1"
      and identify["d"]["intents"] == INTENT_GROUP_AND_C2C | INTENT_INTERACTION
      and identify["d"]["shard"] == [0, 1],
      "IDENTIFY 帧形状（token/intents/shard）")

check(len(received) == 2, f"入站 2 条（C2C + @群消息；未@的过滤）实际 {len(received)}")
c2c_msg, grp_msg = received
check(c2c_msg.priority == "high", "QQ 入站默认 priority=high（打断+插队）")
check(c2c_msg.text == "你好机器人" and c2c_msg.channel_type == "qqbot"
      and c2c_msg.meta["scene"] == "c2c" and c2c_msg.meta["user"] == "USER_A"
      and c2c_msg.meta["msg_id"] == "M100",
      "C2C 入站 → ChannelMessage（meta 完整）")
check(grp_msg.text == "在吗" and grp_msg.meta["scene"] == "group"
      and grp_msg.meta["group"] == "GRP_1",
      "群 @ 消息清洗 @ 标记后上送")

# 出站：最近活跃会话是 GRP_1（群），应回复到群并带 M200 被动 msg_id
ch._loop = asyncio.new_event_loop()
ch.send_answer("这是回答")
ch._loop.run_until_complete(asyncio.sleep(0.05))
url, body, _ = http2.posts[-1]
check("/v2/groups/GRP_1/messages" in url and body["msg_id"] == "M200",
      "send_answer 路由到最近活跃会话（群）+ 被动 msg_id")

# msg_id 过期 → 降级主动
ch._last_conversation["ts"] = time.time() - 400   # 群 TTL 300s
ch.send_answer("迟到的回答")
ch._loop.run_until_complete(asyncio.sleep(0.05))
check("msg_id" not in http2.posts[-1][1],
      "msg_id 过期降级主动消息（不带 msg_id）")

# typing：C2C 场景发 msg_type=6
ch._last_conversation = {"scene": "c2c", "id": "USER_A",
                         "msg_id": "M100", "ts": time.time()}
ch.send_thinking(None)
ch._loop.run_until_complete(asyncio.sleep(0.05))
check(http2.posts[-1][1]["msg_type"] == 6, "send_thinking(None) → C2C typing")

# RESUME：有 session_id + last_seq 时第二帧发 op=6
ws2 = FakeWS([json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}),
              json.dumps({"op": 0, "s": 5, "t": "RESUMED", "d": {}})])
ch._ws_factory = lambda url: ws2
run(ch._connect_once())
check(ws2.sent[0]["op"] == 6 and ws2.sent[0]["d"]["session_id"] == "sess_abc"
      and ws2.sent[0]["d"]["seq"] == 4,
      "重连走 RESUME（session_id + last_seq）")

ch._loop.close()


# ── 5. markdown 自适应与去语法降级 ──────────────────────────────────
from Gateway.channels.qqbot_channel import strip_markdown

md = strip_markdown('# 标题' + chr(10) + '**粗体** 和 *斜体* 与 `code`' + chr(10) + '- 项目一' + chr(10) + '[链接文字](http://x)')
check('标题' in md and '**' not in md and '*' not in md and '`' not in md
      and '· 项目一' in md and '链接文字' in md,
      'strip_markdown 去语法保留内容')

class RejectMDHTTP(FakeHTTP):
    async def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json, headers))
        if 'getAppAccessToken' in url:
            self.token_calls += 1
            return FakeResponse({'access_token': f'tok_{self.token_calls}', 'expires_in': '7200'})
        if isinstance(json, dict) and json.get('msg_type') == 2:
            return FakeResponse({'code': 12003, 'message': 'markdown not allowed'})
        return FakeResponse({'id': 'm', 'timestamp': 't'})

http_md = RejectMDHTTP()
client_md = QQBotClient('a', 's', http=http_md)
run(client_md.send_text('c2c', 'U9', '# 你好' + chr(10) + '**世界**', markdown='auto'))
fallback = http_md.posts[-1][1]
check(fallback['msg_type'] == 0 and '你好' in fallback['content']
      and '**' not in fallback['content'],
      'auto 模式：原生 md 被拒 → 自动降级去语法纯文本')

# ── 6. answer 的工具摘要前缀 ────────────────────────────────────────
ch_sum = QQBotChannel('qq_sum', {})
ch_sum.client._http = FakeHTTP()
ch_sum._loop = asyncio.new_event_loop()
ch_sum._last_conversation = {'scene': 'c2c', 'id': 'US', 'msg_id': 'MS',
                             'ts': time.time()}
ch_sum.send_answer('最终答案', meta={'tool_summary': 'Read×2、run_cmd×1'})
ch_sum._loop.run_until_complete(asyncio.sleep(0.05))
body = [p2 for p2 in ch_sum.client._http.posts if p2[0].endswith('/messages')][-1][1]
sent_text = body.get('content') or body.get('markdown', {}).get('content', '')
check(sent_text.startswith('🔧 工具：Read×2、run_cmd×1')
      and sent_text.endswith('最终答案'),
      'answer 前缀工具摘要行（不单独发消息）')
ch_sum._loop.close()

# ── 7. text_output 能力（中间文本块转发）────────────────────────────
ch_t = QQBotChannel('qq_t2', {})
check('text_output' in ch_t.capabilities, 'QQBot 声明 text_output 能力')

# ── 8. thinking 文本转发 + 被动拒绝降级主动 ──
class RejectPassiveHTTP(FakeHTTP):
    async def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json))
        if 'getAppAccessToken' in url:
            return FakeResponse({'access_token': 'tok', 'expires_in': '7200'})
        if isinstance(json, dict) and json.get('msg_id'):
            return FakeResponse({'code': 40034, 'message': 'reply quota'})
        return FakeResponse({'id': 'ok', 'timestamp': 't'})

ch_rp = QQBotChannel('qq_rp', {})
ch_rp.client._http = RejectPassiveHTTP()
ch_rp._loop = asyncio.new_event_loop()
ch_rp._last_conversation = {'scene': 'c2c', 'id': 'UR', 'msg_id': 'MR', 'ts': time.time()}
ch_rp.send_answer('答案')
ch_rp._loop.run_until_complete(asyncio.sleep(0.05))
last_two = [p2[1] for p2 in ch_rp.client._http.posts if p2[0].endswith('/messages')]
check(last_two and 'msg_id' in last_two[-2] and 'msg_id' not in last_two[-1],
      '被动回复被拒 → 自动降级主动重发')
ch_rp._loop.close()

# ── 9. bot 名字（/users/@me）+ health 附加字段 ──────────────────────
import os as _os

class MeHTTP(FakeHTTP):
    async def get(self, url, headers=None, **kw):
        if url.endswith("/users/@me"):
            self.gets.append((url, headers))
            return FakeResponse({"id": "bot1", "username": "妃下"})
        return await super().get(url, headers=headers, **kw)

ch5 = QQBotChannel("qq5", {"app_id": "a5", "app_secret": "s5"})
ch5.client._http = MeHTTP()
ws5 = FakeWS([json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}),
              json.dumps({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "s5"}})])
ch5._ws_factory = lambda url: ws5
run(ch5._connect_once())
check(ch5._bot_name == "妃下", "READY 后自动取 /users/@me 机器人名")
h5 = ch5.health()
check(h5["bot_name"] == "妃下" and h5["app_id"] == "a5"
      and h5["has_secret"] is True and h5["state"] == "online",
      "health 携带 bot_name/app_id/has_secret")

# get_me 失败不阻断（返回 {}，名字留空）
class BoomHTTP(FakeHTTP):
    async def get(self, url, headers=None, **kw):
        if url.endswith("/users/@me"):
            raise RuntimeError("network down")
        return await super().get(url, headers=headers, **kw)

ch6 = QQBotChannel("qq6", {"app_id": "a", "app_secret": "s"})
ch6.client._http = BoomHTTP()
ws6 = FakeWS([json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}),
              json.dumps({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "s6"}})])
ch6._ws_factory = lambda url: ws6
run(ch6._connect_once())
check(ch6._session_id == "s6" and ch6._bot_name == "",
      "get_me 失败不阻断 READY（名字留空）")

# ── 10. set_credentials：凭据热更新 ─────────────────────────────────
_saved_env = {k: _os.environ.get(k) for k in ("QQBOT_APP_ID", "QQBOT_CLIENT_SECRET")}
try:
    # 运行中通道：换 client + 清 session + 关旧 ws 触发重握手 + 写回 .env
    loop10 = asyncio.new_event_loop()
    import threading as _th
    t10 = _th.Thread(target=loop10.run_forever, daemon=True)
    t10.start()

    class WSClose:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class AliveThread:
        def is_alive(self):
            return True

    ch7 = QQBotChannel("qq7", {"app_id": "old_app", "app_secret": "old_sec"})
    ch7._loop = loop10
    ch7._thread = AliveThread()
    ch7._ws = WSClose()
    ch7._session_id = "sess_old"
    written = []
    ch7.set_env_writer(lambda k, v: written.append((k, v)))
    ch7.set_credentials("new_app", "new_sec")
    time.sleep(0.15)
    check(ch7.app_id == "new_app" and ch7.app_secret == "new_sec"
          and ch7.client.app_id == "new_app" and ch7._session_id is None
          and ch7._bot_name == "",
          "set_credentials 换 client + 清 session/名字")
    check(ch7._ws.closed, "旧 ws 被关闭（触发新凭据重握手）")
    check(("QQBOT_APP_ID", "new_app") in written
          and ("QQBOT_CLIENT_SECRET", "new_sec") in written,
          "凭据写回 .env（注入 writer）")
    check(_os.environ.get("QQBOT_APP_ID") == "new_app"
          and _os.environ.get("QQBOT_CLIENT_SECRET") == "new_sec",
          "os.environ 同步更新")
    loop10.call_soon_threadsafe(loop10.stop)
    t10.join(timeout=2)

    # secret 留空 = 保持不变；未启动通道 → 直接 start()
    ch8 = QQBotChannel("qq8", {"app_id": "a8", "app_secret": "s8"})
    started8 = []
    ch8.start = lambda: started8.append(1)
    ch8.set_credentials("a8_new", "")
    check(ch8.app_id == "a8_new" and ch8.app_secret == "s8",
          "secret 留空保持不变")
    check(started8 == [1], "未启动通道更新凭据后直接 start()")

    # 空 AppID 拒绝
    try:
        ch8.set_credentials("", "x")
        check(False, "空 AppID 应抛 ValueError")
    except ValueError:
        check(True, "空 AppID 应抛 ValueError")
finally:
    for k, v in _saved_env.items():
        if v is None:
            _os.environ.pop(k, None)
        else:
            _os.environ[k] = v

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
