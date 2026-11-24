"""Gateway/channels —— channel 插件包。

内置 channel 在此注册（import 本包即完成）；外部插件经
config.yaml `channels.plugins` 声明模块路径，由 SessionManager 启动时加载。
"""

from Gateway.channels.base import Channel, ChannelMessage
from Gateway.channels.registry import (
    create_channel,
    load_plugins,
    register_channel,
    registered_types,
)
from Gateway.channels.tui_channel import TuiChannel  # noqa: F401  （注册 "tui"）
from Gateway.channels.qqbot_channel import QQBotChannel  # noqa: F401  （注册 "qqbot"）
from Gateway.channels.harunet_channel import HaruNetChannel  # noqa: F401  （注册 "harunet"）
# 兼容旧配置里的 type: agentnet（同一实现，别名注册）
register_channel("agentnet")(HaruNetChannel)
__all__ = [
    "Channel",
    "ChannelMessage",
    "create_channel",
    "load_plugins",
    "register_channel",
    "registered_types",
    "TuiChannel",
    "QQBotChannel",
    "HaruNetChannel",
]
