"""Tools 子系统的配置代理。

解耦目的：Tools/ 内部不再硬依赖 Goharu 的全局 `configurationLoader.config` 单例。
外部 harness 用 `Tools.api.create_tools(config={...})` 或 `set_config({...})` 注入
任意带 `.get(key, default)` 方法的对象（dict 即可）；未注入时回退到 Goharu 全局
配置（在 Goharu 仓库内行为不变），再不行返回空 dict（全部走默认值）。

注意：配置按调用时读取为主，但 Tools/security.py 的安全检查器在首次构造时缓存
配置——外部 harness 应在首次分发工具前完成注入。
"""

import threading

_lock = threading.Lock()
_injected = None


class ConfigView:
    """dict 配置视图：同时支持扁平点号键和嵌套 dict。

    Goharu 全局 configurationLoader 用扁平点号键（"tools.security" 是一个完整的
    键）；外部 harness 更常写嵌套 dict（{"tools": {"security": ...}}）。
    ConfigView 先查扁平键，未命中再按点号逐层下钻，两种写法都生效。
    """

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        node = self._data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def set_config(cfg) -> None:
    """注入配置对象：dict（自动包 ConfigView，扁平/嵌套键皆可）或任何
    带 .get(key, default) 方法的对象（原样使用）。"""
    global _injected
    with _lock:
        _injected = ConfigView(cfg) if isinstance(cfg, dict) else cfg


def reset_config() -> None:
    """清除注入，恢复回退逻辑（测试用）。"""
    global _injected
    with _lock:
        _injected = None


def get_config():
    """取当前配置：注入优先，其次 Goharu 全局 configurationLoader，最后空视图。"""
    with _lock:
        if _injected is not None:
            return _injected
    try:
        from configurationLoader import config
        return config
    except ImportError:
        return ConfigView({})
