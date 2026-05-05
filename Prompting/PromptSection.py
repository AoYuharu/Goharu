from dataclasses import dataclass, field
from typing import Any


SECTION_KINDS = frozenset({
    "system",
    "user",
    "assistant",
    "tool_call",
    "tool_result",
})


@dataclass(frozen=True)
class PromptSection:
    kind: str
    content: Any
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    cache_control: dict[str, str] | None = None  # 用于 Anthropic Prompt Caching

    def __post_init__(self):
        normalized_kind = str(self.kind).strip()
        if normalized_kind not in SECTION_KINDS:
            raise ValueError(f"Unsupported prompt section kind: {self.kind}")
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if self.cache_control is not None:
            object.__setattr__(self, "cache_control", dict(self.cache_control))
