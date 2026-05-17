from datetime import date, datetime

from Memory.MemoryDB import MemoryDB


class L0Repository:
    def __init__(self, db=None):
        self.db = db or MemoryDB()

    @staticmethod
    def _today_key():
        return date.today().isoformat()

    @staticmethod
    def _timestamp_to_day_key(timestamp):
        text = str(timestamp or "")
        if len(text) >= 10:
            return text[:10]
        return L0Repository._today_key()

    def get_or_create_turn(self, session_id="default", day_key=None, started_at=None, status="active"):
        day_key = day_key or self._today_key()
        started_at = started_at or datetime.now().replace(microsecond=0).isoformat()
        row = self.db.query_one(
            """
            SELECT * FROM l0_turns
            WHERE session_id = ? AND day_key = ? AND status = ?
            ORDER BY id DESC LIMIT 1
            """,
            (session_id, day_key, status),
        )
        if row is not None:
            return dict(row)
        cursor = self.db.execute(
            """
            INSERT INTO l0_turns(session_id, started_at, ended_at, day_key, status, pipeline_state)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, started_at, started_at, day_key, status, "pending"),
        )
        return self.get_turn(cursor.lastrowid)

    def get_turn(self, turn_id):
        row = self.db.query_one("SELECT * FROM l0_turns WHERE id = ?", (int(turn_id),))
        return dict(row) if row is not None else None

    def append_message(self, message, session_id="default"):
        timestamp = str(message.get("timestamp") or datetime.now().replace(microsecond=0).isoformat())
        day_key = self._timestamp_to_day_key(timestamp)
        turn = self.get_or_create_turn(session_id=session_id, day_key=day_key, started_at=timestamp)
        content = message.get("content")
        content_text = content if isinstance(content, str) else None
        content_json = None if isinstance(content, str) else self.db.dumps_json(content)
        metadata = {
            key: value
            for key, value in message.items()
            if key not in {"role", "content", "tool_name", "message_type", "timestamp", "token_estimate"}
        }
        cursor = self.db.execute(
            """
            INSERT INTO l0_messages(
                turn_id, role, content, content_json, tool_name, message_type,
                timestamp, token_estimate, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(turn["id"]),
                str(message.get("role") or "assistant"),
                content_text,
                content_json,
                message.get("tool_name") or message.get("name"),
                message.get("message_type"),
                timestamp,
                int(message.get("token_estimate") or 0) if message.get("token_estimate") is not None else None,
                self.db.dumps_json(metadata) if metadata else None,
            ),
        )
        self.db.execute(
            "UPDATE l0_turns SET ended_at = ?, pipeline_state = ? WHERE id = ?",
            (timestamp, "pending", int(turn["id"])),
        )
        return self.get_message(cursor.lastrowid)

    def get_message(self, message_id):
        row = self.db.query_one("SELECT * FROM l0_messages WHERE id = ?", (int(message_id),))
        return self._row_to_message(row) if row is not None else None

    def list_messages(self, session_id=None, start_day=None, limit=None):
        sql = [
            """
            SELECT m.*
            FROM l0_messages m
            JOIN l0_turns t ON t.id = m.turn_id
            WHERE 1=1
            """
        ]
        params = []
        if session_id is not None:
            sql.append("AND t.session_id = ?")
            params.append(str(session_id))
        if start_day is not None:
            sql.append("AND t.day_key >= ?")
            params.append(str(start_day))
        sql.append("ORDER BY m.timestamp ASC, m.id ASC")
        if limit is not None:
            sql.append("LIMIT ?")
            params.append(int(limit))
        rows = self.db.query_all("\n".join(sql), tuple(params))
        return [self._row_to_message(row) for row in rows]

    def list_day_messages(self, day_key, session_id="default"):
        rows = self.db.query_all(
            """
            SELECT m.*
            FROM l0_messages m
            JOIN l0_turns t ON t.id = m.turn_id
            WHERE t.day_key = ? AND t.session_id = ?
            ORDER BY m.timestamp ASC, m.id ASC
            """,
            (str(day_key), str(session_id)),
        )
        return [self._row_to_message(row) for row in rows]

    def list_turn_messages(self, turn_id):
        rows = self.db.query_all(
            "SELECT * FROM l0_messages WHERE turn_id = ? ORDER BY timestamp ASC, id ASC",
            (int(turn_id),),
        )
        return [self._row_to_message(row) for row in rows]

    def list_expired_days(self, retention_days, session_id="default"):
        cutoff = date.today().toordinal() - max(1, int(retention_days)) + 1
        rows = self.db.query_all(
            "SELECT DISTINCT day_key FROM l0_turns WHERE session_id = ? ORDER BY day_key ASC",
            (str(session_id),),
        )
        expired = []
        for row in rows:
            day_key = row["day_key"]
            try:
                day_obj = date.fromisoformat(str(day_key))
            except ValueError:
                continue
            if day_obj.toordinal() < cutoff:
                expired.append(str(day_key))
        return expired

    def delete_day(self, day_key, session_id="default"):
        self.db.execute(
            "DELETE FROM l0_turns WHERE day_key = ? AND session_id = ?",
            (str(day_key), str(session_id)),
        )

    def delete_all_for_session(self, session_id="default"):
        """Delete ALL turns (and cascade messages) for a session."""
        self.db.execute(
            "DELETE FROM l0_turns WHERE session_id = ?",
            (str(session_id),),
        )

    def mark_turn_pipeline_state(self, turn_id, pipeline_state, status=None):
        if status is None:
            self.db.execute(
                "UPDATE l0_turns SET pipeline_state = ? WHERE id = ?",
                (str(pipeline_state), int(turn_id)),
            )
        else:
            self.db.execute(
                "UPDATE l0_turns SET pipeline_state = ?, status = ? WHERE id = ?",
                (str(pipeline_state), str(status), int(turn_id)),
            )

    def _row_to_message(self, row):
        data = dict(row)
        content = data.get("content")
        if content is None and data.get("content_json"):
            content = self.db.loads_json(data.get("content_json"), default=data.get("content_json"))
        metadata = self.db.loads_json(data.get("metadata_json"), default={}) or {}
        message = {
            "id": metadata.get("id") or f"msg_db_{data['id']}",
            "role": data.get("role") or "assistant",
            "content": content if content is not None else "",
            "timestamp": data.get("timestamp") or "",
            "turn_id": data.get("turn_id"),
        }
        if data.get("tool_name"):
            message["tool_name"] = data.get("tool_name")
        if data.get("message_type"):
            message["message_type"] = data.get("message_type")
        if data.get("token_estimate") is not None:
            message["token_estimate"] = data.get("token_estimate")
        for key, value in metadata.items():
            if key not in message:
                message[key] = value
        return message
