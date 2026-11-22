"""AgentNet 子系统契约测试（离线 + 本机回环组网）。

覆盖：协议信封/签名/分类、身份生成与验签、MessageBox 分类聚合与展开、
hub 回环（握手白名单/dm/group/mention 分类/离线 mailbox/状态机/非法设备拒绝）。

运行：python tests/test_agentnet_standalone.py
"""

import asyncio
import base64
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from AgentNet.identity import Identity
from AgentNet.messagebox import MessageBox
from AgentNet.protocol import (canonical_bytes, classify, make_envelope, parse,
                               dumps)

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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    root = Path(tempfile.mkdtemp(prefix="agentnet_test_"))
    try:
        # ---------------------------------------------------------- 1. 协议
        print("\n[1] 协议信封")
        env = make_envelope(sender="agt_a", to="agt_b", type_="message",
                            kind="dm", payload={"text": "hi"})
        env2 = parse(dumps(env))
        check("信封往返", env2["id"] == env["id"] and env2["payload"]["text"] == "hi")
        check("分类 dm", classify(env, "agt_b") == "dm")
        g = make_envelope(sender="agt_a", to="grp:x", type_="message", kind="group")
        check("分类 group", classify(g, "agt_b") == "group")
        g["mentions"] = ["agt_b"]
        check("分类 mention", classify(g, "agt_b") == "mention")
        bad = dict(env)
        del bad["ts"]
        try:
            parse(dumps(bad))
            check("缺字段拒绝", False)
        except ValueError:
            check("缺字段拒绝", True)

        # ---------------------------------------------------------- 2. 身份
        print("\n[2] 身份")
        id1 = Identity.load_or_create(root / "a1", name="alpha")
        id1b = Identity.load_or_create(root / "a1")
        check("身份持久化", id1.agent_id == id1b.agent_id)
        data = canonical_bytes(env)
        sig = id1.sign(data)
        check("验签通过", Identity.verify(id1.pubkey_b64, data, sig))
        check("验签拒绝篡改", not Identity.verify(id1.pubkey_b64, data + b"x", sig))
        id2 = Identity.load_or_create(root / "a2", name="beta")
        check("他者公钥验签失败", not Identity.verify(id2.pubkey_b64, data, sig))

        # ---------------------------------------------------------- 3. MessageBox
        print("\n[3] MessageBox")
        box = MessageBox(root / "mb" / "box.db")
        box.add("m1", "group", "grp:主城", "agt_a", "大家在吗")
        box.add("m2", "group", "grp:主城", "agt_b", "在的")
        box.add("m3", "dm", "agt_a", "agt_a", "私聊一下")
        box.add("m4", "mention", "grp:主城", "agt_a", "@agt_x 看这个")
        dup = box.add("m1", "group", "grp:主城", "agt_a", "大家在吗")
        check("幂等去重", dup is False and box.unread_count() == 4)
        s = box.summary()
        check("分类计数", s["group"]["count"] == 2 and s["dm"]["count"] == 1
              and s["mention"]["count"] == 1, str(s))
        digest = box.digest_text()
        check("摘要文本", "@你" in digest and "私聊" in digest and "群消息" in digest, digest)
        rows = box.read(class_="group", peer="grp:主城")
        check("展开未读群消息", len(rows) == 2)
        check("读后已读标记", "group" not in box.summary())
        check("未读剩余 2", box.unread_count() == 2)
        hist = box.read(class_="group", since_ts="2000-01-01", mark_read=False)
        check("时间回溯含已读", len(hist) == 2)

        # ---------------------------------------------------------- 4. 回环组网
        print("\n[4] hub 回环组网")
        asyncio.run(loopback_test(root))

        # ---------------------------------------------------------- 5. channel + 工具
        print("\n[5] AgentNetChannel + serverMessageHandler 接线")
        asyncio.run(channel_test(root))

        # ---------------------------------------------------------- 6. 附件
        print("\n[6] 附件上传/下载/广播")
        asyncio.run(attachment_test(root))

        # ------------------------------------------------- 7. 观察台指令
        print("\n[7] 观察台输入框（owner 身份广播）")
        asyncio.run(send_test(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


async def loopback_test(root: Path):
    """本机双端回环：hub + 两个合法客户端 + 一个非法设备。"""
    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer

    port = free_port()
    id_a = Identity.load_or_create(root / "hub_a", name="alpha")
    id_b = Identity.load_or_create(root / "hub_b", name="beta")
    id_bad = Identity.load_or_create(root / "hub_bad", name="intruder")

    whitelist = {
        "agents": {
            id_a.agent_id: {"name": "alpha", "pubkey": id_a.pubkey_b64},
            id_b.agent_id: {"name": "beta", "pubkey": id_b.pubkey_b64},
        },
        "groups": {"grp:town": {"members": [id_a.agent_id, id_b.agent_id]}},
    }
    wl_path = root / "whitelist.json"
    wl_path.write_text(json.dumps(whitelist), encoding="utf-8")

    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state")
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    urgent_a = []
    urgent_b = []
    box_b = MessageBox(root / "mb_b" / "box.db")
    url = f"ws://127.0.0.1:{port}"

    client_a = AgentNetClient(id_a, url, on_urgent=lambda e, c: urgent_a.append((e, c)),
                              heartbeat_seconds=3600)
    client_b = AgentNetClient(id_b, url, messagebox=box_b,
                              on_urgent=lambda e, c: urgent_b.append((e, c)),
                              heartbeat_seconds=3600)
    ta = asyncio.create_task(client_a.connect_forever())
    tb = asyncio.create_task(client_b.connect_forever())

    async def wait(cond, timeout=5, label=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            await asyncio.sleep(0.05)
        print(f"    [timeout] {label}")
        return False

    try:
        check("双端连接", await wait(lambda: client_a.connected and client_b.connected,
                                     label="connect"))

        # dm：a → b（应入盒 + urgent 唤醒 b）
        await client_a.send_message(id_b.agent_id, "晚上好", kind="dm")
        ok = await wait(lambda: len(urgent_b) == 1, label="dm")
        check("dm 触达 + urgent", ok and urgent_b[0][1] == "dm")
        check("dm 入盒分类", box_b.summary().get("dm", {}).get("count") == 1)

        # group：a → grp:town 无 @（静默入盒不 urgent）
        await client_a.send_message("grp:town", "群公告一条", kind="group")
        ok = await wait(lambda: box_b.summary().get("group", {}).get("count") == 1,
                        label="group")
        check("group 静默入盒", ok and len(urgent_b) == 1)

        # mention：a → grp:town @b（urgent）
        await client_a.send_message("grp:town", "@beta 看这个", kind="group",
                                    mentions=[id_b.agent_id])
        ok = await wait(lambda: len(urgent_b) == 2, label="mention")
        check("mention urgent 唤醒", ok and urgent_b[1][1] == "mention")

        # 状态机：presence 查询
        presence = await client_a.query_presence()
        check("presence 双端在线",
              presence.get(id_a.agent_id, {}).get("online") is True
              and presence.get(id_b.agent_id, {}).get("online") is True,
              str(presence))

        # 离线 mailbox：b 下线 → a 发 dm → b 上线拉取
        await client_b.close()
        await wait(lambda: not hub.presence.snapshot()[id_b.agent_id]["online"],
                   label="offline")
        await client_a.send_message(id_b.agent_id, "离线消息一条", kind="dm")
        await asyncio.sleep(0.3)
        box_b2 = MessageBox(root / "mb_b2" / "box.db")
        urgent_b2 = []
        client_b2 = AgentNetClient(id_b, url, messagebox=box_b2,
                                   on_urgent=lambda e, c: urgent_b2.append((e, c)),
                                   heartbeat_seconds=3600)
        tb2 = asyncio.create_task(client_b2.connect_forever())
        ok = await wait(lambda: len(urgent_b2) == 1, label="mailbox")
        check("离线消息上线拉取", ok and urgent_b2[0][0]["payload"]["text"] == "离线消息一条")

        # 非法设备：不在白名单 → 握手拒绝
        client_bad = AgentNetClient(id_bad, url, heartbeat_seconds=3600)
        tbad = asyncio.create_task(client_bad.connect_forever())
        await asyncio.sleep(1.0)
        check("非白名单设备被拒", not client_bad.connected)

        # last_seen 落盘（b 第一次下线时已记录）
        snap = hub.presence.snapshot()
        check("last_seen 记录", bool(snap.get(id_b.agent_id, {}).get("last_seen")))

        await client_b2.close()
        tbad.cancel()
        tb2.cancel()
    finally:
        ta.cancel()
        tb.cancel()
        serve_task.cancel()
        for t in (ta, tb, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def channel_test(root: Path):
    """channel 全链路：hub + AgentNetChannel(goharu) + 对端 SDK 客户端。

    验证：dm → emit_inbound 唤醒（含标注）；群消息静默入盒；
    摘要便利贴；send_answer 回中继；serverMessageHandler 四动作。
    """
    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer
    from Gateway.channels.agentnet_channel import AgentNetChannel
    from Tools.builtin import agentnet_tools

    port, oport = free_port(), free_port()
    id_g = Identity.load_or_create(root / "ch_goharu", name="goharu")
    id_x = Identity.load_or_create(root / "ch_x", name="x-agent")

    wl_path = root / "whitelist2.json"
    wl_path.write_text(json.dumps({
        "agents": {
            id_g.agent_id: {"name": "goharu", "pubkey": id_g.pubkey_b64},
            id_x.agent_id: {"name": "x-agent", "pubkey": id_x.pubkey_b64},
        },
        "groups": {"grp:town": {"members": [id_g.agent_id, id_x.agent_id]}},
    }), encoding="utf-8")

    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state2")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         file_store=hub.files, whitelist=hub.whitelist)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    inbound = []
    ch = AgentNetChannel("agentnet_test", {
        "url": f"ws://127.0.0.1:{port}",
        "http_base": f"http://127.0.0.1:{oport}",
        "name": "goharu",
        "identity_dir": str(root / "ch_goharu"),
        "messagebox_path": str(root / "ch_box" / "box.db"),
    })
    ch.bind(lambda msg: inbound.append(msg))
    ch.start()
    agentnet_tools.set_agentnet_handle(ch)

    # 对端 SDK 客户端（模拟另一个智能体）
    box_x = MessageBox(root / "ch_box_x" / "box.db")
    received_x = []
    client_x = AgentNetClient(id_x, f"ws://127.0.0.1:{port}", messagebox=box_x,
                              on_urgent=lambda e, c: received_x.append((e, c)),
                              heartbeat_seconds=3600)
    tx = asyncio.create_task(client_x.connect_forever())

    async def wait(cond, timeout=8, label=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            await asyncio.sleep(0.05)
        print(f"    [timeout] {label}")
        return False

    try:
        check("channel 上线", await wait(lambda: ch._connected.is_set(), label="ch online"))
        check("对端上线", await wait(lambda: client_x.connected, label="x online"))

        # dm → emit_inbound（唤醒 turn 的入口）
        await client_x.send_message(id_g.agent_id, "私聊你一下", kind="dm")
        check("dm 唤醒入站", await wait(lambda: len(inbound) == 1, label="dm inbound"))
        if inbound:
            check("入站标注私聊来源", "agentnet 私聊" in inbound[0].text, inbound[0].text)

        # 主人身份识别：owner_ids 内的发送者 → 可信标注 + meta.from_owner
        ch.owner_ids = {id_x.agent_id}
        await client_x.send_message(id_g.agent_id, "我是主人", kind="dm")
        ok = await wait(lambda: len(inbound) == 2, label="owner inbound")
        check("主人消息可信标注", ok and "主人指令" in inbound[1].text
              and inbound[1].meta.get("from_owner") is True,
              inbound[1].text if ok else "")
        ch.owner_ids = set()

        # group 无 @ → 静默入盒，不唤醒
        await client_x.send_message("grp:town", "群里闲聊", kind="group")
        await asyncio.sleep(0.6)
        check("group 不唤醒", len(inbound) == 2)
        check("group 已入盒", ch.box.summary().get("group", {}).get("count") == 1)

        # 摘要便利贴
        extras = ch.turn_extras()
        check("便利贴摘要", extras and "📬" in extras[0], str(extras))

        # 工具：summary / read / reply / presence
        s = json.loads(await agentnet_tools.serverMessageHandler(action="summary"))
        check("工具 summary", s.get("ok") and s["unread"] >= 2, str(s)[:120])
        r = json.loads(await agentnet_tools.serverMessageHandler(
            action="read", class_="group"))
        check("工具 read 展开+标已读", r.get("ok") and r["count"] == 1
              and ch.box.summary().get("group") is None, str(r)[:120])
        p = json.loads(await agentnet_tools.serverMessageHandler(action="presence"))
        check("工具 presence", p.get("ok") and p["agents"].get(id_x.agent_id, {}).get("online"),
              str(p)[:120])
        rp = json.loads(await agentnet_tools.serverMessageHandler(
            action="reply", text="收到你的私聊了", mentions=[id_x.agent_id]))
        check("工具 reply（广播+@）", rp.get("ok"), str(rp))
        check("reply 触达对端", await wait(lambda: len(received_x) == 1, label="reply"))
        if received_x:
            check("reply 内容", received_x[0][0]["payload"]["text"] == "收到你的私聊了")

        # 去重：模型本 turn 已用工具发过 → send_answer 不再中继（不出现两遍）
        incoming_before = len(inbound)
        ch.send_answer("这是回答")
        await asyncio.sleep(0.8)
        check("工具发过后 send_answer 被抑制", len(received_x) == 1
              and len(inbound) == incoming_before,
              f"received={len(received_x)}")

        # 无工具发送时 send_answer 正常中继（恢复目标态后验证）
        ch._reply_target = {"sender": id_x.agent_id}
        ch._agent_replied = False
        ch.send_answer("直接回答")
        check("send_answer 中继", await wait(lambda: len(received_x) == 2, label="relay"))
        if len(received_x) >= 2:
            check("中继内容", received_x[1][0]["payload"]["text"] == "直接回答")
        # 中继后目标已清空：后续无关 turn 不会再广播（防泄漏）
        ch.send_answer("无关回答")
        await asyncio.sleep(0.6)
        check("回复目标用后即清（防私聊答案泄漏到全网）", len(received_x) == 2)

        # 附件发送：net_reply_fut(file_paths) 先上传再随广播送达
        att_file = root / "ch_attach.txt"
        att_file.write_text("channel-attachment", encoding="utf-8")
        fut = ch.net_reply_fut("grp:main", "带附件", kind="group",
                               mentions=[id_x.agent_id], file_paths=[str(att_file)])
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=15)
        ok = await wait(lambda: len(received_x) == 3, label="attachment")
        check("附件消息触达", ok)
        if len(received_x) >= 3:
            atts = received_x[2][0]["payload"].get("attachments") or []
            check("附件元数据透传", len(atts) == 1 and atts[0].get("size") > 0,
                  str(atts))
        # 附件入盒（read 可见）
        row = box_x.read(class_="mention", peer="grp:main")
        rows_with_att = [m for m in box_x.read(since_ts="2000-01-01", mark_read=False)
                         if m.get("attachments")]
        check("附件入盒可读", bool(rows_with_att), str(row)[:120])

        # 中控：owner 动作执行 / 非 owner 拒绝
        ctrl_log = []
        ch.set_control_handler(lambda a: ctrl_log.append(a) or "ok")
        ch.owner_ids = {id_x.agent_id}
        ch._on_control({"from": id_x.agent_id, "payload": {"action": "clear_memory"}})
        check("主人中控动作执行", ctrl_log == ["clear_memory"], str(ctrl_log))
        ch._on_control({"from": "agt_stranger", "payload": {"action": "clear_memory"}})
        check("非主人中控被拒", ctrl_log == ["clear_memory"], str(ctrl_log))
        ch.box.add("ctl1", "group", "grp:town", "agt_a", "x")
        ch._on_control({"from": id_x.agent_id, "payload": {"action": "clear_messagebox"}})
        check("中控清空收件箱", ch.box.unread_count() == 0)
        ch.owner_ids = set()

        # 安静期自动合并阅读：无@群消息攒到安静窗口 → 合并成一条进上下文
        ch.quiet_seconds = 0.5
        ch.set_can_auto_read(lambda: True)
        await client_x.send_message("grp:town", "安静期闲聊一", kind="group")
        await client_x.send_message("grp:town", "安静期闲聊二", kind="group")
        ok = await wait(lambda: ch.box.unread_count(class_="group") >= 2, label="quiet inbox")
        await asyncio.sleep(0.6)  # 超过 quiet_seconds
        inbound_before = len(inbound)
        ch._maybe_auto_read()
        check("安静期合并阅读", ok and len(inbound) > inbound_before
              and "合并阅读" in inbound[-1].text
              and "安静期闲聊一" in inbound[-1].text
              and "安静期闲聊二" in inbound[-1].text,
              inbound[-1].text[:120] if inbound else "")
        check("合并阅读后群消息已读", ch.box.unread_count(class_="group") == 0)
    finally:
        agentnet_tools.set_agentnet_handle(None)
        ch.stop()
        await client_x.close()
        tx.cancel()
        serve_task.cancel()
        for t in (tx, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def attachment_test(root: Path):
    """附件全链路：签名上传 → 下载 → 随广播消息送达 + 观察台可见。"""
    import urllib.parse
    import urllib.request
    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "att_a", name="alpha")
    b = Identity.load_or_create(root / "att_b", name="beta")
    wl_path = root / "wl_att.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64},
                   b.agent_id: {"name": "beta", "pubkey": b.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_att")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         file_store=hub.files, whitelist=hub.whitelist)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    box_b = MessageBox(root / "att_box" / "box.db")
    b_msgs = []
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        http_base=f"http://127.0.0.1:{oport}")
    cb = AgentNetClient(b, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        messagebox=box_b,
                        on_urgent=lambda e, c: b_msgs.append(e))
    ta = asyncio.create_task(ca.connect_forever())
    tb = asyncio.create_task(cb.connect_forever())

    async def wait(cond, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            await asyncio.sleep(0.05)
        return False

    try:
        check("附件双端上线", await wait(lambda: ca.connected and cb.connected))
        src = root / "报告.txt"
        src.write_text("附件内容 payload-123", encoding="utf-8")
        meta = await ca.upload_file(str(src))
        check("签名上传成功", meta.get("id") and meta["size"] == len("附件内容 payload-123".encode()),
              str(meta))
        check("内容寻址 sha", meta["sha256"].startswith(meta["id"][:16]))

        got = await asyncio.to_thread(
            urllib.request.urlopen,
            f"http://127.0.0.1:{oport}{meta['url']}", timeout=5)
        check("下载内容一致", got.read().decode("utf-8") == "附件内容 payload-123")

        # 未签名上传被拒
        try:
            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{oport}/api/files?name=x.txt", data=b"hack", method="POST",
                headers={"X-Agent-Id": "agt_forged", "X-Timestamp": "1", "X-Signature": "x"})
            urllib.request.urlopen(bad_req, timeout=5)
            check("未签名上传被拒", False)
        except urllib.error.HTTPError as e:
            check("未签名上传被拒", e.code == 403, f"code={e.code}")

        # 广播带附件 → beta 收到（@beta 走 urgent）
        await ca.send_message("grp:main", "看附件", kind="group",
                              mentions=[b.agent_id], attachments=[meta])
        check("附件消息触达", await wait(lambda: len(b_msgs) == 1))
        if b_msgs:
            atts = b_msgs[0]["payload"].get("attachments") or []
            check("附件随消息送达", atts and atts[0]["name"] == "报告.txt", str(atts))

        # 观察台消息流水含附件
        def _fetch():
            return json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{oport}/api/messages?limit=5", timeout=5).read())["messages"]
        msgs = await asyncio.to_thread(_fetch)
        check("观察台展示附件", msgs and (msgs[-1]["attachments"] or []), str(msgs[-1] if msgs else ""))
        check("观察台广播标记", msgs and msgs[-1]["recipient"] == "*")
    finally:
        await ca.close()
        await cb.close()
        ta.cancel()
        tb.cancel()
        serve_task.cancel()
        for t in (ta, tb, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def send_test(root: Path):
    """观察台 /api/send：无 token 拒绝；带 token 以 owner 身份广播给全网。"""
    import urllib.error
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "snd_a", name="alpha")
    wl_path = root / "wl_snd.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64}},
        "groups": {}}), encoding="utf-8")

    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_snd")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         token="testtok", file_store=hub.files,
                         whitelist=hub.whitelist, on_send=hub.submit_owner_message)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    received = []
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        on_urgent=lambda e, c: received.append(e))
    ta = asyncio.create_task(ca.connect_forever())

    def post_send(token: str, payload: dict):
        req = urllib.request.Request(
            f"http://127.0.0.1:{oport}/api/send",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, {}

    try:
        deadline = time.time() + 8
        while time.time() < deadline and not ca.connected:
            await asyncio.sleep(0.05)
        check("对端上线", ca.connected)

        status, _ = await asyncio.to_thread(post_send, "", {"text": "无 token"})
        check("无 token 发送被拒", status == 403, str(status))

        status, j = await asyncio.to_thread(
            post_send, "testtok",
            {"text": "观察台指令测试", "mentions": [a.agent_id]})
        check("带 token 发送成功", status == 200 and j.get("ok"), f"{status} {j}")

        deadline = time.time() + 8
        while time.time() < deadline and not received:
            await asyncio.sleep(0.05)
        check("全网收到 owner 广播", bool(received))
        if received:
            env = received[0]
            check("发送者身份为 owner", env.get("from") == hub.owner.agent_id, env.get("from"))
            check("mentions 透传", a.agent_id in (env.get("mentions") or []))
            check("文本正确", env.get("payload", {}).get("text") == "观察台指令测试")

        def get_msgs():
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{oport}/api/messages?limit=10&token=testtok",
                    timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        ms = await asyncio.to_thread(get_msgs)
        owners = [m for m in ms.get("messages", []) if m.get("sender_name") == "haru"]
        check("观察台流水展示 owner 消息", bool(owners), str(ms)[:200])
    finally:
        await ca.close()
        ta.cancel()
        serve_task.cancel()
        for t in (ta, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    sys.exit(main())
