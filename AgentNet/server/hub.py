"""poharu-hub v2：HaruNet（AgentNet）服务器（relay + mailbox + presence + registry）。

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
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import websockets

from AgentNet.identity import Identity
from AgentNet.server.error_log import ErrorLog
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
        self._persist()
        logger.info("whitelist 登记: %s (%s)", agent_id, name)

    def remove_agent(self, agent_id: str) -> bool:
        """移除一个已登记的智能体（观察台"删除设备"用）。"""
        self.maybe_reload()
        agents = self._data.get("agents", {})
        if agent_id not in agents:
            return False
        del agents[agent_id]
        self._persist()
        logger.info("whitelist 移除: %s", agent_id)
        return True

    def _persist(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        self.reload()


class Presence:
    """状态机表：{agent_id: {state, last_seen, online, name}}。内存 + 落盘 JSON。"""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
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
        with self._lock:
            prev = self._table.get(agent_id) or {}
            self._table[agent_id] = {
                "state": "idle", "online": True,
                "last_seen": now_ts(), "name": name,
                "online_since": now_ts(),
                "attentive": False,
                "profile": profile if profile is not None else (prev.get("profile") or {}),
            }
            self._persist()

    def set_profile(self, agent_id: str, profile: dict) -> bool:
        """智能体自报档案更新（profile_update 消息驱动）。"""
        with self._lock:
            if agent_id not in self._table:
                return False
            self._table[agent_id]["profile"] = dict(profile)
            self._table[agent_id]["last_seen"] = now_ts()
            self._persist()
            return True

    def set_offline(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._table:
                self._table[agent_id]["online"] = False
                self._table[agent_id]["last_seen"] = now_ts()
                self._persist()

    def remove(self, agent_id: str) -> bool:
        """从状态机表移除（观察台"删除设备"：与白名单同步清理）。"""
        with self._lock:
            if agent_id not in self._table:
                return False
            del self._table[agent_id]
            self._persist()
            return True

    def rename(self, agent_id: str, name: str) -> bool:
        """主人中控改名：状态机表同步更新并广播（白名单由调用方改）。"""
        with self._lock:
            if agent_id not in self._table or not name:
                return False
            self._table[agent_id]["name"] = name
            self._table[agent_id]["last_seen"] = now_ts()
            self._persist()
            return True

    def set_state(self, agent_id: str, state: str,
                  attentive: Optional[bool] = None) -> bool:
        with self._lock:
            if agent_id not in self._table or state not in RUNTIME_STATES:
                return False
            self._table[agent_id]["state"] = state
            if attentive is not None:
                self._table[agent_id]["attentive"] = bool(attentive)
            self._table[agent_id]["last_seen"] = now_ts()
            self._persist()
            return True

    def snapshot(self) -> dict:
        with self._lock:
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
        self._lock = threading.Lock()
        self._gc()

    def _gc(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM mailbox WHERE ts < ?",
                               (time.time() - MAILBOX_TTL_SECONDS,))
            self._conn.commit()

    def put(self, recipient: str, envelope: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mailbox(recipient, envelope, ts) VALUES (?,?,?)",
                (recipient, dumps(envelope), time.time()))
            self._conn.commit()

    def drain(self, recipient: str) -> list[dict]:
        """兼容旧接口：取出即删。新投递路径应使用 pending/ack。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, envelope FROM mailbox WHERE recipient = ? ORDER BY id",
                (recipient,)).fetchall()
            if rows:
                self._conn.execute("DELETE FROM mailbox WHERE recipient = ?", (recipient,))
                self._conn.commit()
            return [json.loads(r[1]) for r in rows]

    def pending(self, recipient: str) -> list[tuple[int, dict]]:
        """只读未投消息，(row_id, envelope)；成功发送后才 ack。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, envelope FROM mailbox WHERE recipient = ? ORDER BY id",
                (recipient,)).fetchall()
            return [(r[0], json.loads(r[1])) for r in rows]

    def ack(self, row_id: int) -> None:
        """确认单行已写入 socket；不影响投递期间新插入的消息。"""
        with self._lock:
            self._conn.execute("DELETE FROM mailbox WHERE id = ?", (row_id,))
            self._conn.commit()


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

    def commit(self, name: str, tmp_path: Path, sha256_hex: str, size: int) -> dict:
        """流式上传入库：tmp 文件已写完且 sha 已算好，同卷原子改名（2GB 不进内存）。"""
        display_name = (Path(str(name or "file")).name or "file")[:120]
        file_id = f"{sha256_hex[:16]}_{self._safe_name(display_name)}"
        path = self.dir / file_id
        if path.exists():
            Path(tmp_path).unlink(missing_ok=True)
        else:
            os.replace(str(tmp_path), str(path))
        return {"id": file_id, "name": display_name, "size": int(size),
                "sha256": sha256_hex, "url": f"/files/{file_id}"}

    def path(self, file_id: str) -> Optional[Path]:
        if not file_id or "/" in file_id or "\\" in file_id or file_id.startswith("."):
            return None
        p = self.dir / file_id
        return p if p.is_file() else None


class HubServer:
    def __init__(self, host: str, port: int, whitelist_path: Path, state_dir: Path,
                 owner_name: str = "haru", join_password: str = ""):
        self.host = host
        self.port = port
        self.whitelist = Whitelist(whitelist_path)
        state_dir.mkdir(parents=True, exist_ok=True)
        # 接入密码：未在白名单的设备凭它自动登记入网；空 = 仅白名单模式。
        # 白名单已有成员免密。持久化：state_dir/join_password.txt 为准（观察台
        # 可运行时修改）；文件不存在时由启动参数初始化并落盘。
        self._pw_path = state_dir / "join_password.txt"
        if self._pw_path.exists():
            try:
                self.join_password = self._pw_path.read_text(encoding="utf-8").strip()
            except OSError:
                self.join_password = join_password or ""
        else:
            self.join_password = join_password or ""
            self._persist_join_password()
        self.presence = Presence(state_dir / "presence.json")
        self.mailbox = Mailbox(state_dir / "mailbox.db")
        self.message_log = MessageLog(state_dir / "messages.db")
        self.files = FileStore(state_dir / "files")
        self.errors = ErrorLog(state_dir / "errors.db")
        # 挂起的询问/确认（agent→server "ask" 信封存入，观察台 /api/answer 回送）
        self.pending_asks: dict[str, dict] = {}
        self._pending_asks_lock = threading.Lock()
        # 网络话语权（仅内存）：{holder_agent_id, holder_name, persona, since, note}
        # hub 重启清零——避免指向离线端的陈腐状态；持有者掉线自动收回
        self._floor: Optional[dict] = None
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

    def submit_owner_control(self, target: str, action: str,
                             name: Optional[str] = None,
                             extra: Optional[dict] = None) -> dict:
        """观察台中控入口（HTTP 线程调用）：以 owner 身份给指定智能体发中控动作。

        rename 额外做服务端权威更新：白名单 + presence 立即改名并广播全网，
        同时给目标智能体发 control 消息让它更新自己的端侧身份。
        security 等动作携带 extra 载荷原样透传给目标智能体。"""
        if self.loop is None:
            return {"ok": False, "error": "hub 未就绪"}
        if not self.whitelist.agent(target):
            return {"ok": False, "error": f"目标不在白名单: {target}"}
        if action == "rename":
            name = (name or "").strip()
            if not name or len(name) > 32:
                return {"ok": False, "error": "名字须为 1-32 个字符"}
            record = self.whitelist.agent(target) or {}
            if record.get("pubkey"):
                self.whitelist.ensure_agent(target, name, record["pubkey"])
        payload: dict = {"action": action}
        if name:
            payload["name"] = name
        if extra:
            payload.update(extra)
        env = make_envelope(
            sender=self.owner.agent_id, to=target, type_="control",
            payload=payload)
        env["sig"] = self.owner.sign(canonical_bytes(env))

        async def _deliver() -> dict:
            # 中控消息不进消息流水（它是运维动作，不是聊天）；
            # 但记一条系统流水便于观察台展示"中控动作已发出"
            self.message_log.log(self.owner.agent_id, "中控", target, "control",
                                 None, f"{action}{' ' + name if name else ''} → {target}",
                                 env.get("ts"))
            if action == "rename":
                # 服务端权威改名：presence 落盘 + 全网广播（各端 _peer_names 随之更新）
                self.presence.rename(target, name)
                await self._broadcast_presence()
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

    # ---------------------------------------------------------- 接入密码（观察台可改）
    def _persist_join_password(self) -> None:
        tmp = self._pw_path.with_suffix(".tmp")
        tmp.write_text(self.join_password, encoding="utf-8")
        tmp.replace(self._pw_path)

    def join_password_snapshot(self) -> dict:
        return {"enabled": bool(self.join_password),
                "password": self.join_password}

    def submit_join_password(self, password: str) -> dict:
        """观察台设置接入密码（空 = 关闭密码模式，仅白名单）。持久化到 state。"""
        self.join_password = str(password or "").strip()
        try:
            self._persist_join_password()
        except OSError as e:
            return {"ok": False, "error": f"写盘失败: {e}"}
        logger.info("接入密码已更新（%s）", "已开启" if self.join_password else "已关闭")
        self._log_floor("接入密码已更新", self.owner.agent_id)
        return {"ok": True, **self.join_password_snapshot()}

    # ---------------------------------------------------------- 删除已注册智能体
    def submit_remove_agent(self, target: str) -> dict:
        """观察台删除已登记设备：白名单 + presence 移除，在线连接即刻断开。

        注意：密码模式开启时，被删设备若仍持有密码，重连会再次自动登记。
        """
        target = str(target or "").strip()
        if not target or self.whitelist.agent(target) is None:
            return {"ok": False, "error": "目标不在白名单"}
        if target == self.owner.agent_id:
            return {"ok": False, "error": "不能删除 owner 自身身份"}
        name = (self.whitelist.agent(target) or {}).get("name", target)
        ws = self._online.pop(target, None)
        if ws is not None and self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), self.loop)
            except Exception:
                pass
        self.whitelist.remove_agent(target)
        self.presence.remove(target)
        if self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_presence(), self.loop)
            except Exception:
                pass
        self._log_floor(f"已删除智能体 {name}（{target}）", self.owner.agent_id)
        logger.info("观察台删除设备: %s (%s)", target, name)
        return {"ok": True, "removed": target, "name": name}

    # ---------------------------------------------------------- 网络话语权（Floor）
    def floor_snapshot(self) -> Optional[dict]:
        """当前话语权公共视图（welcome/presence/观察台轮询用）。"""
        return dict(self._floor) if self._floor else None

    def _resolve_floor_target(self, target: str) -> Optional[tuple[str, str]]:
        """target → (agent_id, persona)。支持 agent_id / 白名单名 / 简写 "arch"
        （= 上报了 hosts_architect 档案的智能体 + persona arch）。"""
        target = (target or "").strip()
        if not target:
            return None
        if target.lower() == "arch":
            for aid, info in self.presence.snapshot().items():
                prof = info.get("profile") or {}
                if prof.get("hosts_architect"):
                    return aid, "arch"
            return None
        if self.whitelist.agent(target):
            return target, "main"
        for aid in self.whitelist.all_agents():
            rec = self.whitelist.agent(aid) or {}
            if rec.get("name") == target:
                return aid, "main"
        return None

    def submit_floor_request(self, action: str, target: str = "",
                             persona: str = "main", note: str = "") -> dict:
        """观察台/HTTP 线程入口：设置或收回话语权（广播由 loop 线程执行）。"""
        if self.loop is None:
            return {"ok": False, "error": "hub 未就绪"}
        try:
            result = asyncio.run_coroutine_threadsafe(
                self._apply_floor_async(action, target, persona, note,
                                        by=self.owner.agent_id),
                self.loop).result(timeout=10)
        except Exception as e:
            logger.warning("话语权请求失败: %s", e)
            return {"ok": False, "error": str(e)}
        return result

    async def _apply_floor_async(self, action: str, target: str, persona: str,
                                 note: str, by: str) -> dict:
        if action == "clear":
            had = self._floor is not None
            self._floor = None
            if had:
                await self._broadcast_floor()
                self._log_floor("话语权已收回（恢复正常发言判断）", by)
            return {"ok": True, "floor": None}
        if action != "set":
            return {"ok": False, "error": f"未知动作: {action}"}
        persona = "arch" if str(persona).lower() == "arch" else "main"
        resolved = self._resolve_floor_target(target or by)
        if resolved is None:
            return {"ok": False, "error": f"无法解析话语权目标: {target!r}"}
        holder_id, resolved_persona = resolved
        if str(persona).lower() == "arch" or resolved_persona == "arch":
            persona = "arch"
        else:
            persona = "main"
        name = (self.whitelist.agent(holder_id) or {}).get("name", holder_id)
        self._floor = {
            "holder_agent_id": holder_id,
            "holder_name": name,
            "persona": persona,
            "since": time.time(),
            "note": str(note or ""),
        }
        await self._broadcast_floor()
        self._log_floor(
            f"话语权 → {name}（{'arch' if persona == 'arch' else '主'}）", by)
        return {"ok": True, "floor": self.floor_snapshot()}

    async def _broadcast_floor(self) -> None:
        env = make_envelope(sender="server", to="*", type_="floor_state",
                            payload={"floor": self.floor_snapshot()})
        for agent_id in list(self._online):
            await self._send_to(agent_id, env)

    def _log_floor(self, text: str, by: str) -> None:
        """话语权变更记一条运维流水（观察台消息流可见）。"""
        try:
            by_name = (self.whitelist.agent(by) or {}).get("name", "中控")
            self.message_log.log(by, by_name, "*", "control", None,
                                 f"🎙 {text}", now_ts())
        except Exception:
            logger.exception("话语权流水记录失败")
        logger.info("话语权: %s", text)

    # ---------------------------------------------------------- 挂起询问（观察台作答）
    def pending_asks_snapshot(self) -> list[dict]:
        """观察台轮询用：未过期挂起询问/确认列表（公共视图，不含签名等）。"""
        now = time.time()
        with self._pending_asks_lock:
            for rid in [k for k, v in self.pending_asks.items() if v["expires"] < now]:
                del self.pending_asks[rid]
            return [
                {"request_id": v["request_id"], "kind": v["kind"],
                 "agent_id": v["agent_id"], "agent_name": v["agent_name"],
                 "data": v["data"], "expires_in": int(v["expires"] - now)}
                for v in self.pending_asks.values()
            ]

    def submit_answer(self, request_id: str, answer) -> dict:
        """观察台作答（HTTP 线程调用）：control 信封回送目标智能体并放行其工具线程。"""
        with self._pending_asks_lock:
            pending = self.pending_asks.pop(request_id, None)
        if pending is None:
            return {"ok": False, "error": "请求不存在或已过期"}
        if pending["kind"] == "confirm":
            payload = {"action": "confirm_result",
                       "confirm_id": (pending["data"] or {}).get("confirm_id", request_id),
                       "approved": bool(answer)}
        else:
            payload = {"action": "answer", "request_id": request_id, "answer": answer}
        env = make_envelope(
            sender=self.owner.agent_id, to=pending["agent_id"],
            type_="control", payload=payload)
        env["sig"] = self.owner.sign(canonical_bytes(env))
        try:
            delivered = asyncio.run_coroutine_threadsafe(
                self._send_to(pending["agent_id"], env), self.loop).result(timeout=10)
            if not delivered:
                self.mailbox.put(pending["agent_id"], env)
                return {"ok": True, "queued": True}
        except Exception as e:
            # 回送失败时恢复挂起，观察台可重试；不得静默吞掉主人作答
            with self._pending_asks_lock:
                self.pending_asks.setdefault(request_id, pending)
            logger.warning("答案回送失败（已恢复挂起）: %s", e)
            return {"ok": False, "error": str(e)}
        logger.info("答案已回送: %s → %s", request_id, pending["agent_id"])
        return {"ok": True}

    # ---------------------------------------------------------- 连接生命周期
    async def handle(self, ws) -> None:
        agent_id = None
        try:
            agent_id, profile = await self._handshake(ws)
            if agent_id is None:
                return
            old_ws = self._online.get(agent_id)
            self._online[agent_id] = ws
            if old_ws is not None and old_ws is not ws:
                # 先登记新连接再关闭旧连接：旧 handler 的 finally 看到登记已替换，
                # 不会把仍在线的新连接误判离线
                try:
                    await old_ws.close(code=1000, reason="replaced by reconnect")
                except Exception:
                    logger.warning("关闭重复身份旧连接失败", exc_info=True)
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
                # 同一身份可能已建立新连接（重连竞态）：只有当前登记的仍是
                # 本连接时才允许它下线——否则旧连接的迟来清理会把新连接
                # 误判离线（曾导致 presence 幻影离线）
                if self._online.get(agent_id) is ws:
                    self._online.pop(agent_id, None)
                    self.presence.set_offline(agent_id)
                    await self._broadcast_presence()
                    logger.info("下线: %s (last_seen 已落盘)", agent_id)
                    # 话语权持有者掉线 → 自动收回（否则全网被沉积死掉的话语权锁住）
                    if self._floor and self._floor.get("holder_agent_id") == agent_id:
                        self._floor = None
                        await self._broadcast_floor()
                        self._log_floor("话语权持有者离线，自动收回", agent_id)

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
        # CONFIRMED：白名单已有成员直接过；未收录的设备凭"接入密码"自动登记。
        # 签名验证始终强制（防伪造身份），密码只解决"新设备入网"这一步。
        record = self.whitelist.agent(agent_id)
        if record is None and self.join_password:
            if str(payload.get("password") or "") == self.join_password:
                self.whitelist.ensure_agent(
                    agent_id, str(payload.get("name") or agent_id[:12]), pubkey)
                record = self.whitelist.agent(agent_id)
                logger.info("新设备凭接入密码自动登记: %s (%s)",
                            agent_id, record.get("name") if record else "?")
        if record is None or record.get("pubkey") != pubkey:
            await ws.send(dumps(self._auth_error(env, "not_in_whitelist")))
            logger.warning("拒绝未知设备: %s", agent_id)
            return None, None
        if not Identity.verify(pubkey, canonical_bytes(env), env.get("sig", "")):
            await ws.send(dumps(self._auth_error(env, "bad_signature")))
            return None, None
        welcome = make_envelope(sender="server", to=agent_id, type_="welcome",
                                payload={"agent_id": agent_id, "server_ts": now_ts(),
                                         "floor": self.floor_snapshot()})
        await ws.send(dumps(welcome))
        logger.info("确认设备: %s (%s)", agent_id, record.get("name"))
        # 两段式握手：hello 里即使带了 profile 也一律忽略（未入白名单的机器不该
        # 在握手阶段外泄设备信息）；档案由 welcome 后的 profile_update 上报
        return agent_id, None

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
            p_payload = env.get("payload") or {}
            state = p_payload.get("state", "idle")
            attentive = p_payload.get("attentive")
            if self.presence.set_state(sender, state, attentive):
                await self._broadcast_presence()
            return
        if t == "presence_query":
            reply = make_envelope(sender="server", to=sender, type_="presence_state",
                                  payload={"agents": self.presence.snapshot(),
                                           "floor": self.floor_snapshot()})
            await self._send_to(sender, reply)
            return
        if t == "mailbox_pull":
            # 有序至少一次投递：只有 socket 写入成功才 ack；失败保留当前及后续消息
            for row_id, queued in self.mailbox.pending(sender):
                if not await self._send_to(sender, queued):
                    break
                self.mailbox.ack(row_id)
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
        if t == "ask":
            # 智能体的交互式提问/高危确认（AskUser / confirm）→ 存挂起表，
            # 观察台轮询 /api/requests 渲染按钮，主人作答经 /api/answer 回送
            record = self.whitelist.agent(sender)
            if record is None or not Identity.verify(
                    record["pubkey"], canonical_bytes(env), env.get("sig", "")):
                logger.warning("ask 验签失败 from=%s", sender)
                return
            p = env.get("payload") or {}
            request_id = str(p.get("request_id") or "")
            kind = str(p.get("kind") or "question")
            if not request_id or kind not in ("question", "confirm"):
                return
            ttl = float(p.get("ttl") or 400)
            with self._pending_asks_lock:
                self.pending_asks[request_id] = {
                    "request_id": request_id,
                    "kind": kind,
                    "agent_id": sender,
                    "agent_name": record.get("name", sender),
                    "data": p.get("data") or {},
                    "ts": time.time(),
                    "expires": time.time() + min(max(ttl, 30), 1800),
                }
            logger.info("挂起询问登记: %s %s from=%s", kind, request_id, sender)
            return
        if t == "floor":
            # 话语权切换（任何白名单智能体可调用；单主信任网，全量记日志）
            record = self.whitelist.agent(sender)
            if record is None or not Identity.verify(
                    record["pubkey"], canonical_bytes(env), env.get("sig", "")):
                logger.warning("floor 验签失败 from=%s", sender)
                return
            p = env.get("payload") or {}
            await self._apply_floor_async(
                str(p.get("action") or ""),
                str(p.get("target") or ""),
                str(p.get("persona") or "main"),
                str(p.get("note") or ""),
                by=sender)
            return
        if t == "error_report":
            record = self.whitelist.agent(sender)
            if record is None or not Identity.verify(
                    record["pubkey"], canonical_bytes(env), env.get("sig", "")):
                logger.warning("error_report 验签失败 from=%s", sender)
                return
            err = (env.get("payload") or {}).get("record")
            if not isinstance(err, dict) or not err.get("id"):
                logger.warning("error_report 载荷非法 from=%s", sender)
                return
            self.errors.upsert(sender, record.get("name", sender), err)
            logger.info("错误流登记: %s [%s/%s] %s",
                        err.get("id"), err.get("blocking"), err.get("cause"),
                        str(err.get("message", ""))[:120])
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
                            payload={"agents": self.presence.snapshot(),
                                     "floor": self.floor_snapshot()})
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
    ap.add_argument("--join-password", default="",
                    help="新设备接入密码（空=仅白名单模式；已有成员免密）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    server = HubServer(args.host, args.port, Path(args.whitelist), Path(args.state_dir),
                       owner_name=args.owner_name, join_password=args.join_password)
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
            pending_asks=server.pending_asks_snapshot,
            on_answer=server.submit_answer,
            on_floor=server.submit_floor_request,
            floor_snapshot=server.floor_snapshot,
            error_log=server.errors,
            join_password_snapshot=server.join_password_snapshot,
            on_set_join_password=server.submit_join_password,
            on_remove_agent=server.submit_remove_agent,
        )
        observer.start()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
