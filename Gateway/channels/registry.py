"""Channel 注册表 —— type 名 → Channel 类的插件注册与实例化。

内置 channel（tui/http）在 Gateway/channels/__init__.py 里注册；
外部插件在 config.yaml 的 `channels.plugins` 里声明模块路径，
启动时 import 即触发 @register_channel 完成注册。
"""

import importlib
import logging

logger = logging.getLogger(__name__)

_CHANNEL_TYPES: dict = {}


def register_channel(type_name: str):
    """类装饰器：把 Channel 子类注册为某种 type。"""
    def deco(cls):
        if type_name in _CHANNEL_TYPES:
            logger.warning(
                "channel type '%s' re-registered: %s overrides %s",
                type_name, cls.__name__, _CHANNEL_TYPES[type_name].__name__,
            )
        _CHANNEL_TYPES[type_name] = cls
        return cls
    return deco


def registered_types() -> list:
    return sorted(_CHANNEL_TYPES)


def create_channel(type_name: str, channel_id: str, params: dict | None = None):
    """按 type 实例化 channel；未注册的 type 给说明性错误。"""
    cls = _CHANNEL_TYPES.get(type_name)
    if cls is None:
        raise ValueError(
            f"未知 channel type: '{type_name}'（已注册: {registered_types()}）。"
            f"内置 type 由 Gateway.channels 自动注册；外部插件请在 config.yaml 的 "
            f"channels.plugins 声明模块路径。"
        )
    return cls(channel_id, params)


def load_plugins(module_paths):
    """import 外部插件模块，触发其 @register_channel 完成注册。"""
    for path in module_paths or []:
        importlib.import_module(str(path))
        logger.info("channel plugin loaded: %s", path)
