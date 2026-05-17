"""
会话管理模块 - Session 映射与存储

参考 hermes-agent 的 Session 管理机制
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, List

_logger = logging.getLogger(__name__)

from .platforms.base import SessionSource, Platform


@dataclass
class SessionEntry:
    """会话条目"""
    session_key: str           # 唯一会话键
    session_id: str            # UUID
    source: SessionSource      # 消息来源
    created_at: datetime       # 创建时间
    updated_at: datetime       # 最后更新时间
    was_auto_reset: bool = False  # 是否自动重置
    resume_pending: bool = False  # 是否需要恢复
    resume_reason: Optional[str] = None  # 恢复原因


@dataclass
class SessionContext:
    """会话上下文 - 注入到 Agent 的信息"""
    source: SessionSource              # 消息来源
    session_key: str                   # 会话键
    session_id: str                    # 会话ID
    created_at: datetime               # 创建时间
    updated_at: datetime               # 更新时间
    connected_platforms: List[str]     # 已连接的平台
    shared_multi_user_session: bool    # 是否多用户共享会话


def build_session_key(
    source: SessionSource,
    group_sessions_per_user: bool = True,
    thread_sessions_per_user: bool = False,
) -> str:
    """
    生成唯一的 session_key

    规则：
    - DM: agent:main:{platform}:dm:{chat_id}
    - Group (按用户隔离): agent:main:{platform}:group:{chat_id}:{user_id}
    - Group (共享): agent:main:{platform}:group:{chat_id}

    Args:
        source: 消息来源
        group_sessions_per_user: 群聊中是否按用户隔离
        thread_sessions_per_user: 线程中是否按用户隔离

    Returns:
        str: session_key
    """
    platform = source.platform.value

    # DM 规则: 基于 chat_id
    if source.chat_type == "dm":
        if source.chat_id:
            if source.thread_id:
                return f"agent:main:{platform}:dm:{source.chat_id}:{source.thread_id}"
            return f"agent:main:{platform}:dm:{source.chat_id}"
        return f"agent:main:{platform}:dm"

    # Group/Channel 规则: 基于 chat_id + user_id (可选)
    key_parts = ["agent:main", platform, source.chat_type]

    if source.chat_id:
        key_parts.append(source.chat_id)
    if source.thread_id:
        key_parts.append(source.thread_id)

    # 用户隔离逻辑
    isolate_user = group_sessions_per_user
    if source.thread_id and not thread_sessions_per_user:
        isolate_user = False  # 线程内默认共享

    if isolate_user and source.user_id:
        key_parts.append(source.user_id)

    return ":".join(key_parts)


def is_shared_session(
    source: SessionSource,
    group_sessions_per_user: bool = True,
    thread_sessions_per_user: bool = False,
) -> bool:
    """
    判断是否为多用户共享会话

    Args:
        source: 消息来源
        group_sessions_per_user: 群聊中是否按用户隔离
        thread_sessions_per_user: 线程中是否按用户隔离

    Returns:
        bool: 是否共享
    """
    if source.chat_type == "dm":
        return False  # 私聊永远不共享

    if source.thread_id:
        return not thread_sessions_per_user  # 线程默认共享

    return not group_sessions_per_user  # 群聊可配置


def build_session_context_prompt(context: SessionContext) -> str:
    """
    生成注入到 Agent 的系统提示

    Args:
        context: 会话上下文

    Returns:
        str: 系统提示文本
    """
    source = context.source

    # 基础信息
    prompt = f"You are currently in a {source.chat_type} conversation"

    user_context = ""
    if source.chat_type == "dm":
        user_context = f" with {source.user_name or 'a user'}"
    elif source.chat_type == "group":
        prompt += f" in group '{source.chat_name or 'unknown'}'"
        if not context.shared_multi_user_session:
            user_context = f" (private session with {source.user_name})"

    # 平台信息
    platform_info = f" on {source.platform.value}"

    # 可用平台
    connected_platforms = ""
    if context.connected_platforms:
        connected_platforms = f"\n\nConnected platforms: {', '.join(context.connected_platforms)}"

    return prompt + user_context + platform_info + connected_platforms


class SessionStore:
    """
    会话存储管理器

    职责：
    - 管理会话生命周期
    - 持久化会话状态
    - 处理会话重置策略
    """

    def __init__(
        self,
        storage_path: str = "./runtime_memory/gateway/sessions.json",
        group_sessions_per_user: bool = True,
        thread_sessions_per_user: bool = False,
        reset_mode: str = "idle",  # daily | idle | both | none
        reset_at_hour: int = 4,
        reset_idle_minutes: int = 1440,  # 24小时
    ):
        """
        初始化会话存储

        Args:
            storage_path: 存储文件路径
            group_sessions_per_user: 群聊中是否按用户隔离
            thread_sessions_per_user: 线程中是否按用户隔离
            reset_mode: 重置模式
            reset_at_hour: 每日重置时间（小时）
            reset_idle_minutes: 空闲超时（分钟）
        """
        self.storage_path = Path(storage_path)
        self.group_sessions_per_user = group_sessions_per_user
        self.thread_sessions_per_user = thread_sessions_per_user
        self.reset_mode = reset_mode
        self.reset_at_hour = reset_at_hour
        self.reset_idle_minutes = reset_idle_minutes

        self._entries: Dict[str, SessionEntry] = {}
        self._lock = Lock()
        self._loaded = False

        # 确保存储目录存在
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _generate_session_key(self, source: SessionSource) -> str:
        """生成 session_key"""
        return build_session_key(
            source,
            self.group_sessions_per_user,
            self.thread_sessions_per_user,
        )

    def _ensure_loaded_locked(self) -> None:
        """确保已加载（需要持有锁）"""
        if not self._loaded:
            self._load_locked()
            self._loaded = True

    def _load_locked(self) -> None:
        """从文件加载（需要持有锁）"""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, entry_data in data.items():
                # 反序列化 SessionSource
                source_data = entry_data["source"]
                source = SessionSource(
                    platform=Platform(source_data["platform"]),
                    chat_id=source_data["chat_id"],
                    chat_type=source_data["chat_type"],
                    user_id=source_data.get("user_id"),
                    user_name=source_data.get("user_name"),
                    chat_name=source_data.get("chat_name"),
                    thread_id=source_data.get("thread_id"),
                    guild_id=source_data.get("guild_id"),
                    message_id=source_data.get("message_id"),
                )

                # 反序列化时间
                created_at = datetime.fromisoformat(entry_data["created_at"])
                updated_at = datetime.fromisoformat(entry_data["updated_at"])

                entry = SessionEntry(
                    session_key=entry_data["session_key"],
                    session_id=entry_data["session_id"],
                    source=source,
                    created_at=created_at,
                    updated_at=updated_at,
                    was_auto_reset=entry_data.get("was_auto_reset", False),
                    resume_pending=entry_data.get("resume_pending", False),
                    resume_reason=entry_data.get("resume_reason"),
                )

                self._entries[key] = entry

        except Exception as e:
            _logger.error("Error loading sessions: %s", e)

    def _save(self) -> None:
        """保存到文件（需要持有锁）"""
        try:
            data = {}
            for key, entry in self._entries.items():
                # 序列化 SessionSource
                source_data = {
                    "platform": entry.source.platform.value,
                    "chat_id": entry.source.chat_id,
                    "chat_type": entry.source.chat_type,
                    "user_id": entry.source.user_id,
                    "user_name": entry.source.user_name,
                    "chat_name": entry.source.chat_name,
                    "thread_id": entry.source.thread_id,
                    "guild_id": entry.source.guild_id,
                    "message_id": entry.source.message_id,
                }

                entry_data = {
                    "session_key": entry.session_key,
                    "session_id": entry.session_id,
                    "source": source_data,
                    "created_at": entry.created_at.isoformat(),
                    "updated_at": entry.updated_at.isoformat(),
                    "was_auto_reset": entry.was_auto_reset,
                    "resume_pending": entry.resume_pending,
                    "resume_reason": entry.resume_reason,
                }

                data[key] = entry_data

            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            _logger.error("Error saving sessions: %s", e)

    def _is_session_expired(self, entry: SessionEntry) -> bool:
        """
        检查会话是否过期

        Args:
            entry: 会话条目

        Returns:
            bool: 是否过期
        """
        if self.reset_mode == "none":
            return False

        now = datetime.now()

        # 检查空闲超时
        if self.reset_mode in ("idle", "both"):
            idle_delta = timedelta(minutes=self.reset_idle_minutes)
            if now - entry.updated_at > idle_delta:
                return True

        # 检查每日重置
        if self.reset_mode in ("daily", "both"):
            # 如果当前时间已过重置时间，且上次更新在重置时间之前
            reset_time_today = now.replace(
                hour=self.reset_at_hour, minute=0, second=0, microsecond=0
            )
            if now >= reset_time_today and entry.updated_at < reset_time_today:
                return True

        return False

    def _reset_session_locked(self, session_key: str) -> SessionEntry:
        """
        重置会话（需要持有锁）

        Args:
            session_key: 会话键

        Returns:
            SessionEntry: 新的会话条目
        """
        old_entry = self._entries.get(session_key)
        if not old_entry:
            raise ValueError(f"Session not found: {session_key}")

        # 创建新会话
        new_entry = SessionEntry(
            session_key=session_key,
            session_id=str(uuid.uuid4()),
            source=old_entry.source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self._entries[session_key] = new_entry
        return new_entry

    def get_or_create_session(self, source: SessionSource) -> SessionEntry:
        """
        获取或创建会话

        Args:
            source: 消息来源

        Returns:
            SessionEntry: 会话条目
        """
        session_key = self._generate_session_key(source)

        with self._lock:
            self._ensure_loaded_locked()

            # 检查是否需要自动重置
            if session_key in self._entries:
                entry = self._entries[session_key]
                if self._is_session_expired(entry):
                    # 自动重置过期会话
                    entry = self._reset_session_locked(session_key)
                    entry.was_auto_reset = True
                else:
                    entry.updated_at = datetime.now()
            else:
                # 创建新会话
                entry = SessionEntry(
                    session_key=session_key,
                    session_id=str(uuid.uuid4()),
                    source=source,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                self._entries[session_key] = entry

            self._save()
            return entry

    def touch_session(self, session_key: str) -> bool:
        """
        更新会话时间戳

        Args:
            session_key: 会话键

        Returns:
            bool: 是否成功
        """
        with self._lock:
            self._ensure_loaded_locked()
            if session_key in self._entries:
                self._entries[session_key].updated_at = datetime.now()
                self._save()
                return True
            return False

    def reset_session(self, session_key: str) -> Optional[SessionEntry]:
        """
        手动重置会话

        Args:
            session_key: 会话键

        Returns:
            Optional[SessionEntry]: 新的会话条目
        """
        with self._lock:
            self._ensure_loaded_locked()
            if session_key in self._entries:
                entry = self._reset_session_locked(session_key)
                self._save()
                return entry
            return None
