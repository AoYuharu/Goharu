"""
MicroCompactor — 轻量级上下文压缩

当对话历史中的消息超过指定时间阈值（默认1小时），对该时间段内的工具结果
内容进行折叠：将过期的 tool_result 内容替换为简短占位文本，而不是删除整条
消息或对应的 tool_use 块。与 ContextCompactor（LLM 摘要）互补：
MicroCompactor 不调用 LLM，纯规则匹配。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple


class MicroCompactor:
    """轻量级上下文压缩器 — 基于时间和内容长度的工具结果内容折叠。

    设计原则：
      - 纯规则匹配，不调用 LLM
      - 非工具结果消息（用户提问、助理思考/回答）原样保留
      - 过期工具结果中保留最新的 N 条完整内容
      - 其余过期工具结果中，只有内容较长（超过字符阈值）的才会被折叠；
        内容很短的工具结果保持原样
      - 折叠时保留 tool_result / tool_use 块的存在，仅替换内容
      - 与 ContextCompactor 互补：先 micro_compact 减少体积，再决定是否需要 LLM 摘要
    """

    # ── 内置配置默认值 ─────────────────────────────────
    DEFAULT_AGE_HOURS = 1
    DEFAULT_KEEP_RESULTS = 5
    DEFAULT_MIN_CONTENT_CHARS = 120

    # ── 检测逻辑 ───────────────────────────────────────

    @staticmethod
    def _is_tool_result(msg: Dict[str, Any]) -> bool:
        """判断一条消息是否为工具执行结果。

        支持两种格式：
          1. role == "user" + content 列表中有 type=="tool_result" 的项
          2. role == "user" + content 字符串以 "[task-background" 开头
        """
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "user":
            if isinstance(content, list):
                return any(
                    isinstance(item, dict) and item.get("type") == "tool_result"
                    for item in content
                )
            if isinstance(content, str):
                return content.startswith("[task-background")

        return False

    @staticmethod
    def _estimate_tool_result_chars(msg: Dict[str, Any]) -> int:
        """估算一条工具结果消息的内容字符数。"""
        content = msg.get("content", "")
        total = 0
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    total += len(str(block.get("content", "")))
        elif isinstance(content, str):
            total = len(content)
        return total

    @classmethod
    def _compact_tool_result_message(cls, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """把一条工具结果消息里的内容折叠成简短占位文本。

        保留原始消息结构和 tool_result / tool_use 配对关系，仅替换内容字段。
        如果消息里没有可识别的工具结果块，则返回原消息。
        """
        role = msg.get("role", "")
        content = msg.get("content")

        if role != "user":
            return msg

        if isinstance(content, list):
            new_blocks: List[Any] = []
            changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "?")
                    new_block = dict(block)
                    new_block["content"] = (
                        f"该工具调用结果已过期，内容被省略。 (tool_use_id: {tool_use_id})"
                    )
                    new_blocks.append(new_block)
                    changed = True
                else:
                    new_blocks.append(block)

            if not changed:
                return msg

            new_msg = dict(msg)
            new_msg["content"] = new_blocks
            return new_msg

        if isinstance(content, str) and content.startswith("[task-background"):
            new_msg = dict(msg)
            new_msg["content"] = (
                "[micro-compact] 该后台任务结果已过期，内容被省略。"
            )
            return new_msg

        return msg

    # ── 公共接口 ───────────────────────────────────────

    @classmethod
    def compact(
        cls,
        messages: List[Dict[str, Any]],
        age_threshold_hours: Optional[float] = None,
        keep_tool_results: Optional[int] = None,
        min_content_chars: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """对消息列表执行微压缩。

        Args:
            messages: 消息列表，每条消息需有 timestamp 字段（ISO 格式）
            age_threshold_hours: 超过多少小时视为"过期"，默认 1
            keep_tool_results: 过期部分保留多少条最新工具结果完整内容，默认 5
            min_content_chars: 超过多少字符的过期工具结果才会被折叠，默认 120

        Returns:
            (压缩后的消息列表, 被折叠的工具结果数量)。
            如果无需压缩则返回原列表和 0。
        """
        if not messages:
            return messages, 0

        age_hours = (
            age_threshold_hours
            if age_threshold_hours is not None
            else cls.DEFAULT_AGE_HOURS
        )
        keep_n = (
            keep_tool_results
            if keep_tool_results is not None
            else cls.DEFAULT_KEEP_RESULTS
        )
        min_chars = (
            min_content_chars
            if min_content_chars is not None
            else cls.DEFAULT_MIN_CONTENT_CHARS
        )
        if keep_n <= 0:
            return messages, 0

        cutoff = (datetime.now() - timedelta(hours=age_hours)).isoformat()

        # 收集过期消息中的工具结果索引
        expired_tool_indices: List[int] = []
        for idx, msg in enumerate(messages):
            ts = str(msg.get("timestamp", ""))
            if ts < cutoff and cls._is_tool_result(msg):
                expired_tool_indices.append(idx)

        # 没有过期工具结果，或数量不足保留阈值
        if len(expired_tool_indices) <= keep_n:
            return messages, 0

        # 只压缩最旧的那些中内容较长的；保留最新的 keep_n 条完整内容
        compact_candidates: set[int] = set()
        for idx in expired_tool_indices[:-keep_n]:
            if cls._estimate_tool_result_chars(messages[idx]) > min_chars:
                compact_candidates.add(idx)

        if not compact_candidates:
            return messages, 0

        result: List[Dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if idx in compact_candidates:
                compacted = cls._compact_tool_result_message(msg)
                if compacted:
                    result.append(compacted)
            else:
                result.append(msg)

        return result, len(compact_candidates)

    # ── Token 估算 ─────────────────────────────────────

    @staticmethod
    def estimate_tokens(messages: List[Dict]) -> int:
        """粗略估算消息列表的 token 数（用于决定是否需要压缩）。"""
        from Agent.TokenEstimator import TokenEstimator
        estimator = TokenEstimator()
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += estimator.estimate(
                            str(block.get("text", ""))
                            or str(block.get("content", ""))
                        )
            else:
                total += estimator.estimate(str(content))
        return total
