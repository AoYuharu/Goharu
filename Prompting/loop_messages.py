"""ReAct 循环消息组装的唯一实现（主 Agent 与子 Agent 共用）。

铁律：tool_use/tool_result 的配对与顺序规则只在本模块定义。
ActorAgent 与 SubAgent 一律调用这里的函数，禁止各自手写。

2026-10-17 事故：SubAgent 在自己的 user 消息里把 countdown/reminder
文本放在 tool_result 块之前，DeepSeek 的 Anthropic 兼容端点判定
"tool_use ids without tool_result blocks immediately after" 并返回 400，
全部 AgentDelegate 调用失败。该约束主循环早已踩过并修复（tool_result
块必须位于消息最前面），但子循环是复制粘贴的第二份实现，修复没有同步。
"""

import logging

logger = logging.getLogger(__name__)


def truncate_history(history, max_messages):
    """保留最近 N 条消息，防止上下文随会话无限膨胀（模块级纯函数，可独立复用）。

    截断必须保持 tool_use/tool_result 配对完整：若截断点落在
    assistant tool_use 与其 user tool_result 之间，首条消息会是
    孤立的 tool_result（配对的 tool_use 已被截掉），Anthropic API
    会直接报 400——因此持续丢弃首部的孤立 tool_result 消息。
    """
    if max_messages <= 0 or len(history) <= max_messages:
        return history
    trimmed = history[-max_messages:]
    dropped_pairs = 0
    while trimmed:
        first = trimmed[0]
        content = first.get("content") if isinstance(first, dict) else None
        has_tool_result = isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
        if not has_tool_result:
            break
        trimmed = trimmed[1:]
        dropped_pairs += 1
    logger.info(
        "Context truncation: %d → %d messages (dropped %d orphan tool_result)",
        len(history), len(trimmed), dropped_pairs,
    )
    return trimmed


def sanitize_messages_for_api(messages):
    """清理不符合 Anthropic API 规则的消息序列（模块级纯函数，可独立复用）。

    API 要求：assistant 的 tool_use 必须被后续 user 的 tool_result 配对；
    任何孤立的 tool_use 或 tool_result 都会触发 400/2013。
    这里只修复"配对完整性"，不修改消息语义。
    """
    if not messages:
        return messages

    n = len(messages)
    keep = [True] * n

    # pending_assistants: assistant 消息索引 -> 未匹配 tool_use_id 集合
    pending_assistants = {}

    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or []
        blocks = content if isinstance(content, list) else []

        if role == "assistant":
            tool_use_ids = {
                block.get("id")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
            }
            if tool_use_ids:
                pending_assistants[i] = tool_use_ids
        elif role == "user":
            result_ids = {
                block.get("tool_use_id")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
            }
            if not result_ids:
                continue

            # 优先匹配最近一个待处理的 assistant
            matched_idx = None
            for idx in sorted(pending_assistants.keys(), reverse=True):
                if result_ids.issubset(pending_assistants[idx]):
                    matched_idx = idx
                    break

            if matched_idx is not None:
                pending_assistants[matched_idx] -= result_ids
                if not pending_assistants[matched_idx]:
                    del pending_assistants[matched_idx]
            else:
                # 孤立的 tool_result：只移除 tool_result 块
                non_result_blocks = [
                    block for block in blocks
                    if not (isinstance(block, dict) and block.get("type") == "tool_result")
                ]
                if non_result_blocks:
                    messages[i]["content"] = non_result_blocks
                else:
                    keep[i] = False

    # 处理仍未完全匹配的 assistant：删除未匹配的 tool_use 块
    for idx, unmatched_ids in pending_assistants.items():
        content = messages[idx].get("content") or []
        blocks = content if isinstance(content, list) else []
        new_blocks = [
            block for block in blocks
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") in unmatched_ids
            )
        ]
        if new_blocks:
            messages[idx]["content"] = new_blocks
        else:
            keep[idx] = False

    cleaned = [msg for i, msg in enumerate(messages) if keep[i]]

    # 防御性处理：如果清理后最前面是尚未配对的 assistant tool_use，直接删除。
    # 必须先看下一条：若紧随的 user 消息带着这些 tool_use 的 tool_result，
    # 说明配对完整——删了 assistant 反而制造新的孤立 tool_result（400）。
    while cleaned:
        first = cleaned[0]
        if first.get("role") != "assistant":
            break
        content = first.get("content") or []
        blocks = content if isinstance(content, list) else []
        tool_use_ids = {
            b.get("id")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
        }
        if not tool_use_ids:
            break
        nxt_ids = set()
        if len(cleaned) > 1 and cleaned[1].get("role") == "user":
            nc = cleaned[1].get("content") or []
            if isinstance(nc, list):
                nxt_ids = {
                    b.get("tool_use_id")
                    for b in nc
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                    and b.get("tool_use_id")
                }
        if tool_use_ids <= nxt_ids:
            break   # 配对完整，不能删
        cleaned.pop(0)

    if len(cleaned) != len(messages):
        logger.info(
            "History sanitized for API: %d → %d messages",
            len(messages), len(cleaned),
        )
    return cleaned


def build_tool_result_message(tool_results, trailing_texts=()):
    """组装紧跟 assistant tool_use 的 user 消息。

    硬约束（DeepSeek/Anthropic 兼容端点）：tool_result 块必须位于消息
    最前面；任何提示文本（倒计时、reminder、错误说明）只能追加在
    tool_result 之后，否则端点会误判"tool_use 缺少 tool_result"并 400。

    Args:
        tool_results: ToolCall.create_anthropic_tool_result(...) 产出的块列表
        trailing_texts: 追加在最后的文本（自动跳过空串）
    """
    content = list(tool_results)
    for text in trailing_texts:
        if text and str(text).strip():
            content.append({"type": "text", "text": str(text)})
    return {"role": "user", "content": content}
