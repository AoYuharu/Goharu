"""文本清洗工具（主循环与渲染器共用的唯一实现）。"""

import re

# 模型会模仿 prompt 里的消息 ID 标签。历史实况出现过：
#   [ID:msg_ab12cd34]   规范格式（PromptRenderer 注入）
#   [ID=msg_ab12cd34]   模型改写（用等号）
#   [ID：msg_ab12cd34]  全角冒号
# 且模型会层层叠加（[ID=x]\n\n[ID=y]...），所以一次性剥离开头全部前缀。
ID_PREFIX_PATTERN = re.compile(
    r"^(?:\[\s*ID\s*[:=：]?\s*msg_[0-9a-fA-F]+\s*\]\s*)+",
    re.IGNORECASE,
)


def strip_id_prefixes(text: str) -> str:
    """移除文本开头的消息 ID 模仿前缀（含叠加形式）。"""
    if not text:
        return text
    return ID_PREFIX_PATTERN.sub("", text)
