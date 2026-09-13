"""Memory 包配置访问层 — 解耦全局 configurationLoader 单例。

取值优先级：显式 overrides > 全局 configurationLoader.config > default。
独立使用 Memory 包时（宿主项目没有 configurationLoader / config.yaml），
所有取值走 overrides 或 default，不会因 import 失败而崩溃。
"""

import logging

logger = logging.getLogger(__name__)

try:
    from configurationLoader import config as _global_config
except Exception:
    _global_config = None


class MemoryConfig:
    """扁平点号键配置读取器，与 configurationLoader.Configuration.get 语义一致。"""

    def __init__(self, overrides=None):
        self._overrides = dict(overrides or {})

    def get(self, key_path, default=None):
        if key_path in self._overrides:
            return self._overrides[key_path]
        if _global_config is not None:
            try:
                return _global_config.get(key_path, default)
            except Exception:
                pass
        return default


def resolve_config(cfg=None):
    """把调用方传入的 cfg 归一化为 MemoryConfig。

    接受：None（回退全局配置）、MemoryConfig、扁平点号键 dict。
    """
    if cfg is None:
        return MemoryConfig()
    if isinstance(cfg, MemoryConfig):
        return cfg
    if isinstance(cfg, dict):
        return MemoryConfig(cfg)
    raise TypeError(f"cfg must be None, MemoryConfig or dict, got {type(cfg)}")
