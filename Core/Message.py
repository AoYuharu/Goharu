"""
Unified message type for distinguishing user-visible messages from LLM-context-only messages.

Internal control messages (background tasks, micro-compact, reactivation, interrupts)
should be marked CONTEXT_ONLY so they never leak into the chat UI during history replay.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class MessageSource(Enum):
    """Who or what produced the message."""
    USER_INPUT = "user_input"
    AGENT_ANSWER = "agent_answer"
    TOOL_RESULT = "tool_result"
    BG_TASK = "bg_task"
    MICRO_COMPACT = "micro_compact"
    REACTIVATION = "reactivation"
    INTERRUPT = "interrupt"


class MessageVisibility(Enum):
    """How the message should be displayed."""
    FULL = "full"              # Visible in both LLM context and user chat UI
    CONTEXT_ONLY = "context_only"  # LLM context only, hidden from history replay


@dataclass
class CoreMessage:
    """A typed message that carries visibility metadata.

    Usage:
        # User-visible message (default):
        msg = CoreMessage("user", "Hello world").to_dict()

        # LLM-context-only internal message:
        msg = CoreMessage.context_only(
            MessageSource.BG_TASK,
            "[System: task-background] Tool moved to background"
        ).to_dict()

        # Check visibility on a serialized dict:
        if CoreMessage.is_context_only(some_dict):
            skip_display()
    """
    role: str
    content: str
    visibility: MessageVisibility = MessageVisibility.FULL
    source: Optional[MessageSource] = None
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now().replace(microsecond=0).isoformat())

    @classmethod
    def context_only(cls, source: MessageSource, content: str) -> "CoreMessage":
        """Factory for LLM-context-only messages (hidden from UI replay)."""
        return cls(
            role="user",
            content=content,
            visibility=MessageVisibility.CONTEXT_ONLY,
            source=source,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for WorkingMemory.append()."""
        d: Dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "id": self.id,
            "timestamp": self.timestamp,
            "_visibility": self.visibility.value,
        }
        if self.source is not None:
            d["_source"] = self.source.value
        return d

    @staticmethod
    def is_context_only(msg: Dict[str, Any]) -> bool:
        """Check if a serialized message dict should be hidden from UI replay."""
        return msg.get("_visibility") == MessageVisibility.CONTEXT_ONLY.value
