"""
轻量计时工具 — ReAct 循环全盘时间测量。

为 Agent Runtime 的每个关键阶段打时间点，把耗时写进 logs/timing/timing.log
（同时 propagate 到 stderr，方便 TUI 父进程实时看到）。超过 SLOW_MS 的阶段
用 WARNING 级别输出，其余用 INFO，便于 grep 定位慢点/卡点。

用法：
    from Core.timing import timeit

    # 1) 上下文管理器（同步 / 异步都可用）
    with timeit("gateway.process_message.notify_activity"):
        ...

    # 2) 装饰器（同步/异步函数都支持）
    @timeit("memory.retrieve")
    def f(): ...

    # 3) 手动打点（跨作用域）
    t = timeit.start("llm.query")
    try:
        ...
    finally:
        t.done()
"""
import functools
import inspect
import logging
import time as _time

from Core.LogManager import get_logger

logger = get_logger("Core.timing")

# 超过该毫秒数的阶段升级为 WARNING，便于一眼看到慢点
SLOW_MS = float(500)


class timeit:
    """计时器：上下文管理器 + 装饰器 + 手动打点。"""

    def __init__(self, name):
        self.name = name
        self._start = 0.0
        self._done = False

    # ── 上下文管理器 ──
    def __enter__(self):
        self._start = _time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish()
        return False

    # ── 装饰器（同步 / 异步自动识别）──
    def __call__(self, fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                with timeit(self.name or fn.__qualname__):
                    return await fn(*args, **kwargs)
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with timeit(self.name or fn.__qualname__):
                return fn(*args, **kwargs)
        return wrapper

    # ── 手动打点 ──
    @classmethod
    def start(cls, name):
        t = cls(name)
        t._start = _time.perf_counter()
        return t

    def done(self):
        if not self._done:
            self._finish()

    def _finish(self):
        self._done = True
        elapsed_ms = (_time.perf_counter() - self._start) * 1000.0
        level = logging.WARNING if elapsed_ms >= SLOW_MS else logging.INFO
        logger.log(level, "[timing] %-44s %9.1f ms", self.name, elapsed_ms)
