import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from configurationLoader import config
from Core.ToolCall import ToolCall
from Memory.repositories.L0Repository import L0Repository


class WorkingMemory:
    def __init__(self):
        self.daily_dir = Path(config.get("memory.daily.dir", "./runtime_memory/daily"))
        self.retention_days = max(1, int(config.get("memory.daily.retention_days", 7)))
        self.sqlite_enabled = bool(config.get("memory.sqlite.enabled", True))
        self.daily_json_enabled = bool(config.get("memory.export.daily_json_enabled", True))
        self.session_id = str(config.get("memory.session_id", "default"))
        self.l0_repo = L0Repository() if self.sqlite_enabled else None
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now():
        return datetime.now().replace(microsecond=0).isoformat()

    @staticmethod
    def _sanitize_text(text):
        return str(text).encode("utf-8", errors="replace").decode("utf-8")

    @staticmethod
    def _fallback_timestamp(date_str: str):
        return f"{date_str}T00:00:00"

    def _day_path(self, date_str: str):
        return self.daily_dir / f"{date_str}.json"

    def _empty_day(self, date_str: str):
        now = self._now()
        return {
            "date": date_str,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "message_count": 0,
        }

    def _serialize_value(self, value):
        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                self._sanitize_text(key): self._serialize_value(val)
                for key, val in value.items()
            }
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return self._sanitize_text(value)

    def _coerce_message(self, message):
        if isinstance(message, ToolCall):
            return message.to_record()

        if not isinstance(message, dict):
            raise TypeError("WorkingMemory messages must be dict or ToolCall")

        if message.get("message_type") == "tool_call":
            tool_call = ToolCall.from_record(message)
            if tool_call is not None:
                record = tool_call.to_record(timestamp=message.get("timestamp"))
                if "id" in message:
                    record["id"] = message["id"]
                content = message.get("content")
                if isinstance(content, list):
                    record["content"] = self._serialize_value(content)
                return record

        return dict(message)

    def _normalize_message(self, message, fallback_timestamp=None):
        source_message = self._coerce_message(message)
        normalized = {
            str(key): self._serialize_value(value)
            for key, value in source_message.items()
        }
        normalized["timestamp"] = (
            source_message.get("timestamp") or fallback_timestamp or self._now()
        )
        if "id" not in normalized:
            normalized["id"] = f"msg_{uuid.uuid4().hex[:8]}"
        return normalized

    def _read_day_file(self, path: Path):
        date_str = path.stem
        try:
            raw_text = path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return self._empty_day(date_str)

        if isinstance(data, dict) and "messages" in data:
            messages = [
                self._normalize_message(message, self._fallback_timestamp(date_str))
                for message in data.get("messages", [])
                if isinstance(message, dict)
            ]
            return {
                "date": data.get("date", date_str),
                "created_at": data.get("created_at", self._fallback_timestamp(date_str)),
                "updated_at": data.get("updated_at", self._fallback_timestamp(date_str)),
                "messages": messages,
                "message_count": len(messages),
            }

        legacy_messages = []
        if isinstance(data, dict) and "conversations" in data:
            legacy_messages = data.get("conversations", [])
        elif isinstance(data, list):
            legacy_messages = data

        messages = [
            self._normalize_message(message, self._fallback_timestamp(date_str))
            for message in legacy_messages
            if isinstance(message, dict)
        ]
        day_payload = self._empty_day(date_str)
        day_payload["created_at"] = self._fallback_timestamp(date_str)
        day_payload["updated_at"] = self._fallback_timestamp(date_str)
        day_payload["messages"] = messages
        day_payload["message_count"] = len(messages)
        return day_payload

    def _write_day(self, payload: dict):
        path = self._day_path(payload["date"])
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(serialized, encoding="utf-8")
        temp_path.replace(path)

    def append(self, content):
        message = self._normalize_message(content)

        # Dual-write: SQLite (new primary store) + daily JSON (legacy export)
        if self.sqlite_enabled and self.l0_repo:
            self.l0_repo.append_message(message, session_id=self.session_id)

        if self.daily_json_enabled:
            today = date.today().isoformat()
            day_payload = self.read_day(today) or self._empty_day(today)
            if not day_payload["messages"]:
                day_payload["created_at"] = message["timestamp"]
            day_payload["messages"].append(message)
            day_payload["updated_at"] = message["timestamp"]
            day_payload["message_count"] = len(day_payload["messages"])
            self._write_day(day_payload)

    def list_daily_files(self):
        return sorted(self.daily_dir.glob("*.json"), key=lambda path: path.stem)

    def list_expired_days(self):
        if self.sqlite_enabled and self.l0_repo:
            return self.l0_repo.list_expired_days(self.retention_days, session_id=self.session_id)

        cutoff = date.today() - timedelta(days=self.retention_days - 1)
        expired_days = []
        for path in self.list_daily_files():
            try:
                file_day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if file_day < cutoff:
                expired_days.append(path.stem)
        return expired_days

    def read_day(self, date_str: str):
        if self.sqlite_enabled and self.l0_repo:
            messages = self.l0_repo.list_day_messages(date_str, session_id=self.session_id)
            return {"date": date_str, "messages": messages, "message_count": len(messages)}

        path = self._day_path(date_str)
        if not path.exists():
            return None
        return self._read_day_file(path)

    def delete_day(self, date_str: str):
        if self.sqlite_enabled and self.l0_repo:
            self.l0_repo.delete_day(date_str, session_id=self.session_id)
        if self.daily_json_enabled:
            path = self._day_path(date_str)
            if path.exists():
                path.unlink()

    def clear_today(self):
        """清空今天的消息（当前会话）"""
        today = date.today().isoformat()
        self.delete_day(today)

    def clear_all(self):
        """清空当前会话的全部消息（所有日期）。"""
        if self.sqlite_enabled and self.l0_repo:
            self.l0_repo.delete_all_for_session(self.session_id)
        if self.daily_json_enabled:
            for path in self.list_daily_files():
                try:
                    path.unlink()
                except OSError:
                    pass

    def get_recent_messages(self):
        if self.sqlite_enabled and self.l0_repo:
            cutoff = (date.today() - timedelta(days=self.retention_days - 1)).isoformat()
            return self.l0_repo.list_messages(session_id=self.session_id, start_day=cutoff)

        cutoff = date.today() - timedelta(days=self.retention_days - 1)
        messages = []
        for path in self.list_daily_files():
            try:
                file_day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if file_day < cutoff:
                continue
            day_payload = self._read_day_file(path)
            messages.extend(day_payload["messages"])
        return sorted(messages, key=lambda message: message.get("timestamp", ""))

    def get_messages(self):
        return self.get_recent_messages()

