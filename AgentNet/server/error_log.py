"""HaruNet 错误流存储：各智能体统一错误记录（观察台错误 tab 数据源）。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class ErrorLog:
    def __init__(self, db_path: Path, max_rows: int = 5000):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._max_rows = max_rows
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS errors("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "error_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL,"
            "agent_id TEXT NOT NULL, agent_name TEXT NOT NULL,"
            "scope TEXT, severity TEXT, blocking TEXT, cause TEXT,"
            "source TEXT, session_id TEXT, message TEXT, traceback TEXT,"
            "fingerprint TEXT, status TEXT, wake_status TEXT, attempts INTEGER,"
            "updated_ts TEXT, extra TEXT)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_err_fp ON errors(fingerprint)")
        self._conn.commit()
        self._prune()

    def _prune(self) -> None:
        cur = self._conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        if cur > self._max_rows:
            self._conn.execute(
                "DELETE FROM errors WHERE id IN (SELECT id FROM errors "
                "ORDER BY id ASC LIMIT ?)", (cur - self._max_rows,))
            self._conn.commit()

    def upsert(self, agent_id: str, agent_name: str, record: dict) -> dict:
        error_id = str(record.get("id") or "")
        if not error_id:
            return {}
        row = {
            "error_id": error_id,
            "ts": str(record.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S")),
            "agent_id": str(agent_id),
            "agent_name": str(agent_name or agent_id),
            "scope": str(record.get("scope") or "")[:120],
            "severity": str(record.get("severity") or "error")[:20],
            "blocking": str(record.get("blocking") or "feature")[:20],
            "cause": str(record.get("cause") or "runtime")[:20],
            "source": str(record.get("source") or "")[:160],
            "session_id": str(record.get("session") or "")[:80],
            "message": str(record.get("message") or "")[:4000],
            "traceback": str(record.get("traceback") or "")[:20000],
            "fingerprint": str(record.get("fingerprint") or "")[:32],
            "status": str(record.get("status") or "new")[:32],
            "wake_status": str(record.get("wake_status") or "none")[:32],
            "attempts": int(record.get("attempts") or 0),
            "updated_ts": str(record.get("updated_ts") or record.get("ts") or ""),
            "extra": json.dumps(record.get("extra") or {}, ensure_ascii=False)[:4000],
        }
        self._conn.execute(
            "INSERT INTO errors(error_id, ts, agent_id, agent_name, scope, severity,"
            " blocking, cause, source, session_id, message, traceback, fingerprint,"
            " status, wake_status, attempts, updated_ts, extra)"
            " VALUES(:error_id,:ts,:agent_id,:agent_name,:scope,:severity,:blocking,"
            ":cause,:source,:session_id,:message,:traceback,:fingerprint,:status,"
            ":wake_status,:attempts,:updated_ts,:extra)"
            " ON CONFLICT(error_id) DO UPDATE SET"
            " ts=excluded.ts, agent_id=excluded.agent_id, agent_name=excluded.agent_name,"
            " scope=excluded.scope, severity=excluded.severity, blocking=excluded.blocking,"
            " cause=excluded.cause, source=excluded.source, session_id=excluded.session_id,"
            " message=excluded.message, traceback=excluded.traceback,"
            " fingerprint=excluded.fingerprint, status=excluded.status,"
            " wake_status=excluded.wake_status, attempts=excluded.attempts,"
            " updated_ts=excluded.updated_ts, extra=excluded.extra",
            row)
        self._conn.commit()
        self._prune()
        got = self._conn.execute(
            "SELECT id FROM errors WHERE error_id = ?", (error_id,)).fetchone()
        return {"ok": True, "row_id": got[0] if got else 0}

    def recent(self, limit: int = 100, after: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM errors WHERE id > ? ORDER BY id ASC LIMIT ?",
            (int(after), int(limit))).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM errors LIMIT 0").description]
        out = []
        for r in rows:
            item = dict(zip(cols, r))
            try:
                item["extra"] = json.loads(item.get("extra") or "{}")
            except ValueError:
                item["extra"] = {}
            item["row_id"] = item.pop("id")
            out.append(item)
        return out
