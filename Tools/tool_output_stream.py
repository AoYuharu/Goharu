"""工具实时输出广播器。

run_cmd 等长时间运行的工具在执行时逐行发布 stdout/stderr，
Gateway 订阅后转发为 tool.output 事件给 TUI，让用户实时看到
安装程序/编译/训练等可能挂起的命令的输出。

设计：
- 模块级单例 tool_output_bus
- subscribe(cb) 注册回调，cb(line, stream, source) 在任何线程被调用（线程安全）
- publish() 逐行发布，无缓冲
"""

import threading
from typing import Callable, List


class ToolOutputBus:
    def __init__(self):
        self._subscribers: List[Callable[[str, str, str], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[str, str, str], None]):
        """注册输出回调。callback(line, stream='stdout'|'stderr', source=命令文本)"""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, line: str, stream: str = "stdout", source: str = ""):
        """发布一行输出（线程安全，可被任意工具线程调用）"""
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(line, stream, source)
            except Exception:
                pass


# 全局单例
tool_output_bus = ToolOutputBus()
