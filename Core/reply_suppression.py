"""群聊回复屏蔽词判定。

模型对明显无关的群消息可以直接输出固定屏蔽词（如“不回复”），
Gateway 在出口处拦截，不转发到群 channel；TUI 仍可看到该决定。
"""

import re

DEFAULT_GROUP_SUPPRESS_PHRASES = (
    "不回复",
    "无需回复",
    "不回应",
    "不参与",
)

_MARKDOWN_RE = re.compile(r"[*_`~#>]+")
_PUNCT_RE = re.compile(r"[。.!！,，:：;；、]+$")


def normalize_answer(text: str) -> str:
    text = str(text or "")
    text = _MARKDOWN_RE.sub("", text)
    text = re.sub(r"\s+", "", text)
    return _PUNCT_RE.sub("", text).strip()


def is_group_suppress_text(text: str, phrases=None) -> bool:
    """整条回答是屏蔽词才算；带解释/正文时不拦截，避免误杀。"""
    normalized = normalize_answer(text)
    if not normalized:
        return False
    allowed = tuple(phrases) if phrases else DEFAULT_GROUP_SUPPRESS_PHRASES
    return normalized in {normalize_answer(p) for p in allowed}
