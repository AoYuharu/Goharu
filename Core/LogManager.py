"""
LogManager — 集中式日志管理。

按模块分文件夹输出日志：
  logs/agent/    — ActorAgent, SummarizerAgent, ReviewerAgent, MemoryOrchestrator
  logs/memory/   — MemoryManager, WorkingMemory, LongTermMemory
  logs/api/      — LLMCore, API 请求/响应
  logs/tools/    — ToolRuntime, ToolCall, 安全层
  logs/gateway/  — GatewaySession, JSON-RPC, 后台任务
  logs/system/   — 启动/关闭, 配置, 未分类

特性：
- 每个模块独立 RotatingFileHandler（默认 5MB × 3 个备份）
- 同时输出到 stderr（供 TUI 父进程捕获）
- crash 日志走独立 handler，写入 logs/gateway/crash.log
- 可通过 config.yaml 的 logging section 控制
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional


class LogManager:
    """集中管理所有 logger 的创建和配置。"""

    _instance: Optional["LogManager"] = None
    _initialized: bool = False

    # 模块 → 日志子目录 映射
    MODULE_ROUTING: Dict[str, str] = {
        # Agent
        "Agent.ActorAgent": "agent",
        "Agent.SummarizerAgent": "agent",
        "Agent.ReviewAgent": "agent",
        "Agent.MemoryOrchestrator": "agent",
        "Agent.BackgroundTaskManager": "agent",
        "Agent.LargeLanguageModel": "agent",
        "Agent.LLMCore": "api",  # LLMCore 主要做 API 调用
        "Agent.LLMResponseLogger": "api",
        "Agent.MicroCompactor": "agent",
        "Agent.ToolResultBudget": "agent",
        "Agent.TokenEstimator": "agent",
        # Memory
        "Memory.MemoryManager": "memory",
        "Memory.WorkingMemory": "memory",
        "Memory.LongTermMemory": "memory",
        "Memory.UserProfileMemory": "memory",
        "Memory.MemoryDB": "memory",
        "Memory.pipeline": "memory",
        "Memory.repositories": "memory",
        "Memory.retrieval": "memory",
        "Memory.projection": "memory",
        # Tools
        "Tools.runtime": "tools",
        "Tools.builtin": "tools",
        "Tools.guard": "tools",
        "Tools.security": "tools",
        "Tools.task_guide": "tools",
        "Tools.tool_process_tracker": "tools",
        "Tools.platform_utils": "tools",
        # Gateway / TUI
        "TUI.gateway_entry": "gateway",
        "TUI": "gateway",
        "Gateway": "gateway",
        # Prompting
        "Prompting": "agent",
        "Prompting.PromptAssembler": "agent",
        "Prompting.PromptRenderer": "agent",
        "Prompting.PromptLoader": "agent",
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._log_dir: Optional[Path] = None
        self._max_bytes: int = 5 * 1024 * 1024  # 5MB
        self._backup_count: int = 3
        self._level: int = logging.INFO
        self._crash_handler: Optional[logging.FileHandler] = None
        self._stderr_handler: Optional[logging.StreamHandler] = None
        self._configured: bool = False
        self._file_handlers: Dict[str, RotatingFileHandler] = {}
        self._initialized = True

    # ── 公开 API ────────────────────────────────────────

    def setup(
        self,
        base_dir: Optional[str] = None,
        level: Optional[int] = None,
        max_bytes: Optional[int] = None,
        backup_count: Optional[int] = None,
        stderr_output: bool = True,
    ):
        """一次性配置日志系统。必须在其他模块创建 logger 之前调用。

        Args:
            base_dir: 日志根目录，默认从 config.yaml 读取或使用 ./runtime_memory/logs
            level: 日志级别，默认 INFO
            max_bytes: 单个日志文件最大字节数，默认 5MB
            backup_count: 备份文件数，默认 3
            stderr_output: 是否同时输出到 stderr，默认 True
        """
        if self._configured:
            return

        # 从 config.yaml 读取配置
        try:
            from configurationLoader import config
            logging_cfg = config.get("logging", {}) or {}
            base_dir = base_dir or logging_cfg.get("base_dir", "./runtime_memory/logs")
            level = level or getattr(logging, logging_cfg.get("level", "INFO"), logging.INFO)
            max_bytes = max_bytes or int(logging_cfg.get("max_mb", 5)) * 1024 * 1024
            backup_count = backup_count or int(logging_cfg.get("backup_count", 3))
        except Exception:
            base_dir = base_dir or "./runtime_memory/logs"
            level = level or logging.INFO
            max_bytes = max_bytes or 5 * 1024 * 1024
            backup_count = backup_count or 3

        self._log_dir = Path(base_dir)
        self._level = level
        self._max_bytes = max_bytes
        self._backup_count = backup_count

        self._log_dir.mkdir(parents=True, exist_ok=True)

        # 创建子目录
        for subdir in ("agent", "memory", "api", "tools", "gateway", "system"):
            (self._log_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Root logger 配置
        root_logger = logging.getLogger()
        root_logger.setLevel(self._level)

        # 清除已有 handlers（防止 basicConfig 重复）
        root_logger.handlers.clear()

        # --- stderr handler ---
        if stderr_output:
            self._stderr_handler = logging.StreamHandler(sys.stderr)
            self._stderr_handler.setLevel(self._level)
            self._stderr_handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname).1s] %(name)s: %(message)s',
                datefmt='%H:%M:%S',
            ))
            root_logger.addHandler(self._stderr_handler)

        # --- crash log handler (独立) ---
        crash_dir = self._log_dir / "gateway"
        crash_dir.mkdir(parents=True, exist_ok=True)
        self._crash_handler = RotatingFileHandler(
            crash_dir / "crash.log",
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        self._crash_handler.setLevel(logging.ERROR)
        self._crash_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        root_logger.addHandler(self._crash_handler)

        self._configured = True

    def get_logger(self, name: str) -> logging.Logger:
        """获取模块专用 logger。

        每个 logger 有两条输出通道：
        1. propagate → root logger → stderr (简洁格式) + crash.log (ERROR)
        2. 直接挂 category file handler → logs/{category}/{category}.log

        同 category 的模块共享同一个 RotatingFileHandler 实例。
        """
        if not self._configured:
            self.setup()

        category = self._route_module(name)
        logger = logging.getLogger(name)
        logger.setLevel(self._level)

        # 确保 category file handler 存在（所有同 category logger 共享）
        if category not in self._file_handlers:
            file_path = self._log_dir / category / f"{category}.log"
            handler = RotatingFileHandler(
                file_path,
                maxBytes=self._max_bytes,
                backupCount=self._backup_count,
                encoding="utf-8",
            )
            handler.setLevel(self._level)
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            ))
            self._file_handlers[category] = handler

        # 将 category handler 挂到 logger 上（只挂一次）
        cat_handler = self._file_handlers[category]
        if cat_handler not in logger.handlers:
            logger.addHandler(cat_handler)

        return logger

    @classmethod
    def _route_module(cls, name: str) -> str:
        """根据模块名路由到日志子目录。"""
        # 精确匹配
        if name in cls.MODULE_ROUTING:
            return cls.MODULE_ROUTING[name]
        # 前缀匹配（最长匹配优先）
        best = "system"
        best_len = 0
        for prefix, category in cls.MODULE_ROUTING.items():
            if name.startswith(prefix) and len(prefix) > best_len:
                best = category
                best_len = len(prefix)
        return best


# 全局实例
_log_manager = LogManager()


def init_logging():
    """初始化日志系统（在程序入口处调用一次）。"""
    _log_manager.setup()


def get_logger(name: str) -> logging.Logger:
    """获取模块专用 logger。

    用法:
        from Core.LogManager import get_logger
        logger = get_logger(__name__)
    """
    return _log_manager.get_logger(name)
