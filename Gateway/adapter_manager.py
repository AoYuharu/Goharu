"""
适配器管理器 - 负责平台适配器的生命周期管理

职责：
- 创建平台适配器
- 启动和停止适配器
- 管理适配器连接状态
"""

from typing import Dict, Optional, Callable, Awaitable

from .platforms.base import BasePlatformAdapter, Platform, MessageEvent
from .platforms.qq_adapter import QQAdapter


# 消息处理器类型
MessageHandler = Callable[[MessageEvent], Awaitable[Optional[str]]]


class AdapterManager:
    """适配器管理器"""

    def __init__(self, platforms_config: Dict):
        """
        初始化管理器

        Args:
            platforms_config: 平台配置字典
        """
        self.platforms_config = platforms_config
        self.adapters: Dict[Platform, BasePlatformAdapter] = {}

    async def start_all(self, message_handler: MessageHandler) -> None:
        """
        启动所有已启用的平台适配器

        Args:
            message_handler: 消息处理器（由 Router 提供）
        """
        for platform_name, platform_config in self.platforms_config.items():
            if not platform_config.get("enabled", False):
                continue

            try:
                adapter = self._create_adapter(platform_name, platform_config)
                if adapter:
                    # 注入消息处理器
                    adapter.set_message_handler(message_handler)

                    # 连接平台
                    success = await adapter.connect()
                    if success:
                        self.adapters[adapter.platform] = adapter
                        print(f"[AdapterManager] Platform {platform_name} connected")
                    else:
                        print(f"[AdapterManager] Platform {platform_name} connection failed")
            except Exception as e:
                print(f"[AdapterManager] Error starting platform {platform_name}: {e}")

    async def stop_all(self) -> None:
        """停止所有适配器"""
        for platform, adapter in self.adapters.items():
            try:
                await adapter.disconnect()
                print(f"[AdapterManager] Platform {platform.value} disconnected")
            except Exception as e:
                print(f"[AdapterManager] Error disconnecting {platform.value}: {e}")

        self.adapters.clear()

    def get_connected_platforms(self) -> list:
        """
        获取已连接的平台列表

        Returns:
            list: 平台名称列表
        """
        return [p.value for p in self.adapters.keys()]

    def _create_adapter(
        self,
        platform_name: str,
        platform_config: Dict
    ) -> Optional[BasePlatformAdapter]:
        """
        创建平台适配器

        Args:
            platform_name: 平台名称
            platform_config: 平台配置

        Returns:
            Optional[BasePlatformAdapter]: 适配器实例
        """
        platform_name_lower = platform_name.lower()

        if platform_name_lower == "qq" or platform_name_lower == "qqbot":
            return QQAdapter(platform_config)
        # 可以在这里添加更多平台
        # elif platform_name_lower == "telegram":
        #     return TelegramAdapter(platform_config)
        else:
            print(f"[AdapterManager] Unknown platform: {platform_name}")
            return None
