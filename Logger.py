import json
from datetime import datetime
from pathlib import Path


class ConversationLogger:
    def __init__(self, log_dir="./logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"conversation_{timestamp}.log"
        self.session_start = datetime.now()
        self._write_header()

    def _write_header(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"=== TableHelper Conversation Log ===\n")
            f.write(f"Session started: {self.session_start.isoformat()}\n")
            f.write(f"=" * 80 + "\n\n")

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def _write(self, content):
        try:
            with open(self.log_file, "a", encoding="utf-8", errors="replace") as f:
                f.write(content)
                f.flush()
        except Exception as e:
            pass

    def log_user_input(self, user_input):
        self._write(f"[{self._timestamp()}] USER:\n{user_input}\n\n")

    def log_tool_call(self, step, tool_name, arguments, result_preview):
        self._write(f"[{self._timestamp()}] TOOL CALL (step {step}):\n")
        self._write(f"  Tool: {tool_name}\n")
        self._write(f"  Arguments: {json.dumps(arguments, ensure_ascii=False, indent=2)}\n")
        self._write(f"  Result: {result_preview}\n\n")

    def log_reflection(self, step, reflection_text):
        self._write(f"[{self._timestamp()}] REFLECTION (step {step}):\n{reflection_text}\n\n")

    def log_assistant_response(self, response):
        self._write(f"[{self._timestamp()}] ASSISTANT:\n{response}\n\n")
        self._write("-" * 80 + "\n\n")

    def log_error(self, error_message):
        self._write(f"[{self._timestamp()}] ERROR:\n{error_message}\n\n")

    def log_memory_event(self, event):
        self._write(f"[{self._timestamp()}] MEMORY: {event}\n")

    def get_log_path(self):
        return str(self.log_file)
