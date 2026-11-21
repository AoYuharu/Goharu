"""交互式提问管理器（AskUser 工具的阻塞/应答中枢）。

模型调用 AskUser 工具需要用户在多个选项中做选择时，阻塞工具线程并向
前端（TUI）发起提问请求；用户选择后把答案送回工具线程作为工具结果返回。

数据流（镜像 confirmation_manager 的确认链）：
  AskUser(question, options) → QuestionManager.request() 阻塞工具线程
    → notifier 回调 → Gateway 发提问事件 → TUI 渲染选项面板
    → 用户选择 → 前端 resolve → 线程放行，答案作为工具结果返回
"""

import threading
import uuid
from typing import Callable, Optional

# 用户长时间不响应时自动超时（避免工具线程永久挂起）
DEFAULT_TIMEOUT = 300


class QuestionManager:
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
        self._notifier: Optional[Callable[[str, str, list], None]] = None
        self._pending = {}  # request_id -> {"event": Event, "answer": Optional[str]}
        self._pending_lock = threading.Lock()
        self._initialized = True

    def set_notifier(self, callback: Optional[Callable[[str, dict], None]]):
        """前端注册：callback(request_id, payload) → 发事件给 TUI。
        payload 形如 {"questions": [{question, options, multi_select}, ...]}。
        传 None 表示注销（无前端可用）。"""
        self._notifier = callback

    @property
    def has_notifier(self) -> bool:
        """只读判据：当前是否有前端可接收提问。"""
        return self._notifier is not None

    def request(self, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
        """阻塞当前（工具）线程，等待用户回答。返回用户答案（字符串；
        批量面板回传 JSON 字符串）；超时或无前端（notifier 未注册）返回 None。"""
        if self._notifier is None:
            return None  # 无前端（无 TUI），立即返回
        request_id = uuid.uuid4().hex[:12]
        ev = threading.Event()
        with self._pending_lock:
            self._pending[request_id] = {"event": ev, "answer": None}
        try:
            self._notifier(request_id, payload)
        except Exception:
            pass
        answered = ev.wait(timeout)
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if not answered or entry is None:
            return None  # 超时或异常 → None
        return entry["answer"]

    def resolve(self, request_id: str, answer: str) -> bool:
        """用户已选择。返回是否找到对应请求。"""
        with self._pending_lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return False
            entry["answer"] = answer
            entry["event"].set()
            return True


# 全局单例
question_manager = QuestionManager()
