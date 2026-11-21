"""SendFile 契约测试：Channel 接口 / QQ 两段式上传 / 工具层路由。

- 基类 send_file 默认 False（不支持）
- QQBot send_file：本机文件（≤5MB base64）与公网 URL 两段式（/files → msg_type=7）
- 文件不存在 / 超限 → False
- 工具层：注册的 channel 可发；未注册给说明性错误；registry.dispatch 全链路

运行：python tests/test_channel_sendfile.py
"""

import asyncio
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Gateway.channels.base import Channel
from Gateway.channels.qqbot_channel import QQBotChannel, QQBotClient

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


# ── 1. 基类默认不支持 ──────────────────────────────────────────────
class PlainChannel(Channel):
    channel_type = "plain"

    def send_answer(self, text, meta=None):
        pass


check(PlainChannel("p1").send_file("/tmp/x.txt") is False, "基类 send_file 默认 False")

# ── 2. QQ 两段式：本机文件 ────────────────────────────────────────
class FakeHTTP:
    def __init__(self):
        self.posts = []

    async def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json))
        if "getAppAccessToken" in url:
            return _Resp({"access_token": "tok", "expires_in": "7200"})
        if url.endswith("/files"):
            return _Resp({"file_uuid": "f1", "file_info": "FILE_INFO_X", "ttl": 3600})
        return _Resp({"id": "m1", "timestamp": "t"})

    async def aclose(self):
        pass


class _Resp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._d


TMP = Path(tempfile.mkdtemp(prefix="sendfile_test_"))
img = TMP / "photo.png"
img.write_bytes(b"\x89PNG fake data" * 10)

ch = QQBotChannel("qq_f", {})
ch.client._http = FakeHTTP()
ch._loop = asyncio.new_event_loop()
ch._last_conversation = {"scene": "c2c", "id": "UF", "msg_id": "MF",
                         "ts": time.time()}

ok = ch.send_file(str(img), caption="看这张图")
ch._loop.run_until_complete(asyncio.sleep(0.05))
check(ok is True, "本机文件 send_file 受理 True")
up = next(p for p in ch.client._http.posts if p[0].endswith("/files"))
check(up[1]["file_type"] == 1 and up[1]["srv_send_msg"] is False
      and up[1]["file_name"] == "photo.png"
      and up[1]["file_data"] == base64.b64encode(img.read_bytes()).decode(),
      "/files 上传体：file_type 推断+base64+文件名")
msg = [p for p in ch.client._http.posts if p[0].endswith("/messages")][-1]
check(msg[1]["msg_type"] == 7 and msg[1]["media"]["file_info"] == "FILE_INFO_X"
      and msg[1]["content"] == "看这张图" and msg[1]["msg_id"] == "MF",
      "msg_type=7 携带 file_info+caption+被动 msg_id")

# ── 3. URL 直传 ───────────────────────────────────────────────────
ch.client._http.posts.clear()
ok = ch.send_file("https://example.com/report.pdf")
ch._loop.run_until_complete(asyncio.sleep(0.05))
up = next(p for p in ch.client._http.posts if p[0].endswith("/files"))
check(ok and up[1]["url"] == "https://example.com/report.pdf"
      and up[1]["file_type"] == 4 and "file_data" not in up[1],
      "公网 URL：平台拉取模式，未知扩展名按 file_type=4")

# ── 4. 失败路径 ───────────────────────────────────────────────────
check(ch.send_file(str(TMP / "不存在.txt")) is False, "文件不存在 → False")
big = TMP / "big.bin"
big.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
check(ch.send_file(str(big)) is False, "超 5MB → False（base64 直传上限）")

# ── 5. 工具层路由 ─────────────────────────────────────────────────
from Tools.builtin import channel_tools
from Tools.registry import registry

# 未注册时给说明性错误
r1 = json.loads(channel_tools.send_file("qqbot", str(img)))
check(r1["status"] == "error" and "不可用" in r1["error"],
      "未注册 channel 给说明性错误")

def _adapter(path_or_url, caption="", channel_id=None, file_type=None):
    ok = ch.send_file(path_or_url, caption, force_type=file_type)
    return ok, "已受理发送" if ok else "失败"

channel_tools.register_channel_sender("qqbot", _adapter)
r2 = json.loads(channel_tools.send_file("qqbot", str(img), caption="via tool"))
check(r2["status"] == "success", "注册后工具发送成功")
r3 = json.loads(channel_tools.send_file("qqbot", str(TMP / "nope.txt")))
check(r3["status"] == "error", "工具层透传失败状态")

# registry.dispatch 全链路（走 schema 校验）
async def _dispatch():
    return await registry.dispatch("SendFile", {
        "channel": "qqbot", "path": str(img), "caption": "dispatch 链路"})

r4 = json.loads(asyncio.run(_dispatch()))
check(r4["status"] == "success", "registry.dispatch('SendFile') 全链路")

# ── 6. 类型推断：只认图片/视频，其余（含 mp3）一律按文件 ──
ch._loop.run_until_complete(asyncio.sleep(0.1))   # 先排空此前调度残留
ch.client._http.posts.clear()
ok = ch.send_file('https://example.com/a.mp3')
ch._loop.run_until_complete(asyncio.sleep(0.05))
up = [p_ for p_ in ch.client._http.posts if p_[0].endswith('/files')][-1]
check(ok and up[1]['file_type'] == 4, 'mp3 默认按文件发送（不再发语音）')
ch.client._http.posts.clear()
ok = ch.send_file(str(img), caption='force', force_type='file')
ch._loop.run_until_complete(asyncio.sleep(0.05))
up = [p_ for p_ in ch.client._http.posts if p_[0].endswith('/files')][-1]
check(ok and up[1]['file_type'] == 4, 'force_type=file 覆盖扩展名推断')

ch._loop.close()

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
