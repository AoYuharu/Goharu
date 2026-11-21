"""Memory 包：惰性暴露独立入口，保持 import Memory 轻量。

外部 harness 推荐显式 `from Memory.bridge import create_memory`；
`from Memory import create_memory` 亦可（PEP 562 惰性加载）。
"""


def __getattr__(name):
    if name in ("MemoryBridge", "create_memory"):
        from Memory import bridge
        return getattr(bridge, name)
    raise AttributeError(f"module 'Memory' has no attribute {name!r}")


__all__ = ["MemoryBridge", "create_memory"]
