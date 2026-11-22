"""poharu-hub v2：AgentNet 服务器（relay + mailbox + presence + registry）。

单进程 asyncio websockets 服务。白名单制：不在 whitelist 的指纹握手即拒。
状态机：UNKNOWN → IDENTIFIED（指纹出示）→ CONFIRMED（白名单匹配）→
IDLE/WORKING/MAINTENANCE（心跳自报）→ OFFLINE（断连落 last_seen）。

部署：python -m AgentNet.server.hub --config server_config.json
whitelist.json：
{
  "agents": {"agt_xxx": {"name": "goharu", "pubkey": "<base64 der>", "tags": []}},
  "groups": {"grp:主城": {"members": ["agt_xxx", "agt_yyy"]}}
}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import websockets

from AgentNet.identity import Identity
from AgentNet.protocol import (
    RUNTIME_STATES,
    agent_id_from_pubkey_der,
    canonical_bytes,
    dumps,
    make_envelope,
    now_ts,
    parse,
)

logger = logging.getLogger("agentnet.hub")

MAILBOX_TTL_SECONDS = 7 * 86400
HELLO_TIMEOUT = 10


class Whitelist:
    def __init__(self, path: Path):
        self._path = path
        self._mtime = 0.0
        self._data: dict = {"agents": {}, "groups": {}}
        self.reload()

    def reload(self) -> None:
        self._data = json.loads(self._path.read_text(encoding="utf-8"))
        self._mtime = self._path.stat().st_mtime
        logger.info("whitelist 加载: %d agents, %d groups",
                    len(self._data["agents"]), len(self._data.get("groups", {})))

    def maybe_reload(self) -> None:
        try:
            if self._path.stat().st_mtime != self._mtime:
                self.reload()
        except OSError:
            pass

    def agent(self, agent_id: str) -> Optional[dict]:
        self.maybe_reload()
        return self._data.get("agents", {}).get(agent_id)

    def group_members(self, group: str) -> list[str]:
        self.maybe_reload()
        return list(self._data.get("groups", {}).get(group, {}).get("members", []))

    def all_agents(self) -> dict:
        self.maybe_reload()
        return dict(self._data.get("agents", {}))

    def ensure_agent(self, agent_id: str, name: str, pubkey: str) -> None:
        """登记/更新一个智能体（服务器自有身份启动时自注册用）。"""
        self.maybe_reload()
        agents = self._data.setdefault("agents", {})
        cur = agents.get(agent_id) or {}
        if cur.get("pubkey") == pubkey and cur.get("name") == name:
            return
        agents[agent_id] = {**cur, "name": name, "pubkey": pubkey}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        self.reload()
        logger.info("whitelist 登记: %s (%s)", agent_id, name)


class Presence:
    """状态机表：{agent_id: {state, last_seen, online, name}}。内存 + 落盘 JSON。"""

    def __init__(self, path: Path):
        self._path = path
        self._table: dict[str, dict] = {}
        if path.exists():
            try:
                self._table = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass

    def _persist(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._table, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def set_online(self, agent_id: str, name: str, profile: Optional[dict] = None) -> None:
        prev = self._table.get(agent_id) or {}
        self._table[agent_id] = {
            "state": "idle", "online": True,
            "last_seen": now_ts(), "name": name,
            "online_since": now_ts(),
            "profile": profile if profile is not None else (prev.get("profile") or {}),
        }
        self._persist()

    def set_profile(self, agent_id: str, profile: dict) -> bool:
        """智能体自报档案更新（profile_update 消息驱动）。"""
        if agent_id not in self._table:
            return False
        self._table[agent_id]["profile"] = dict(profile)
        self._table[agent_id]["last_seen"] = now_ts()
        self._persist()
        return True

    def set_offline(self, agent_id: str) -> None:
        if agent_id in self._table:
            self._table[agent_id]["online"] = False
            self._table[agent_id]["last_seen"] = now_ts()
            self._persist()

    def set_state(self, agent_id: str, state: str) -> bool:
        if agent_id not in self._table or state not in RUNTIME_STATES:
            return False
        self._table[agent_id]["state"] = state
        self._table[agent_id]["last_seen"] = now_ts()
        self._persist()
        return True

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._table))


class Mailbox:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS mailbox("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL,"
            "envelope TEXT NOT NULL, ts REAL NOT NULL)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mb_rcpt ON mailbox(recipient)")
        self._conn.commit()
        self._gc()

    def _gc(self) -> None:
        self._conn.execute("DELETE FROM mailbox WHERE ts < ?",
                           (time.time() - MAILBOX_TTL_SECONDS,))
        self._conn.commit()

    def put(self, recipient: str, envelope: dict) -> None:
        self._conn.execute(
            "INSERT INTO mailbox(recipient, envelope, ts) VALUES (?,?,?)",
            (recipient, dumps(envelope), time.time()))
        self._conn.commit()

    def drain(self, recipient: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, envelope FROM mailbox WHERE recipient = ? ORDER BY id",
            (recipient,)).fetchall()
        if rows:
            self._conn.execute("DELETE FROM mailbox WHERE recipient = ?", (recipient,))
            self._conn.commit()
        return [json.loads(r[1]) for r in rows]


class MessageLog:
    """消息流水（观察台数据源）。按行记录路由过的每条消息，含发送者名字快照。"""

    def __init__(self, db_path: Path, max_rows: int = 20000):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
            "sender TEXT NOT NULL, sender_name TEXT NOT NULL,"
            "recipient TEXT NOT NULL, kind TEXT NOT NULL,"
            "group_name TEXT, mentions TEXT, attachments TEXT, text TEXT NOT NULL)")
        try:  # 旧库迁移：附件列后加
            self._conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_id ON messages(id)")
        self._conn.commit()
        self._max_rows = max_rows

    def log(self, sender: str, sender_name: str, recipient: str, kind: str,
            mentions: Optional[list], text: str, ts: Optional[str] = None,
            attachments: Optional[list] = None) -> None:
        self._conn.execute(
            "INSERT INTO messages(ts, sender, sender_name, recipient, kind,"
            " group_name, mentions, attachments, text) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts or now_ts(), sender, sender_name, recipient, kind,
             recipient if kind == "group" else None,
             json.dumps(mentions or [], ensure_ascii=False),
             json.dumps(attachments or [], ensure_ascii=False), text))
        self._conn.commit()
        # 轻量 retention：超出上限裁最旧（每 200 条查一次）
        c = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if c > self._max_rows + 200:
            self._conn.execute(
                "DELETE FROM messages WHERE id IN "
                "(SELECT id FROM messages ORDER BY id ASC LIMIT ?)", (c - self._max_rows,))
            self._conn.commit()

    def recent(self, limit: int = 100, after_id: int = 0,
               filter_: str = "all") -> list[dict]:
        sql = ["SELECT id, ts, sender, sender_name, recipient, kind, group_name,"
               " mentions, attachments, text FROM messages WHERE id > ?"]
        params: list = [int(after_id)]
        if filter_ == "group":
            sql.append("AND kind = 'group'")
        elif filter_ == "dm":
            sql.append("AND kind = 'dm'")
        elif filter_ == "mention":
            sql.append("AND kind = 'group' AND mentions != '[]'")
        sql.append("ORDER BY id DESC LIMIT ?")
        params.append(int(limit))
        rows = [dict(r) for r in self._conn.execute(
            "\n".join(sql), params).fetchall()]
        for r in rows:
            try:
                r["mentions"] = json.loads(r.get("mentions") or "[]")
            except ValueError:
                r["mentions"] = []
            try:
                r["attachments"] = json.loads(r.get("attachments") or "[]")
            except ValueError:
                r["attachments"] = []
        return list(reversed(rows))


class FileStore:
    """附件仓库：内容寻址存储（sha 前缀 + 安全文件名），供上传/下载端点使用。"""

    def __init__(self, dir_path: Path):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        # 仅 ASCII 安全字符进文件名（file_id 会出现在 URL 里，非 ASCII 会炸 urllib）
        base = Path(str(name or "file")).name or "file"
        return "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "_"
                       for c in base)[:80]

    def save(self, name: str, data: bytes) -> dict:
        import hashlib

        sha = hashlib.sha256(data).hexdigest()
        display_name = (Path(str(name or "file")).name or "file")[:120]
        file_id = f"{sha[:16]}_{self._safe_name(display_name)}"
        path = self.dir / file_id
        if not path.exists():
            path.write_bytes(data)
        return {"id": file_id, "name": display_name, "size": len(data),
                "sha256": sha, "url": f"/files/{file_id}"}

    def path(self, file_id: str) -> Optional[Path]:
        if not file_id or "/" in file_id or "\\" in file_id or file_id.startswith("."):
            return None
        p = self.dir / file_id
        return p if p.is_file() else None


class HubServer:
    def __init__(self, host: str, port: int, whitelist_path: Path, state_dir: Path,
                 owner_name: str = "haru"):
        self.host = host
        self.port = port
        self.whitelist = Whitelist(whitelist_path)
        state_dir.mkdir(parents=True, exist_ok=True)
        self.presence = Presence(state_dir / "presence.json")
        self.mailbox = Mailbox(state_dir / "mailbox.db")
        self.message_log = MessageLog(state_dir / "messages.db")
        self.files = FileStore(state_dir / "files")
        self._online: dict[str, Any] = {}  # agent_id -> websocket
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        # 用户本体身份：观察台输入框以该身份广播（服务器自持私钥，自动进白名单）
        self.owner = Identity.load_or_create(state_dir / "owner_identity", name=owner_name)
        self.whitelist.ensure_agent(self.owner.agent_id, owner_name, self.owner.pubkey_b64)

    def submit_owner_message(self, text: str, mentions: Optional[list] = None,
                             attachments: Optional[list] = None) -> dict:
        """观察台指令入口（HTTP 线程调用）：以 owner 身份广播一条消息。"""
        if self.loop is None:
            return {"ok": False, "error": "hub 未就绪"}
        env = make_envelope(
            sender=self.owner.agent_id, to="*", type_="message", kind="group",
            mentions=mentions, payload={"text": text})
        if attachments:
            env["payload"]["attachments"] = attachments
        env["sig"] = self.owner.sign(canonical_bytes(env))
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._route_message(self.owner.agent_id, env), self.loop)
            fut.result(timeout=10)
        except Exception as e:
            logger.warning("owner 指令投递失败: %s", e)
            return {"ok": False, "error": str(e)}
        return {"ok": True, "msg_id": env["id"]}

    def submit_owner_control(self, target: str, action: str) -> dict:
        """观察台中控入口（HTTP 线程调用）：以 owner 身份给指定智能体发中控动作。"""
        if self.loop is None:
            return {"ok": False, "error": "hub 未就绪"}
        if not self.whitelist.agent(target):
            return {"ok": False, "error": f"目标不在白名单: {target}"}
        env = make_envelope(
            sender=self.owner.agent_id, to=target, type_="control",
            payload={"action": action})
        env["sig"] = self.owner.sign(canonical_bytes(env))

        async def _deliver() -> dict:
            # 中控消息不进消息流水（它是运维动作，不是聊天）；
            # 但记一条系统流水便于观察台展示"中控动作已发出"
            self.message_log.log(self.owner.agent_id, "中控", target, "control",
                                 None, f"{action} → {target}", env.get("ts"))
            return {"delivered": await self._send_to(target, env)}

        try:
            result = asyncio.run_coroutine_threadsafe(_deliver(), self.loop).result(timeout=10)
        except Exception as e:
            logger.warning("owner 中控投递失败: %s", e)
            return {"ok": False, "error": str(e)}
        if not result["delivered"]:
            self.mailbox.put(target, env)
            return {"ok": True, "msg_id": env["id"], "queued": True}
        return {"ok": True, "msg_id": env["id"], "queued": False}

    # ---------------------------------------------------------- 连接生命周期
    async def handle(self, ws) -> None:
        agent_id = None
        try:
            agent_id, profile = await self._handshake(ws)
            if agent_id is None:
                return
            self._online[agent_id] = ws
            self.presence.set_online(
                agent_id, self.whitelist.agent(agent_id).get("name", ""), profile)
            await self._broadcast_presence()
            async for raw in ws:
                await self._route(agent_id, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("连接处理异常 agent=%s", agent_id)
        finally:
            if agent_id is not None:
                self._online.pop(agent_id, None)
                self.presence.set_offline(agent_id)
                await self._broadcast_presence()
                logger.info("下线: %s (last_seen 已落盘)", agent_id)

    async def _handshake(self, ws):
        """UNKNOWN → IDENTIFIED → CONFIRMED；任一步失败回 auth_error 并断开。

        返回 (agent_id, profile)；失败返回 (None, None)。
        """
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=HELLO_TIMEOUT)
            env = parse(raw)
        except Exception:
            return None, None
        if env.get("type") != "hello":
            return None, None
        payload = env.get("payload") or {}
        agent_id = payload.get("agent_id", "")
        pubkey = payload.get("pubkey", "")
        # IDENTIFIED：指纹与公钥自洽
        try:
            import base64

            der = base64.b64decode(pubkey)
            if agent_id_from_pubkey_der(der) != agent_id:
                raise ValueError("指纹不匹配")
        except Exception:
            await ws.send(dumps(self._auth_error(env, "identity_mismatch")))
            return None, None
        # CONFIRMED：白名单 + 签名验证
        record = self.whitelist.agent(agent_id)
        if record is None or record.get("pubkey") != pubkey:
            await ws.send(dumps(self._auth_error(env, "not_in_whitelist")))
            logger.warning("拒绝未知设备: %s", agent_id)
            return None, None
        if not Identity.verify(pubkey, canonical_bytes(env), env.get("sig", "")):
            await ws.send(dumps(self._auth_error(env, "bad_signature")))
            return None, None
        welcome = make_envelope(sender="server", to=agent_id, type_="welcome",
                                payload={"agent_id": agent_id, "server_ts": now_ts()})
        await ws.send(dumps(welcome))
        logger.info("确认设备: %s (%s)", agent_id, record.get("name"))
        profile = payload.get("profile") or {}
        if not isinstance(profile, dict):
            profile = {}
        return agent_id, profile

    def _auth_error(self, env: dict, reason: str) -> dict:
        return make_envelope(sender="server", to=env.get("from", "?"),
                             type_="auth_error", payload={"reason": reason})

    # ---------------------------------------------------------- 路由
    async def _route(self, sender: str, raw: str) -> None:
        try:
            env = parse(raw)
        except ValueError:
            return
        t = env["type"]
        if t == "presence_update":
            state = (env.get("payload") or {}).get("state", "idle")
            if self.presence.set_state(sender, state):
                await self._broadcast_presence()
            return
        if t == "presence_query":
            reply = make_envelope(sender="server", to=sender, type_="presence_state",
                                  payload={"agents": self.presence.snapshot()})
            await self._send_to(sender, reply)
            return
        if t == "mailbox_pull":
            for queued in self.mailbox.drain(sender):
                await self._send_to(sender, queued)
            return
        if t == "profile_update":
            # 智能体自报档案更新（验签后生效并广播新状态机表）
            record = self.whitelist.agent(sender)
            if record is None or not Identity.verify(
                    record["pubkey"], canonical_bytes(env), env.get("sig", "")):
                logger.warning("profile_update 验签失败 from=%s", sender)
                return
            profile = (env.get("payload") or {}).get("profile")
            if isinstance(profile, dict) and self.presence.set_profile(sender, profile):
                logger.info("档案更新: %s → focus=%s tags=%s", sender,
                            profile.get("focus", ""), profile.get("tags", []))
                await self._broadcast_presence()
            return
        if t == "message":
            # 验签（防冒充）；白名单公钥
            record = self.whitelist.agent(sender)
            if record is None or not Identity.verify(
                    record["pubkey"], canonical_bytes(env), env.get("sig", "")):
                logger.warning("消息验签失败 from=%s", sender)
                return
            await self._route_message(sender, env)
            return
        logger.debug("未知类型: %s from=%s", t, sender)

    async def _route_message(self, sender: str, env: dict) -> None:
        """全员广播制：一条消息推给网内所有其他智能体（在线即推，离线入盒）。

        kind 仅作分类元数据；定向沟通用 mentions（@）表达——接收端据此
        把消息分类为 mention（需立即处理）或 group（静默入盒）。
        """
        payload = env.get("payload") or {}
        text = str(payload.get("text", ""))
        attachments = payload.get("attachments") or []
        sender_name = (self.whitelist.agent(sender) or {}).get("name", sender)
        kind = env.get("kind") or "group"
        self.message_log.log(sender, sender_name, "*", kind,
                             env.get("mentions"), text, env.get("ts"), attachments)
        for agent_id in self.whitelist.all_agents():
            if agent_id == sender:
                continue
            await self._deliver(agent_id, env)

    async def _deliver(self, recipient: str, env: dict) -> None:
        if not await self._send_to(recipient, env):
            self.mailbox.put(recipient, env)  # 离线 → mailbox
            logger.info("离线入盒: → %s (%s)", recipient, env.get("id"))

    async def _send_to(self, agent_id: str, env: dict) -> bool:
        ws = self._online.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send(dumps(env))
            return True
        except websockets.ConnectionClosed:
            return False

    async def _broadcast_presence(self) -> None:
        env = make_envelope(sender="server", to="*", type_="presence_state",
                            payload={"agents": self.presence.snapshot()})
        for agent_id in list(self._online):
            await self._send_to(agent_id, env)

    # ---------------------------------------------------------- 入口
    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        async with websockets.serve(self.handle, self.host, self.port):
            logger.info("poharu-hub v2 监听 %s:%d", self.host, self.port)
            await asyncio.Future()  # run forever


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7878)
    ap.add_argument("--whitelist", default="whitelist.json")
    ap.add_argument("--state-dir", default="./hub_state")
    ap.add_argument("--observer-port", type=int, default=0,
                    help="手机端观察台 HTTP 端口（0=关闭）")
    ap.add_argument("--observer-token", default="",
                    help="观察台访问 token（空=开放；开放时 /api/send 被禁用）")
    ap.add_argument("--owner-name", default="haru",
                    help="观察台输入框使用的用户身份名")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    server = HubServer(args.host, args.port, Path(args.whitelist), Path(args.state_dir),
                       owner_name=args.owner_name)
    if args.observer_port:
        from AgentNet.server.observer import ObserverServer
        observer = ObserverServer(
            message_log=server.message_log,
            presence=server.presence,
            host="0.0.0.0",
            port=args.observer_port,
            token=args.observer_token,
            file_store=server.files,
            whitelist=server.whitelist,
            on_send=server.submit_owner_message,
            on_control=server.submit_owner_control,
        )
        observer.start()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
