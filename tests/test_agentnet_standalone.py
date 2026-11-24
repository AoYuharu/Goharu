"""AgentNet 子系统契约测试（离线 + 本机回环组网）。

覆盖：协议信封/签名/分类、身份生成与验签、MessageBox 分类聚合与展开、
hub 回环（握手白名单/dm/group/mention 分类/离线 mailbox/状态机/非法设备拒绝）。

运行：python tests/test_agentnet_standalone.py
"""

import asyncio
import base64
import json
import os
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
        g["mentions"] = ["*"]
        check("分类 @全员", classify(g, "agt_anyone") == "mention")
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

        # ------------------------------------------------- 8. 待答卡片区
        print("\n[8] AskUser/高危确认观察台链路")
        asyncio.run(ask_test(root))

        # ------------------------------------------------- 9. 大附件流式上传
        print("\n[9] >25MB 附件流式上传（2GB 上限）")
        asyncio.run(big_upload_test(root))

        # ------------------------------------------------- 10. 页面静态标记
        print("\n[10] 观察台页面 Phase3 标记")
        page = (Path(__file__).resolve().parent.parent
                / "AgentNet" / "server" / "observer_page.html").read_text(encoding="utf-8")
        check("页面含待答卡片区", "askDock" in page and "/api/requests" in page)
        check("页面含确认卡片", "confirm_result" in page or "高危命令" in page)
        check("页面含键盘焦点守卫", "activeElement" in page)
        check("页面含安全策略区", "buildSecuritySection" in page
              and "action: 'security'" in page)
        check("feed 重置保留 askDock", "function clearFeed()" in page
              and "feed.innerHTML = ''" not in page)

        # ------------------------------------------- 11. 安全规则中控链路
        print("\n[11] 安全策略观察台链路")
        asyncio.run(security_control_test(root))

        # ------------------------------------------------- 12. security 单测
        print("\n[12] CommandSecurityChecker.apply_rules")
        security_unit_test()

        # ------------------------------------------------- 13. 网络话语权
        print("\n[13] 网络话语权（hub 广播/观察台/掉线收回）")
        asyncio.run(floor_test(root))

        # ------------------------------------------- 14. 接入边界（握手/退避）
        print("\n[14] 接入边界：两段式握手 + 白名单拒绝长退避")
        asyncio.run(handshake_test(root))

        # ------------------------------------------- 15. 可靠性修复回归
        print("\n[15] 可靠性：mailbox/共享状态/重连/生命周期")
        asyncio.run(reliability_test(root))

        # ----------------------------------- 16. 接入密码设置 / 删除智能体
        print("\n[16] 接入密码运行时设置 + 删除已注册智能体")
        asyncio.run(admin_test(root))
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
            check("入站为合并整理格式", "HaruNet 组网消息整理" in inbound[0].text
                  and id_x.agent_id in inbound[0].text, inbound[0].text)

        # 主人身份识别：owner_ids 内的发送者 → 可信标注 + meta.from_owner
        ch.owner_ids = {id_x.agent_id}
        await client_x.send_message(id_g.agent_id, "我是主人", kind="dm")
        ok = await wait(lambda: len(inbound) == 2, label="owner inbound")
        check("主人消息可信标注", ok and "主人" in inbound[1].text
              and inbound[1].meta.get("from_owner") is True,
              inbound[1].text if ok else "")
        ch.owner_ids = set()

        # 对齐静默：清掉关注期并等 pending 去抖 flush 落地
        ch.attention_seconds = 0
        ch._attentive_until = 0
        await asyncio.sleep(1.6)
        base_inbound = len(inbound)

        # group 无 @（且不在关注期）→ 静默入盒，不唤醒
        await client_x.send_message("grp:town", "群里闲聊", kind="group")
        await asyncio.sleep(0.6)
        check("group 不唤醒", len(inbound) == base_inbound)
        check("group 已入盒", ch.box.summary().get("group", {}).get("count") == 1)

        # 摘要便利贴
        extras = ch.turn_extras()
        check("便利贴摘要", extras and "📬" in extras[0], str(extras))

        # 工具：summary / read / reply / presence
        s = json.loads(await agentnet_tools.serverMessageHandler(action="summary"))
        # 唤醒即已读：mention/dm 在入盒时就被标记（内容已进合并上下文），
        # 未读只剩未唤醒的群闲聊
        check("唤醒消息不占未读", s.get("ok") and s["unread"] == 1, str(s)[:120])
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
              f"received={len(received_x)} inbound_delta={len(inbound) - incoming_before}")

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

        # 改名：owner control rename → 端侧身份即时更新 + 落盘
        ch._on_control({"from": id_x.agent_id,
                        "payload": {"action": "rename", "name": "goharu-pc"}})
        check("中控改名端侧生效", ch.name == "goharu-pc"
              and ch.identity.name == "goharu-pc", ch.name)
        prof = json.loads((root / "ch_goharu" / "profile.json")
                          .read_text(encoding="utf-8"))
        check("改名落盘", prof.get("name") == "goharu-pc", str(prof))
        ch.name = "goharu"            # 模拟重启后 config 默认值装配
        ch._restore_saved_profile()   # profile.json 的自更新名字必须赢过 config
        check("重启后 profile 名优先于 config", ch.name == "goharu-pc", ch.name)

        # hub 侧权威改名（观察台 /api/control 走的就是这条路）：
        # 白名单 + presence 立即生效并广播，各端 _peer_names 随之更新
        # 注意：submit_owner_control 内部 run_coroutine_threadsafe(...).result()
        # 会阻塞调用线程——测试的调用线程就是事件循环线程，必须放线程里跑
        res = await asyncio.to_thread(hub.submit_owner_control, id_g.agent_id,
                                      "rename", "goharu-final")
        wl_name = (hub.whitelist.agent(id_g.agent_id) or {}).get("name")
        pv_name = (hub.presence.snapshot().get(id_g.agent_id) or {}).get("name")
        check("hub 改名白名单生效", bool(res.get("ok")) and wl_name == "goharu-final",
              f"{res} wl={wl_name}")
        check("hub 改名 presence 生效", pv_name == "goharu-final", str(pv_name))
        ok = await wait(lambda: ch._peer_names.get(id_g.agent_id) == "goharu-final",
                        label="peer name broadcast")
        check("改名广播刷新 peer names", ok, str(ch._peer_names))
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
              and "安静期自动阅读" in inbound[-1].text
              and "安静期闲聊一" in inbound[-1].text
              and "安静期闲聊二" in inbound[-1].text,
              inbound[-1].text[:120] if inbound else "")
        check("合并阅读后群消息已读", ch.box.unread_count(class_="group") == 0)

        # 大体量群消息 → 压缩智能体摘要后补送（不内联原文）
        ch._group_inline_max_chars = 50
        ch.set_summarize_fn(lambda name, lines: "压缩摘要：有人讨论了测试事项")
        await client_x.send_message("grp:town", "这是一条很长的群消息，" * 20, kind="group")
        ok = await wait(lambda: ch.box.unread_count(class_="group") > 0, label="big inbox")
        await asyncio.sleep(0.6)
        ch._maybe_auto_read()
        ok = await wait(lambda: any("压缩摘要" in m.text for m in inbound),
                        timeout=8, label="summary msg")
        check("大体量群消息压缩摘要", ok, str([m.text[:60] for m in inbound][-2:]))
        check("摘要含阅读提示", any("不重要且不相关" in m.text for m in inbound))
        ch._group_inline_max_chars = 1500

        # update_profile：工具更新档案 → 落盘 + hub presence 即时生效
        up = json.loads(await agentnet_tools.serverMessageHandler(
            action="update_profile", focus="代码与组网协作",
            tags=["coding", "agentnet"]))
        check("工具 update_profile", up.get("ok"), str(up))
        ok = await wait(
            lambda: (hub.presence.snapshot().get(id_g.agent_id, {})
                     .get("profile", {}).get("focus") == "代码与组网协作"),
            label="profile pushed")
        check("档案上报 hub 生效", ok, str(hub.presence.snapshot()))
        prof_disk = json.loads(
            (root / "ch_goharu" / "profile.json").read_text(encoding="utf-8"))
        check("档案落盘", prof_disk.get("focus") == "代码与组网协作"
              and "agentnet" in (prof_disk.get("tags") or []), str(prof_disk))

        # ── 关注期模型 ──────────────────────────────────────────
        ch.attention_seconds = 1.5
        ch._attentive_until = 0
        before = len(inbound)
        await client_x.send_message("grp:town", "关注期外闲聊A", kind="group")
        await asyncio.sleep(0.6)
        check("关注期外群消息不唤醒", len(inbound) == before)

        # @全员 → 唤醒并进入关注期
        await client_x.send_message("grp:town", "@全员 集合", kind="group",
                                    mentions=["*"])
        ok = await wait(lambda: len(inbound) > before, label="mention-all")
        check("@全员唤醒", ok and "全员 集合" in inbound[-1].text
              and "⚡重点" in inbound[-1].text,
              inbound[-1].text[:100] if ok else "")

        # 关注期内：无@群消息也实时唤醒
        mid = len(inbound)
        await client_x.send_message("grp:town", "期内闲聊B", kind="group")
        ok = await wait(lambda: len(inbound) > mid, label="attentive chat")
        check("关注期内群消息实时唤醒", ok and "期内闲聊B" in inbound[-1].text
              and "关注期内顺带接收" in inbound[-1].text,
              inbound[-1].text[:120] if ok else "")

        # 主人消息（不带@）折中方案3：不立即唤醒，但短安静期快速兜底阅读
        ch.owner_ids = {id_x.agent_id}
        ch._attentive_until = 0  # 清关注期，验证主人消息本身不再立即唤醒
        ch.owner_quiet_seconds = 0.5
        ch.quiet_seconds = 60  # 普通安静期拉长，确保触发靠的是 owner_quiet_seconds
        mid = len(inbound)
        await client_x.send_message("grp:town", "主人随便说一句", kind="group")
        await asyncio.sleep(0.3)  # 未到短安静期：仍静默入盒
        check("主人消息无@不立即唤醒", len(inbound) == mid,
              f"inbound_delta={len(inbound) - mid}")
        check("主人消息已入盒", ch.box.unread_count(class_="group") >= 1)
        await asyncio.sleep(0.4)  # 超过 owner_quiet_seconds
        ch._maybe_auto_read()
        check("主人消息短安静期快速阅读", len(inbound) > mid
              and "主人随便说一句" in inbound[-1].text
              and "（主人）" in inbound[-1].text
              and "含主人消息" in inbound[-1].text,
              inbound[-1].text[:200] if len(inbound) > mid else "")
        ch.owner_ids = set()
        ch.owner_quiet_seconds = 15
        ch.quiet_seconds = 0.5

        # ── 群体意识：主人@别的智能体 → 旁观标注 + 不自动中继 ──────────
        ch.owner_ids = {id_x.agent_id}
        ch._peer_names[id_x.agent_id] = "haru-box"
        ch.attention_seconds = 60
        ch._attentive_until = time.time() + 60   # 关注期内，主人群消息会实时进来
        mid = len(inbound)
        await client_x.send_message("grp:town", "haru-box 看一下这个问题", kind="group",
                                    mentions=[id_x.agent_id])
        ok = await wait(lambda: len(inbound) > mid, label="owner@other")
        check("主人@别人 → 群体动态分区", ok
              and "主人消息 · 群体动态" in inbound[-1].text
              and "⚡重点" not in inbound[-1].text,
              inbound[-1].text[:200] if ok else "")
        check("主人@别人 → 标注@对象", ok
              and "specifically sent for @haru-box, Not For You" in inbound[-1].text
              and "helpful or necessary to participate" in inbound[-1].text,
              inbound[-1].text[:250] if ok else "")
        mid_recv = len(received_x)
        ch.send_answer("我旁观的结论")
        await asyncio.sleep(0.5)
        check("主人@别人 → 旁观回答不自动中继", len(received_x) == mid_recv,
              f"received={len(received_x)}")

        # 同事群消息也带@对象标注
        mid = len(inbound)
        await client_x.send_message("grp:town", "补充一点背景", kind="group",
                                    mentions=[id_x.agent_id])
        ok = await wait(lambda: len(inbound) > mid, label="peer@other")
        check("同事@别人 → 群消息标注", ok
              and "specifically sent for @haru-box, Not For You" in inbound[-1].text,
              inbound[-1].text[:250] if ok else "")

        # ── 三态 reminder 全覆盖：@我 → 必须回复；无@ → 广播择优参与 ──────
        mid = len(inbound)
        await client_x.send_message("grp:town", "goharu 汇报一下进度", kind="group",
                                    mentions=[ch.identity.agent_id])
        ok = await wait(lambda: len(inbound) > mid, label="owner@me")
        check("主人@我 → reply reminder", ok
              and "specifically sent for you, you need to reply it"
              in inbound[-1].text,
              inbound[-1].text[:250] if ok else "")
        mid = len(inbound)
        await client_x.send_message("grp:town", "大家今晚例行同步一下", kind="group")
        ok = await wait(lambda: len(inbound) > mid, label="owner broadcast")
        check("主人无@ → 广播 reminder", ok
              and "broadcast message sent from user" in inbound[-1].text
              and "helpful or necessary to reply" in inbound[-1].text,
              inbound[-1].text[:250] if ok else "")

        # mentions 落盒可读（安静期自动阅读路径用）
        ch.box.add("m_note1", "group", "grp:town", id_x.agent_id, "落盒标注",
                   mentions=[id_x.agent_id])
        row_note = [r for r in ch.box.read(since_ts="2000-01-01", mark_read=False)
                    if r.get("msg_id") == "m_note1"]
        check("mentions 入盒可读", bool(row_note)
              and row_note[0].get("mentions") == [id_x.agent_id],
              str(row_note)[:120])
        ch.owner_ids = set()
        ch._attentive_until = 0

        # 关注期过期后回到静默
        ch._attentive_until = 0
        mid = len(inbound)
        await client_x.send_message("grp:town", "过期后闲聊C", kind="group")
        await asyncio.sleep(0.6)
        check("关注期过期回静默", len(inbound) == mid)

        # ── 防乒乓 ──────────────────────────────────────────────
        # 同事触发的 turn：send_answer 不自动中继（必须用 reply 工具）
        ch.attention_seconds = 60
        ch._attentive_until = 0
        mid = len(inbound)
        await client_x.send_message("grp:town", "同事消息一", kind="group",
                                    mentions=[id_g.agent_id])
        await wait(lambda: len(inbound) > mid, label="agent-trigger")
        mid_recv = len(received_x)
        ch.send_answer("我不回复")
        await asyncio.sleep(0.6)
        check("同事触发不自动中继", len(received_x) == mid_recv,
              f"received={len(received_x)}")

        # 连续同事闲聊超过 pingpong_limit → 不再唤醒
        ch._agent_pingpong_limit = 1
        ch._agent_streak = 0
        ch._attentive_until = time.time() + 60
        mid = len(inbound)
        await client_x.send_message("grp:town", "同事闲聊第一次", kind="group")
        ok1 = await wait(lambda: len(inbound) > mid, label="pp1")
        mid2 = len(inbound)
        await client_x.send_message("grp:town", "同事闲聊第二次", kind="group")
        await asyncio.sleep(1.6)
        check("乒乓刹车生效", ok1 and len(inbound) == mid2,
              f"woke_first={ok1} delta={len(inbound) - mid2}")

        # ── 网络话语权（channel 层）：他人持权 → @ 不唤醒；归己 → 唤醒 ──
        ch._agent_pingpong_limit = 3
        ch._agent_streak = 0
        ch.attention_seconds = 60
        mock_floor = {"holder_agent_id": id_x.agent_id, "holder_name": "haru-box",
                      "persona": "main", "since": time.time()}
        ch.floor.update(mock_floor)
        ch._attentive_until = 0
        mid = len(inbound)
        await client_x.send_message("grp:town", "@你但话语权在别处", kind="group",
                                    mentions=[id_g.agent_id])
        await asyncio.sleep(1.8)
        check("他人持权 → @也不唤醒", len(inbound) == mid,
              f"delta={len(inbound) - mid}")
        ch.floor.update({"holder_agent_id": id_g.agent_id, "holder_name": "goharu",
                         "persona": "main", "since": time.time()})
        mid = len(inbound)
        await client_x.send_message("grp:town", "归你了", kind="group")
        ok = await wait(lambda: len(inbound) > mid, label="floor owner wake")
        check("话语权归己 → 主人消息必唤醒", ok, f"delta={len(inbound) - mid}")
        ch.floor.update(None)

        # 关注期状态上报观察台：进入/退出翻转即推 presence（hub 存 attentive 字段）
        ch._attentive_until = time.time() + 60
        ch._report_presence_if_changed()
        ok = await wait(lambda: hub.presence.snapshot()[id_g.agent_id].get("attentive")
                        is True, label="attentive on")
        check("关注期进入上报观察台", ok, str(hub.presence.snapshot()[id_g.agent_id]))
        ch._attentive_until = 0
        ch._report_presence_if_changed()
        ok = await wait(lambda: hub.presence.snapshot()[id_g.agent_id].get("attentive")
                        is False, label="attentive off")
        check("关注期退出上报观察台", ok, str(hub.presence.snapshot()[id_g.agent_id]))
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

        # 关注期状态透出：/api/presence 带 attentive 字段，客户端推送后可见
        def get_presence():
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{oport}/api/presence?token=testtok",
                    timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        pr = await asyncio.to_thread(get_presence)
        agent_p = pr.get("agents", {}).get(a.agent_id, {})
        check("presence 含关注期字段", "attentive" in agent_p
              and agent_p.get("attentive") is False, str(agent_p)[:160])
        ca._attentive_provider = lambda: True
        await ca.push_presence()
        att = None
        deadline = time.time() + 5
        while time.time() < deadline:
            pr = await asyncio.to_thread(get_presence)
            att = pr.get("agents", {}).get(a.agent_id, {}).get("attentive")
            if att is True:
                break
            await asyncio.sleep(0.05)
        check("观察台 API 透出关注中", att is True, str(att))
    finally:
        await ca.close()
        ta.cancel()
        serve_task.cancel()
        for t in (ta, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def ask_test(root: Path):
    """AskUser/高危确认全链路：客户端 submit_ask → hub 挂起表 →
    观察台 GET /api/requests 轮询 → POST /api/answer → control 信封回送端侧。"""
    import urllib.error
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "ask_a", name="alpha")
    wl_path = root / "wl_ask.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_ask")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         token="asktok", file_store=hub.files, whitelist=hub.whitelist,
                         pending_asks=hub.pending_asks_snapshot, on_answer=hub.submit_answer)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    controls = []
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        on_control=lambda e: controls.append(e))
    ta = asyncio.create_task(ca.connect_forever())

    def get_requests():
        with urllib.request.urlopen(
                f"http://127.0.0.1:{oport}/api/requests?token=asktok", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def post_answer(payload: dict, token: str = "asktok"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{oport}/api/answer",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    try:
        check("ask 端上线", await wait_conn(ca), str(ca.connected))

        # 提问登记 → 观察台可见
        await ca.submit_ask("q-1", "question",
                            {"questions": [{"question": "选哪个？",
                                            "options": ["甲", "乙"], "multi_select": False}]})
        rows = None
        deadline = time.time() + 8
        while time.time() < deadline:
            rows = (await asyncio.to_thread(get_requests)).get("requests", [])
            if rows:
                break
            await asyncio.sleep(0.05)
        check("提问入挂起表", bool(rows), str(rows))
        if rows:
            check("挂起表带智能体名", rows[0].get("agent_name") == "alpha", str(rows[0]))
            check("挂起表带过期倒计时", rows[0].get("expires_in") is not None, str(rows[0]))

        # 无 token 作答被拒
        status, _ = await asyncio.to_thread(post_answer, {"request_id": "q-1", "answer": "甲"}, "")
        check("无 token 作答被拒", status == 403, str(status))

        # 作答 → control 回送
        status, j = await asyncio.to_thread(post_answer, {"request_id": "q-1", "answer": "甲"})
        check("作答提交成功", status == 200 and j.get("ok"), f"{status} {j}")
        deadline = time.time() + 8
        while time.time() < deadline and not controls:
            await asyncio.sleep(0.05)
        check("答案 control 回送", bool(controls), str(controls))
        if controls:
            check("answer 动作载荷", controls[-1]["payload"].get("action") == "answer"
                  and controls[-1]["payload"].get("answer") == "甲"
                  and controls[-1]["payload"].get("request_id") == "q-1",
                  str(controls[-1]["payload"]))
        check("作答后挂起表清空",
              not (await asyncio.to_thread(get_requests)).get("requests"), "")

        # 高危确认：answer=true → confirm_result approved
        controls.clear()
        await ca.submit_ask("c-1", "confirm",
                            {"confirm_id": "c-1", "cmd": "rm -rf /tmp/x",
                             "description": "测试命令", "timeout": 300})
        deadline = time.time() + 8
        while time.time() < deadline:
            rows = (await asyncio.to_thread(get_requests)).get("requests", [])
            if rows:
                break
            await asyncio.sleep(0.05)
        check("确认入挂起表", bool(rows) and rows[0].get("kind") == "confirm", str(rows))
        status, j = await asyncio.to_thread(post_answer, {"request_id": "c-1", "answer": True})
        check("确认作答成功", status == 200 and j.get("ok"), f"{status} {j}")
        deadline = time.time() + 8
        while time.time() < deadline and not controls:
            await asyncio.sleep(0.05)
        check("confirm_result 回送", bool(controls)
              and controls[-1]["payload"].get("action") == "confirm_result"
              and controls[-1]["payload"].get("approved") is True
              and controls[-1]["payload"].get("confirm_id") == "c-1",
              str(controls[-1]["payload"] if controls else ""))

        # 未登记 request_id 作答 → 报错不炸
        status, j = await asyncio.to_thread(post_answer, {"request_id": "nope", "answer": 1})
        check("未知 request_id 优雅报错", status == 500 and not j.get("ok", True), f"{status} {j}")
    finally:
        await ca.close()
        ta.cancel()
        serve_task.cancel()
        for t in (ta, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def big_upload_test(root: Path):
    """30MB 文件（旧 25MB 上限之上）流式上传：验签、sha、下载回读一致。"""
    import hashlib
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "big_a", name="alpha")
    wl_path = root / "wl_big.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_big")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         file_store=hub.files, whitelist=hub.whitelist)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        http_base=f"http://127.0.0.1:{oport}")
    ta = asyncio.create_task(ca.connect_forever())

    try:
        check("大文件端上线", await wait_conn(ca), str(ca.connected))
        src = root / "big.bin"
        payload = os.urandom(30 * 1024 * 1024)
        src.write_bytes(payload)
        expect_sha = hashlib.sha256(payload).hexdigest()
        meta = await ca.upload_file(str(src))
        check("30MB 上传成功（越过旧 25MB 上限）",
              meta.get("size") == len(payload), str(meta)[:200])
        check("sha256 一致", meta.get("sha256") == expect_sha)
        check("内容寻址 id", (meta.get("id") or "").startswith(expect_sha[:16]))

        from unittest.mock import patch

        with patch.object(Path, "read_bytes",
                          side_effect=AssertionError("下载不得整文件 read_bytes")):
            got = await asyncio.to_thread(
                urllib.request.urlopen,
                f"http://127.0.0.1:{oport}{meta['url']}", timeout=10)
            check("下载 Content-Length 正确",
                  got.headers.get("Content-Length") == str(len(payload)),
                  got.headers.get("Content-Length"))
            sha = hashlib.sha256()
            while True:
                chunk = got.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
            check("分块下载内容一致", sha.hexdigest() == expect_sha)
    finally:
        await ca.close()
        ta.cancel()
        serve_task.cancel()
        for t in (ta, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def wait_conn(client, timeout: float = 8) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.connected:
            return True
        await asyncio.sleep(0.05)
    return False


async def floor_test(root: Path):
    """网络话语权全链路：agent 工具/观察台设置 → floor_state 广播 → welcome 快照
    → 观察台查询 → 持有者掉线自动收回。"""
    import urllib.error
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "fl_a", name="alpha")
    b = Identity.load_or_create(root / "fl_b", name="beta")
    c = Identity.load_or_create(root / "fl_c", name="gamma")
    wl_path = root / "wl_fl.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64},
                   b.agent_id: {"name": "beta", "pubkey": b.pubkey_b64},
                   c.agent_id: {"name": "gamma", "pubkey": c.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_fl")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         token="fltok", file_store=hub.files, whitelist=hub.whitelist,
                         on_floor=hub.submit_floor_request,
                         floor_snapshot=hub.floor_snapshot)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    events_a, events_b = [], []
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        on_event=lambda k, e: events_a.append((k, e)))
    cb = AgentNetClient(b, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        on_event=lambda k, e: events_b.append((k, e)))
    ta = asyncio.create_task(ca.connect_forever())
    tb = asyncio.create_task(cb.connect_forever())

    def post_floor(payload: dict, token: str = "fltok"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{oport}/api/floor",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    def get_floor():
        with urllib.request.urlopen(
                f"http://127.0.0.1:{oport}/api/floor?token=fltok", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        check("floor 双端上线", await wait_conn(ca) and await wait_conn(cb))

        # agent 工具路径：a 把话语权交给 b
        await ca.set_floor("set", target=b.agent_id, persona="main",
                           note="测试转交")
        deadline = time.time() + 8
        got = None
        while time.time() < deadline:
            for k, e in events_b:
                if k == "floor_state" and (e.get("payload") or {}).get("floor"):
                    got = (e.get("payload") or {})["floor"]
                    break
            if got:
                break
            await asyncio.sleep(0.05)
        check("floor_state 广播到持有者", got is not None, str(events_b)[-300:])
        if got:
            check("广播载荷正确", got.get("holder_agent_id") == b.agent_id
                  and got.get("persona") == "main"
                  and got.get("holder_name") == "beta", str(got))
        check("非持有者也收到广播",
              any(k == "floor_state" for k, _ in events_a), str(events_a)[-200:])

        # 观察台查询
        data = await asyncio.to_thread(get_floor)
        check("观察台可见话语权", (data.get("floor") or {}).get("holder_agent_id")
              == b.agent_id, str(data))

        # 迟到者 welcome 快照携带 floor（独立身份 gamma，避免同身份双连接竞态）
        events_c = []
        cc = AgentNetClient(c, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                            on_event=lambda k, e: events_c.append((k, e)))
        tc = asyncio.create_task(cc.connect_forever())
        ok = await wait_conn(cc)
        welcome_floor = None
        for k, e in events_c:
            if k == "welcome":
                welcome_floor = (e.get("payload") or {}).get("floor")
        check("welcome 携带 floor 快照", ok and welcome_floor
              and welcome_floor.get("holder_agent_id") == b.agent_id,
              str(welcome_floor))
        await cc.close()
        tc.cancel()

        # 无 token 被拒；带 token 收回
        status, _ = await asyncio.to_thread(
            post_floor, {"action": "clear"}, "")
        check("无 token 话语权被拒", status == 403, str(status))
        status, j = await asyncio.to_thread(post_floor, {"action": "clear"})
        check("带 token 收回成功", status == 200 and j.get("ok")
              and not j.get("floor"), f"{status} {j}")

        # 观察台转交给 arch（简写）：b 未报 hosts_architect → 失败
        status, j = await asyncio.to_thread(
            post_floor, {"action": "set", "target": "arch", "persona": "arch"})
        check("无 arch 宿主时优雅拒绝", status == 500 and not j.get("ok"),
              f"{status} {j}")

        # 持有者掉线 → 自动收回
        events_a.clear()
        status, j = await asyncio.to_thread(
            post_floor, {"action": "set", "target": b.agent_id, "persona": "main"})
        check("先设置持有者", status == 200 and j.get("ok"), f"{status} {j}")
        await cb.close()
        deadline = time.time() + 8
        cleared = False
        while time.time() < deadline:
            for k, e in events_a:
                if k == "floor_state" and not (e.get("payload") or {}).get("floor"):
                    cleared = True
                    break
            if cleared:
                break
            await asyncio.sleep(0.05)
        check("持有者掉线 → 自动收回广播", cleared, str(events_a)[-300:])
        data = await asyncio.to_thread(get_floor)
        check("收回后快照为空", data.get("floor") is None, str(data))
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


async def reliability_test(root: Path):
    """本轮可靠性修复：mailbox 至少一次投递、共享状态锁、同身份重连、
    close 唤醒退避、channel 离线状态与同 loop 非阻塞 profile 上报。"""
    import websockets

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer, Mailbox, Presence

    # ---------- Mailbox.pending/ack：第二条发送失败时后续消息保留 ----------
    a = Identity.load_or_create(root / "rel_a", name="alpha")
    wl_path = root / "wl_rel.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", free_port(), wl_path, root / "hub_state_rel")
    for i in range(3):
        hub.mailbox.put(a.agent_id, make_envelope(
            sender="agt_x", to=a.agent_id, type_="message", kind="dm",
            payload={"text": f"离线{i}"}))
    pull = dumps(make_envelope(sender=a.agent_id, to="server", type_="mailbox_pull"))
    calls = {"n": 0}

    async def flaky(_id, _env):
        calls["n"] += 1
        return calls["n"] == 1

    hub._send_to = flaky
    await hub._route(a.agent_id, pull)
    left = hub.mailbox.pending(a.agent_id)
    check("失败停止后仅首条 ack", len(left) == 2 and left[0][1]["payload"]["text"] == "离线1",
          str([e["payload"]["text"] for _, e in left]))

    async def succeed(_id, _env):
        return True

    hub._send_to = succeed
    await hub._route(a.agent_id, pull)
    check("重试后余下消息按序投完", not hub.mailbox.pending(a.agent_id))

    async def explode(_id, _env):
        raise ConnectionError("socket closed")

    hub.mailbox.put(a.agent_id, make_envelope(
        sender="agt_x", to=a.agent_id, type_="message", kind="dm",
        payload={"text": "不能丢"}))
    hub._send_to = explode
    try:
        await hub._route(a.agent_id, pull)
    except ConnectionError:
        pass
    check("发送异常时当前消息仍保留",
          [e["payload"]["text"] for _, e in hub.mailbox.pending(a.agent_id)] == ["不能丢"])
    check("legacy drain 兼容", len(hub.mailbox.drain(a.agent_id)) == 1
          and not hub.mailbox.pending(a.agent_id))

    # ---------- Presence 并发读写 ----------
    presence = Presence(root / "presence_stress.json")
    presence.set_online(a.agent_id, "alpha")

    def writer():
        for i in range(80):
            presence.set_state(a.agent_id, "working" if i % 2 else "idle", i % 2 == 0)
            presence.set_profile(a.agent_id, {"focus": f"f{i}"})

    def reader():
        for _ in range(80):
            snap = presence.snapshot()
            assert a.agent_id in snap

    await asyncio.gather(asyncio.to_thread(writer), asyncio.to_thread(reader))
    check("Presence 并发读写无撕裂", a.agent_id in presence.snapshot())

    # ---------- submit_answer：False 入盒、异常恢复、重复作答只成功一次 ----------
    hub.loop = asyncio.get_running_loop()

    def pending(rid):
        return {"request_id": rid, "kind": "question", "agent_id": a.agent_id,
                "agent_name": "alpha", "data": {}, "ts": time.time(),
                "expires": time.time() + 300}

    hub.pending_asks["r1"] = pending("r1")
    hub._send_to = lambda _id, _env: asyncio.sleep(0, result=False)
    res = await asyncio.to_thread(hub.submit_answer, "r1", "甲")
    queued = hub.mailbox.pending(a.agent_id)
    check("answer 发送失败自动入盒", res.get("ok") and res.get("queued")
          and queued and queued[-1][1]["payload"]["action"] == "answer", str(res))

    hub.pending_asks["r2"] = pending("r2")

    async def fail_answer(_id, _env):
        raise ConnectionError("send failed")

    hub._send_to = fail_answer
    res = await asyncio.to_thread(hub.submit_answer, "r2", "乙")
    check("answer 异常恢复挂起", not res.get("ok") and "r2" in hub.pending_asks, str(res))

    hub.pending_asks["r3"] = pending("r3")
    hub._send_to = lambda _id, _env: asyncio.sleep(0, result=True)
    first = await asyncio.to_thread(hub.submit_answer, "r3", "丙")
    second = await asyncio.to_thread(hub.submit_answer, "r3", "丙")
    check("同一请求仅第一次作答成功", first.get("ok") and not second.get("ok"),
          f"{first} / {second}")

    # ---------- 同身份重连：新连接替换并关闭旧连接，旧清理不污染新状态 ----------
    port = free_port()
    hub2 = HubServer("127.0.0.1", port, wl_path, root / "hub_state_rel2")
    serve_task = asyncio.create_task(hub2.serve())
    await asyncio.sleep(0.3)

    async def raw_hello():
        ws = await websockets.connect(f"ws://127.0.0.1:{port}")
        env = make_envelope(sender=a.agent_id, to="server", type_="hello",
                            payload={"agent_id": a.agent_id, "name": "alpha",
                                     "pubkey": a.pubkey_b64})
        env["sig"] = a.sign(canonical_bytes(env))
        await ws.send(dumps(env))
        reply = parse(await ws.recv())
        return ws, reply

    ws1, r1 = await raw_hello()
    registered1 = hub2._online.get(a.agent_id)
    ws2, r2 = await raw_hello()
    registered2 = hub2._online.get(a.agent_id)
    check("同身份第二条握手成功", r1["type"] == "welcome" and r2["type"] == "welcome")
    await ws1.wait_closed()
    await asyncio.sleep(0.2)
    check("旧连接被替换关闭且新连接仍在线",
          registered2 is not None and registered2 is not registered1
          and hub2.presence.snapshot()[a.agent_id]["online"] is True)
    await ws2.close()
    deadline = time.time() + 5
    while time.time() < deadline and hub2.presence.snapshot()[a.agent_id]["online"]:
        await asyncio.sleep(0.05)
    check("新连接正常关闭才下线", not hub2.presence.snapshot()[a.agent_id]["online"])
    serve_task.cancel()
    try:
        await serve_task
    except (asyncio.CancelledError, Exception):
        pass

    # ---------- close 立即唤醒长退避 ----------
    client = AgentNetClient(a, f"ws://127.0.0.1:{port}", reject_backoff_seconds=60)

    async def fail_run():
        raise ConnectionError("test disconnect")

    client._run_once = fail_run
    task = asyncio.create_task(client.connect_forever())
    deadline = time.time() + 5
    while time.time() < deadline and client._last_backoff == 0:
        await asyncio.sleep(0.02)
    started = time.time()
    await client.close()
    await asyncio.wait_for(task, timeout=2)
    check("close 立即打断重连退避", time.time() - started < 1.5,
          f"{time.time()-started:.2f}s")

    # ---------- channel 离线与同 loop 非阻塞 profile 上报 ----------
    from Gateway.channels.agentnet_channel import AgentNetChannel

    ch = AgentNetChannel.__new__(AgentNetChannel)
    ch.identity = a
    ch._connected = asyncio.Event()
    ch._connected.set()
    ch._on_net_event("disconnected", {})
    check("disconnected 清 channel 状态", not ch._connected.is_set())

    called = asyncio.Event()

    class FakeProfileClient:
        def __init__(self):
            self._profile = {}

        async def update_profile(self):
            called.set()

    ch._client = FakeProfileClient()
    ch._loop = asyncio.get_running_loop()
    ch._connected.set()
    started = time.time()
    ch._report_profile()
    check("同 loop profile 上报不阻塞", time.time() - started < 0.2,
          f"{time.time()-started:.3f}s")
    deadline = time.time() + 2
    while time.time() < deadline and not called.is_set():
        await asyncio.sleep(0.02)
    check("profile 上报实际完成", called.is_set())


async def handshake_test(root: Path):
    """接入边界：hello 不带/不采信设备档案（入白名单后才 profile_update 上报）；
    不在白名单的机器握手被拒后走长退避，不刷屏。"""
    import websockets

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer

    port = free_port()
    b = Identity.load_or_create(root / "hs_b", name="beta")
    c = Identity.load_or_create(root / "hs_c", name="gamma")
    d = Identity.load_or_create(root / "hs_d", name="intruder")
    wl_path = root / "wl_hs.json"
    wl_path.write_text(json.dumps({
        "agents": {b.agent_id: {"name": "beta", "pubkey": b.pubkey_b64},
                   c.agent_id: {"name": "gamma", "pubkey": c.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_hs",
                    join_password="testpw")
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    # 真客户端：档案经第二段（profile_update）到达 hub
    cb = AgentNetClient(b, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        profile={"focus": "beta-focus"})
    tb = asyncio.create_task(cb.connect_forever())
    # 不在白名单：短退避参数便于测试观测
    cd = AgentNetClient(d, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        reject_backoff_seconds=1)
    td = asyncio.create_task(cd.connect_forever())
    try:
        check("白名单端上线", await wait_conn(cb))
        ok = False
        deadline = time.time() + 8
        while time.time() < deadline:
            prof = hub.presence.snapshot().get(b.agent_id, {}).get("profile") or {}
            if prof.get("focus") == "beta-focus" and (prof.get("device") or {}).get("host"):
                ok = True
                break
            await asyncio.sleep(0.05)
        check("档案经 profile_update 第二段上报", ok,
              str(hub.presence.snapshot().get(b.agent_id, {}).get("profile"))[:160])

        # 原始 ws：hello 带诱饵档案 → hub 必须忽略
        hello = make_envelope(sender=c.agent_id, to="server", type_="hello",
                              payload={"agent_id": c.agent_id, "name": "gamma",
                                       "pubkey": c.pubkey_b64,
                                       "profile": {"focus": "LEAK-MARKER"}})
        hello["sig"] = c.sign(canonical_bytes(hello))
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(dumps(hello))
            reply = parse(await ws.recv())
            check("诱饵档案端仍能握手", reply["type"] == "welcome", reply["type"])
        prof_c = hub.presence.snapshot().get(c.agent_id, {}).get("profile") or {}
        check("hub 忽略 hello 内档案（无 LEAK）", "LEAK-MARKER" not in json.dumps(prof_c),
              str(prof_c)[:120])

        # 非白名单端：握手被拒 → 长退避（_last_backoff == reject_backoff）
        ok = False
        deadline = time.time() + 8
        while time.time() < deadline:
            if cd._last_backoff == 1.0 and not cd.connected:
                ok = True
                break
            await asyncio.sleep(0.05)
        check("非白名单 → 被拒且走 reject_backoff", ok,
              f"last_backoff={cd._last_backoff} connected={cd.connected}")
        check("默认拒绝退避为 10 分钟",
              AgentNetClient(b, f"ws://127.0.0.1:{port}")._reject_backoff == 600)

        # 接入密码：非白名单设备凭密码自动登记入网（旧成员免密）
        check("被拒时白名单无该指纹", hub.whitelist.agent(d.agent_id) is None)
        cd2 = AgentNetClient(d, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                             password="testpw")
        td2 = asyncio.create_task(cd2.connect_forever())
        try:
            ok = await wait_conn(cd2)
            check("凭接入密码入网", ok and cd2.connected, str(cd2.last_error))
            check("密码正确后自动登记白名单", hub.whitelist.agent(d.agent_id) is not None)
        finally:
            await cd2.close()
            td2.cancel()
            try:
                await td2
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        await cb.close()
        await cd.close()
        tb.cancel()
        td.cancel()
        serve_task.cancel()
        for t in (tb, td, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def admin_test(root: Path):
    """观察台管理：运行时改接入密码（持久化）+ 删除已注册智能体（白名单/presence）。"""
    import urllib.error
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "ad_a", name="alpha")
    b = Identity.load_or_create(root / "ad_b", name="beta")
    wl_path = root / "wl_admin.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64},
                   b.agent_id: {"name": "beta", "pubkey": b.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_admin")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         token="adtok", file_store=hub.files, whitelist=hub.whitelist,
                         join_password_snapshot=hub.join_password_snapshot,
                         on_set_join_password=hub.submit_join_password,
                         on_remove_agent=hub.submit_remove_agent)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    cb = AgentNetClient(b, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600)
    tb = asyncio.create_task(cb.connect_forever())

    def get_json(path, token="adtok"):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{oport}{path}?token={token}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(path, payload, token="adtok"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{oport}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    try:
        check("管理端上线", await wait_conn(cb))

        # 密码设置：无 token 拒绝 → 设置 → 快照可见 → 落盘持久化
        status, _ = await asyncio.to_thread(post, "/api/join-password",
                                            {"password": "x"}, "")
        check("无 token 设置密码被拒", status == 403, str(status))
        status, j = await asyncio.to_thread(post, "/api/join-password",
                                            {"password": "newpw"})
        check("设置密码成功", status == 200 and j.get("ok") and j.get("enabled"),
              f"{status} {j}")
        pw_file = root / "hub_state_admin" / "join_password.txt"
        check("密码持久化", pw_file.exists()
              and pw_file.read_text(encoding="utf-8").strip() == "newpw")
        snap = await asyncio.to_thread(get_json, "/api/join-password")
        check("快照返回当前密码", snap.get("password") == "newpw", str(snap))
        # 重启（新建 hub 实例）后密码保持
        hub2 = HubServer("127.0.0.1", free_port(), wl_path,
                         root / "hub_state_admin")
        check("重启后密码从磁盘恢复", hub2.join_password == "newpw",
              hub2.join_password)

        # 删除智能体：owner 不可删 → 普通设备删除（白名单 + presence）
        check("删除前在白名单", hub.whitelist.agent(b.agent_id) is not None)
        status, j = await asyncio.to_thread(post, "/api/remove-agent",
                                            {"target": hub.owner.agent_id})
        check("owner 不可删除", status == 500 and not j.get("ok"), f"{status} {j}")
        status, j = await asyncio.to_thread(post, "/api/remove-agent",
                                            {"target": b.agent_id})
        check("删除智能体成功", status == 200 and j.get("ok")
              and j.get("removed") == b.agent_id, f"{status} {j}")
        check("白名单已移除", hub.whitelist.agent(b.agent_id) is None)
        check("presence 已移除",
              b.agent_id not in hub.presence.snapshot(),
              str(hub.presence.snapshot().keys()))
        # 删除未知目标 → 报错
        status, j = await asyncio.to_thread(post, "/api/remove-agent",
                                            {"target": "agt_nope"})
        check("未知目标删除报错", status == 500 and not j.get("ok"), f"{status} {j}")

        # 页面静态标记
        page = (Path(__file__).resolve().parent.parent
                / "AgentNet" / "server" / "observer_page.html").read_text(encoding="utf-8")
        check("页面含网络设置面板", "netPanel" in page and "/api/join-password" in page
              and "/api/remove-agent" in page)
    finally:
        await cb.close()
        tb.cancel()
        serve_task.cancel()
        for t in (tb, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def security_control_test(root: Path):
    """安全策略中控：观察台 POST /api/control action=security →
    载荷原样投递目标智能体（端侧 apply_rules 写回各自 config，由端侧负责）。"""
    import urllib.error
    import urllib.request

    from AgentNet.client import AgentNetClient
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "sec_a", name="alpha")
    wl_path = root / "wl_sec.json"
    wl_path.write_text(json.dumps({
        "agents": {a.agent_id: {"name": "alpha", "pubkey": a.pubkey_b64}},
        "groups": {}}), encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl_path, root / "hub_state_sec")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1", port=oport,
                         token="sectok", file_store=hub.files, whitelist=hub.whitelist,
                         on_control=hub.submit_owner_control)
    obs.start()
    serve_task = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)

    controls = []
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600,
                        on_control=lambda e: controls.append(e))
    ta = asyncio.create_task(ca.connect_forever())

    def post_control(payload: dict, token: str = "sectok"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{oport}/api/control",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    try:
        check("security 端上线", await wait_conn(ca), str(ca.connected))

        status, j = await asyncio.to_thread(post_control, {
            "target": a.agent_id, "action": "security",
            "dangerous_commands": ["rm -rf", "format"],
            "require_confirmation": ["sudo", "del"],
            "allow_confirmation": True})
        check("安全策略下发成功", status == 200 and j.get("ok"), f"{status} {j}")
        deadline = time.time() + 8
        while time.time() < deadline and not controls:
            await asyncio.sleep(0.05)
        check("规则 control 回送", bool(controls), str(controls))
        if controls:
            p = controls[-1]["payload"]
            check("规则载荷完整", p.get("action") == "security"
                  and p.get("dangerous_commands") == ["rm -rf", "format"]
                  and p.get("require_confirmation") == ["sudo", "del"]
                  and p.get("allow_confirmation") is True,
                  str(p))

        # 非 list 载荷 → 400；空载荷 → 400
        status, _ = await asyncio.to_thread(post_control, {
            "target": a.agent_id, "action": "security",
            "dangerous_commands": "rm -rf"})
        check("非法规则清单被拒", status == 400, str(status))
        status, _ = await asyncio.to_thread(post_control, {
            "target": a.agent_id, "action": "security"})
        check("空规则载荷被拒", status == 400, str(status))

        # 未知动作仍被拒
        status, _ = await asyncio.to_thread(post_control, {
            "target": a.agent_id, "action": "reboot_everything"})
        check("未知动作被拒", status == 400, str(status))
    finally:
        await ca.close()
        ta.cancel()
        serve_task.cancel()
        for t in (ta, serve_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


def security_unit_test():
    """CommandSecurityChecker.apply_rules：运行时即时生效 + 配置对象有
    set/save 时写回（persisted=True），纯 dict 配置只改运行时。"""
    from Tools.config_proxy import reset_config, set_config
    from Tools.security import get_security_checker
    import Tools.security as sec_mod

    try:
        # 纯 dict 注入（外部 harness 视角）：只改运行时
        set_config({"tools": {"security": {
            "enabled": True, "allow_confirmation": True,
            "dangerous_commands": ["shutdown"], "require_confirmation": ["del"]}}})
        sec_mod._checker_instance = None
        chk = get_security_checker()
        check("快照字段齐", chk.snapshot()["dangerous_commands"] == ["shutdown"],
              str(chk.snapshot()))
        r = chk.apply_rules(dangerous_commands=["rm -rf"],
                            require_confirmation=["sudo"], allow_confirmation=False)
        check("运行时即时生效", chk.dangerous_commands == ["rm -rf"]
              and chk.require_confirmation == ["sudo"]
              and chk.allow_confirmation is False, str(chk.snapshot()))
        check("纯 dict 配置不写盘", r.get("persisted") is False, str(r))
        check("分类随新规则变", chk.classify_command("sudo rm -rf /x") == "blocked"
              and chk.classify_command("ls") == "safe", "")

        # 带 set/save 的配置对象（Goharu 全局 Configuration 视角）：写回
        class _FakeCfg:
            def __init__(self):
                self.store = {"tools.security.dangerous_commands": ["shutdown"]}
                self.saved = False

            def get(self, key, default=None):
                return self.store.get(key, default)

            def set(self, key, value):
                self.store[key] = value

            def save(self):
                self.saved = True

        fake = _FakeCfg()
        set_config(fake)
        sec_mod._checker_instance = None
        chk2 = get_security_checker()
        r2 = chk2.apply_rules(dangerous_commands=["format"])
        check("set/save 配置写盘", r2.get("persisted") is True and fake.saved
              and fake.store["tools.security.dangerous_commands"] == ["format"],
              str(fake.store))
    finally:
        reset_config()
        sec_mod._checker_instance = None


if __name__ == "__main__":
    sys.exit(main())
