"""MessageBox：端侧消息分类聚合存储。

核心目的：智能体不一股脑收所有消息——消息先落盒（SQLite append-only），
模型只看到分类计数摘要（经 reminders 便利贴进当轮上下文），展开由
阈值触发或模型主动调 serverMessageHandler(read) 完成。

类别：mention（群@我）/ dm（私聊）/ group（群闲聊），维度含 peer
（dm 的对方 / group 的群名）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .protocol import CLASS_DM, CLASS_GROUP, CLASS_MENTION, now_ts

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT UNIQUE,          -- envelope id，幂等去重（重连/离线拉取可能重复）
    class TEXT NOT NULL,         -- mention | dm | group
    peer TEXT NOT NULL,          -- dm: 对方 agent_id；group: 群名
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    mentions TEXT NOT NULL DEFAULT '[]',
    ts TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mb_class_read ON messages(class, read);
CREATE INDEX IF NOT EXISTS idx_mb_peer ON messages(peer, read);
"""


class MessageBox:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            # 旧库迁移：附件列是后加的
            cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
            if "attachments" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
                conn.commit()
            if "mentions" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN mentions TEXT NOT NULL DEFAULT '[]'")
                conn.commit()
        logger.info("MessageBox 初始化: %s", self._path)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------ 写
    def add(self, msg_id: Optional[str], class_: str, peer: str,
            sender: str, text: str, ts: Optional[str] = None,
            attachments: Optional[list] = None,
            mentions: Optional[list] = None) -> bool:
        """入盒。msg_id 重复（重连重投）返回 False 不重复入账。"""
        try:
            self._conn().execute(
                "INSERT INTO messages(msg_id, class, peer, sender, text, ts, attachments, mentions)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (msg_id, class_, peer, sender, text, ts or now_ts(),
                 json.dumps(attachments or [], ensure_ascii=False),
                 json.dumps(mentions or [], ensure_ascii=False)),
            )
            self._conn().commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # ------------------------------------------------------------------ 读
    def summary(self) -> dict:
        """分类计数摘要：{class: {count, peers: {peer: {count, last_preview, last_ts}}}}。"""
        rows = self._conn().execute(
            "SELECT class, peer, sender, text, ts FROM messages WHERE read = 0 "
            "ORDER BY id ASC"
        ).fetchall()
        out: dict = {}
        for r in rows:
            cls = out.setdefault(r["class"], {"count": 0, "peers": {}})
            cls["count"] += 1
            p = cls["peers"].setdefault(r["peer"], {"count": 0, "last_preview": "", "last_ts": ""})
            p["count"] += 1
            p["last_preview"] = f'{r["sender"]}: {r["text"][:40]}'
            p["last_ts"] = r["ts"]
        return out

    def unread_count(self, class_: Optional[str] = None) -> int:
        if class_ is None:
            return int(self._conn().execute(
                "SELECT COUNT(*) c FROM messages WHERE read = 0").fetchone()["c"])
        return int(self._conn().execute(
            "SELECT COUNT(*) c FROM messages WHERE read = 0 AND class = ?",
            (class_,)).fetchone()["c"])

    def latest_ts(self, class_: Optional[str] = None) -> Optional[str]:
        """某类最近一条未读消息的时间戳（安静期判断用）。"""
        if class_ is None:
            row = self._conn().execute(
                "SELECT ts FROM messages WHERE read = 0 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = self._conn().execute(
                "SELECT ts FROM messages WHERE read = 0 AND class = ? "
                "ORDER BY id DESC LIMIT 1", (class_,)).fetchone()
        return row["ts"] if row else None

    def latest_ts_from(self, senders, class_: Optional[str] = None) -> Optional[str]:
        """来自指定发送者集合的最近一条未读消息时间戳（主人消息快速阅读用）。"""
        if not senders:
            return None
        marks = ",".join("?" * len(senders))
        if class_ is None:
            row = self._conn().execute(
                f"SELECT ts FROM messages WHERE read = 0 AND sender IN ({marks}) "
                "ORDER BY id DESC LIMIT 1", list(senders)).fetchone()
        else:
            row = self._conn().execute(
                f"SELECT ts FROM messages WHERE read = 0 AND sender IN ({marks}) "
                "AND class = ? ORDER BY id DESC LIMIT 1",
                list(senders) + [class_]).fetchone()
        return row["ts"] if row else None

    def mark_read_by_msg_id(self, msg_id: Optional[str]) -> None:
        """按 envelope id 标记已读（被立即唤醒的消息从收件箱消失）。"""
        if not msg_id:
            return
        self._conn().execute("UPDATE messages SET read = 1 WHERE msg_id = ?", (msg_id,))
        self._conn().commit()

    def clear(self) -> None:
        """清空（中控 clear_messagebox 用）。"""
        self._conn().execute("DELETE FROM messages")
        self._conn().commit()

    def read(
        self,
        class_: Optional[str] = None,
        peer: Optional[str] = None,
        since_ts: Optional[str] = None,
        limit: int = 50,
        mark_read: bool = True,
    ) -> list[dict]:
        """展开消息。class_/peer/since 可组合过滤。

        since_ts 显式给出 = 时间回溯（含已读，默认不改已读标记）；
        否则只展开未读，并在 mark_read=True 时把本次返回的条目标记已读。
        """
        sql = ["SELECT id, msg_id, class, peer, sender, text, ts, read, attachments,"
               " mentions FROM messages WHERE 1=1"]
        params: list = []
        if class_:
            sql.append("AND class = ?")
            params.append(class_)
        if peer:
            sql.append("AND peer = ?")
            params.append(peer)
        if since_ts is not None:
            sql.append("AND ts >= ?")
            params.append(since_ts)
        else:
            sql.append("AND read = 0")
        sql.append("ORDER BY id ASC LIMIT ?")
        params.append(int(limit))
        rows = [dict(r) for r in self._conn().execute("\n".join(sql), params).fetchall()]
        for r in rows:
            try:
                r["attachments"] = json.loads(r.get("attachments") or "[]")
            except ValueError:
                r["attachments"] = []
            try:
                r["mentions"] = json.loads(r.get("mentions") or "[]")
            except ValueError:
                r["mentions"] = []
        if mark_read and rows and since_ts is None:
            ids = [r["id"] for r in rows]
            self._conn().execute(
                f"UPDATE messages SET read = 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids)
            self._conn().commit()
        return rows

    def digest_text(self) -> str:
        """便利贴格式的一行摘要（进当轮上下文用）。"""
        s = self.summary()
        if not s:
            return ""
        total = sum(c["count"] for c in s.values())
        parts = []
        label = {CLASS_MENTION: "@你", CLASS_DM: "私聊", CLASS_GROUP: "群消息"}
        for cls in (CLASS_MENTION, CLASS_DM, CLASS_GROUP):
            if cls not in s:
                continue
            peers = "、".join(f"[{p}]×{d['count']}" for p, d in s[cls]["peers"].items())
            parts.append(f"{label[cls]} {peers}")
        return f"📬 messagebox: {total} 条未读 — " + " · ".join(parts)
