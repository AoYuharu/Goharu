"""危险命令交互确认管理器。

require_confirmation 清单中的命令（del/rmdir/taskkill/reg 等）执行前，
阻塞工具线程并向 TUI 发起确认请求；用户选择 Yes/No 后放行或拒绝。

数据流：
  run_cmd(危险命令) → ConfirmationManager.request() 阻塞工具线程
    → notifier 回调 → Gateway 发 tool.confirm_request 事件 → TUI 弹确认框
    → 用户选择 → TUI 调 agent.confirm_response → Gateway resolve → 线程放行
"""

import threading
import uuid
from typing import Callable, Optional

# 用户长时间不响应时自动拒绝（避免工具线程永久挂起）
DEFAULT_TIMEOUT = 180


class ConfirmationManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._notifier: Optional[Callable[[str, str, str], None]] = None
        self._pending = {}  # confirm_id -> {"event": Event, "approved": bool}
        self._pending_lock = threading.Lock()
        self._initialized = True

    def set_notifier(self, callback: Callable[[str, str, str], None]):
        """Gateway 注册：callback(confirm_id, cmd, description) → 发事件给 TUI"""
        self._notifier = callback

    def request(self, cmd: str, description: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
        """阻塞当前（工具）线程，等待用户确认。返回 True=允许执行。"""
        if self._notifier is None:
            return False  # 无前端（无 TUI），拒绝
        confirm_id = uuid.uuid4().hex[:12]
        ev = threading.Event()
        with self._pending_lock:
            self._pending[confirm_id] = {"event": ev, "approved": False}
        try:
            self._notifier(confirm_id, cmd, description)
        except Exception:
            pass
        approved = ev.wait(timeout)
        with self._pending_lock:
            entry = self._pending.pop(confirm_id, None)
        if not approved or entry is None:
            return False  # 超时或异常 → 拒绝
        return bool(entry["approved"])

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        """用户已选择。返回是否找到对应请求。"""
        with self._pending_lock:
            entry = self._pending.get(confirm_id)
            if entry is None:
                return False
            entry["approved"] = bool(approved)
            entry["event"].set()
            return True


# 全局单例
confirmation_manager = ConfirmationManager()
