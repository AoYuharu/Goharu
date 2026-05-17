"""
MicroCompactor — 轻量级上下文压缩

当对话历史中的消息超过指定时间阈值（默认1小时），对该时间段内的工具结果
进行选择性保留：只保留最新的 N 条（默认5条），其余合并为一条占位符消息。
与 ContextCompactor（LLM 摘要）互补：MicroCompactor 不调用 LLM，纯规则匹配。
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional


class MicroCompactor:
    """轻量级上下文压缩器 — 基于时间的工具结果选择性保留。

    设计原则：
      - 纯规则匹配，不调用 LLM
      - 非工具结果消息（用户提问、助理思考/回答）原样保留
      - 过期工具结果中保留最新的 N 条，其余用一个占位符替代
      - 与 ContextCompactor 互补：先 micro_compact 减少体积，再决定是否需要 LLM 摘要
    """

    # ── 内置配置默认值 ─────────────────────────────────
    DEFAULT_AGE_HOURS = 1
    DEFAULT_KEEP_RESULTS = 5
    RESULT_PREVIEW_LENGTH = 120

    # ── 公共接口 ───────────────────────────────────────

    @classmethod
    def compact(
        cls,
        messages: List[Dict[str, Any]],
        age_threshold_hours: Optional[float] = None,
        keep_tool_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """对消息列表执行微压缩。

        Args:
            messages: 消息列表，每条消息需有 timestamp 字段（ISO 格式）
            age_threshold_hours: 超过多少小时视为"过期"，默认 1
            keep_tool_results: 过期部分保留多少条最新工具结果，默认 5

        Returns:
            压缩后的消息列表。如果无需压缩则返回原列表。
        """
        if not messages:
            return messages

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
        if keep_n <= 0:
            return messages

        now = datetime.now()
        cutoff = (now - timedelta(hours=age_hours)).isoformat()

        # 按时间戳分割
        recent: List[Dict] = []
        expired: List[Dict] = []
        for msg in messages:
            ts = str(msg.get("timestamp", ""))
            if ts >= cutoff:
                recent.append(msg)
            else:
                expired.append(msg)

        # 没有过期消息或过期消息太少
        if not expired:
            return messages

        # 找到过期部分中的工具结果
        tool_result_indices = cls._find_tool_results(expired)

        if len(tool_result_indices) <= keep_n:
            return messages  # 无需压缩

        # 保留最新的 N 条，其余移除
        keep_set = set(tool_result_indices[-keep_n:])
        remove_set = set(tool_result_indices[:-keep_n])

        # 收集被移除的工具信息
        removed_infos = []
        for idx in tool_result_indices[:-keep_n]:
            info = cls._extract_tool_info(expired[idx])
            if info:
                removed_infos.append(info)

        # 构建压缩后的过期部分
        compacted_expired: List[Dict] = []
        for idx, msg in enumerate(expired):
            if idx in remove_set:
                continue
            compacted_expired.append(msg)

        # 插入占位符
        if removed_infos:
            placeholder = cls._build_placeholder(removed_infos, keep_n)
            # 将占位符放在过期段开头（时间线上最早的位置附近）
            compacted_expired.insert(0, placeholder)

        return compacted_expired + recent

    # ── 检测逻辑 ───────────────────────────────────────

    @staticmethod
    def _is_tool_result(msg: Dict[str, Any]) -> bool:
        """判断一条消息是否为工具执行结果。

        支持两种格式：
          1. role == "user" + content 列表中有  — Anthropic 原生 tool_result 块
             type=="tool_result" 的项
          2. role == "user" + content 字符串以   — 后台任务完成通知
             "[task-background" 开头
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

    @classmethod
    def _find_tool_results(cls, messages: List[Dict]) -> List[int]:
        """返回过期消息中所有工具结果的索引列表（已按位置排序）。"""
        indices = []
        for idx, msg in enumerate(messages):
            if cls._is_tool_result(msg):
                indices.append(idx)
        return indices

    # ── 信息提取 ───────────────────────────────────────

    @classmethod
    def _extract_tool_info(cls, msg: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """从一条工具结果消息中提取摘要信息。

        Returns:
            {"name": 工具名, "preview": 结果预览字符串} 或 None
        """
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "user":
            if isinstance(content, list):
                # 取第一个 tool_result 块的名称（从 tool_use_id 推测）和内容
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_id = item.get("tool_use_id", "?")
                        result_content = item.get("content", "")
                        preview = cls._truncate(str(result_content))
                        return {"name": f"tool_use({tool_id})", "preview": preview}
                return None

            if isinstance(content, str):
                if content.startswith("[task-background"):
                    # 从文本中提取工具名
                    import re
                    match = re.search(r"Tool:\s*(\S+)", content)
                    name = match.group(1) if match else "background_task"
                    preview = cls._truncate(content)
                    return {"name": name, "preview": preview}

        return None

    @classmethod
    def _truncate(cls, text: str, max_len: int = None) -> str:
        limit = max_len if max_len is not None else cls.RESULT_PREVIEW_LENGTH
        compact = " ".join(str(text).split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."

    # ── 占位符构建 ─────────────────────────────────────

    @classmethod
    def _build_placeholder(
        cls, removed_infos: List[Dict[str, str]], kept_count: int
    ) -> Dict[str, Any]:
        """为被移除的工具结果构建一条占位符消息。

        占位符使用 role="user"，以 "@micro-compact" 前缀标注，
        确保 LLM 能将其作为系统级上下文更新来理解。
        """
        tool_names = list(dict.fromkeys(info["name"] for info in removed_infos))
        names_str = ", ".join(tool_names[:10])
        if len(tool_names) > 10:
            names_str += f", ... (+{len(tool_names) - 10} more)"

        previews = "\n".join(
            f"  - {info['name']}: {info['preview']}"
            for info in removed_infos[:10]
        )
        if len(removed_infos) > 10:
            previews += f"\n  ... (and {len(removed_infos) - 10} more results omitted)"

        content = (
            f"[micro-compact] {len(removed_infos)} older tool results have been "
            f"compacted. Only the latest {kept_count} results from the expired "
            f"period (>{cls.DEFAULT_AGE_HOURS}h old) are preserved.\n\n"
            f"Removed tools: {names_str}\n\n"
            f"Preview of removed results:\n{previews}\n\n"
            f"These are old results — do NOT re-execute these tools unless the "
            f"user explicitly requests it. Rely on preserved recent results instead."
        )

        from Core.Message import CoreMessage, MessageSource
        return CoreMessage.context_only(MessageSource.MICRO_COMPACT, content).to_dict()

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
