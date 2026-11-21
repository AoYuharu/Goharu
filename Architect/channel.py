"""TelemetryChannel —— 遥测采集的 channel 插件适配器。

以 `channels.plugins: ["Architect.channel"]` + session 挂 `architect_telemetry`
channel 的方式接入，Gateway 本体零改动：事件经 broadcast 的 "event" 能力
轮发送达，喂给进程级 ArchitectHub 单例的 digest。

本文件是 Architect 模块唯一 import Gateway 的地方（插件身份决定的合法方向）。
"""

import logging

from Gateway.channels.base import Channel
from Gateway.channels.registry import register_channel

from Architect.hub import get_default_hub

logger = logging.getLogger(__name__)


@register_channel("architect_telemetry")
class TelemetryChannel(Channel):
    """哑 channel：只收事件喂遥测，不对外发任何消息。"""

    channel_type = "architect_telemetry"
    capabilities = frozenset({"event"})

    def send_answer(self, text, meta=None):
        pass   # 必要接口的 no-op：遥测 channel 不与用户对话

    def send_event(self, event_type, payload):
        get_default_hub().ingest_event(event_type, payload)
