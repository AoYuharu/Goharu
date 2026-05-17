"""
Snip 工具 — 通过消息 ID 删除历史对话中的指定消息，用于上下文裁剪。

模型可以调用此工具传入一个或多个消息 ID，工具会从当日工作记忆中
移除对应消息，释放上下文空间。
"""
import json
import logging
from datetime import date

from Tools.registry import registry

logger = logging.getLogger(__name__)

# 模块级引用，由外部在 MemoryManager 创建后注入
_working_memory = None


def set_working_memory(wm):
    """由外部（gateway_entry / gateway_runner）在初始化后调用，注入 WorkingMemory 实例"""
    global _working_memory
    _working_memory = wm
    logger.info("Snip tool: WorkingMemory reference set")


def _is_tool_call(msg):
    """检测消息是否包含工具调用（需配对删除）"""
    if msg.get("message_type") == "tool_call":
        return True
    if msg.get("role") == "assistant":
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return True
    return False


def _is_tool_result(msg):
    """检测消息是否为工具执行结果（需配对删除）"""
    if msg.get("role") == "user":
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return True
    return False


def _ensure_tool_pairing(target_ids, messages):
    """
    维护 tool_use / tool_result 配对完整性。

    如果 target_ids 中包含 tool_call 但缺少对应的 tool_result（或反之），
    自动将配对消息也加入待删除集合。迭代直到稳定。
    """
    change = True
    while change:
        change = False
        for i, msg in enumerate(messages):
            mid = msg.get("id", "")
            if mid not in target_ids:
                continue

            # tool_call 被删 → 下一个 tool_result 也必须删
            if _is_tool_call(msg) and i + 1 < len(messages):
                next_id = messages[i + 1].get("id", "")
                if _is_tool_result(messages[i + 1]) and next_id and next_id not in target_ids:
                    target_ids.add(next_id)
                    change = True

            # tool_result 被删 → 上一个 tool_call 也必须删
            if _is_tool_result(msg) and i - 1 >= 0:
                prev_id = messages[i - 1].get("id", "")
                if _is_tool_call(messages[i - 1]) and prev_id and prev_id not in target_ids:
                    target_ids.add(prev_id)
                    change = True
    return target_ids


def _validate_conversation(messages):
    """
    验证剩余对话中 tool_use / tool_result 是否完整配对。
    返回 (is_valid, orphaned_indices) — orphaned_indices 是需要移除的孤立消息索引。
    """
    pending_tool_use_indices = []
    orphaned = set()

    for i, msg in enumerate(messages):
        if _is_tool_call(msg):
            pending_tool_use_indices.append(i)
        elif _is_tool_result(msg):
            if pending_tool_use_indices:
                pending_tool_use_indices.pop()
            else:
                # tool_result 没有前置 tool_use → 孤立的
                orphaned.add(i)

    # tool_use 没有后续 tool_result → 孤立的
    orphaned.update(pending_tool_use_indices)

    return len(orphaned) == 0, list(orphaned)


def snip_messages(ids):
    """
    删除指定 ID 的对话消息。自动维护 tool_use/tool_result 配对完整性。

    Args:
        ids: 消息 ID 列表，如 ["msg_abc123", "msg_def456"]

    Returns:
        JSON 字符串，包含删除结果摘要
    """
    if _working_memory is None:
        return json.dumps({
            "error": "Snip 工具未初始化，WorkingMemory 不可用",
        }, ensure_ascii=False)

    if not ids or not isinstance(ids, list):
        return json.dumps({
            "error": "ids 参数必须是包含至少一个 ID 的字符串列表",
        }, ensure_ascii=False)

    # 规范化 ID 列表
    target_ids = set()
    for item in ids:
        if isinstance(item, str) and item.strip():
            target_ids.add(item.strip())

    if not target_ids:
        return json.dumps({
            "error": "未提供有效的消息 ID",
        }, ensure_ascii=False)

    # 读取今日消息
    today = date.today().isoformat()
    day_payload = _working_memory.read_day(today)

    if not day_payload or not day_payload.get("messages"):
        return json.dumps({
            "removed": 0,
            "remaining": 0,
            "message": "今日没有对话消息",
        }, ensure_ascii=False)

    messages = day_payload["messages"]
    original_count = len(messages)

    # Step 1: 维护 tool_use/tool_result 配对完整性（正向配对）
    original_ids = set(target_ids)
    final_ids = _ensure_tool_pairing(target_ids, messages)

    # 过滤消息
    removed_summaries = []
    kept_messages = []
    for message in messages:
        msg_id = message.get("id", "")
        if msg_id in final_ids:
            role = message.get("role", "?")
            content_preview = str(message.get("content", ""))[:60].replace("\n", " ")
            removed_summaries.append(f"[{msg_id}] {role}: {content_preview}")
        else:
            kept_messages.append(message)

    # Step 2: 删除后验证 — 清理剩余对话中所有孤立的 tool_use/tool_result
    is_valid, orphaned_indices = _validate_conversation(kept_messages)
    extra_removed = 0
    if not is_valid:
        orphaned_ids = {kept_messages[idx].get("id", "") for idx in orphaned_indices}
        final_valid = []
        for message in kept_messages:
            if message.get("id", "") in orphaned_ids:
                role = message.get("role", "?")
                content_preview = str(message.get("content", ""))[:60].replace("\n", " ")
                removed_summaries.append(
                    f"[{message.get('id', '')}] (orphaned) {role}: {content_preview}"
                )
                extra_removed += 1
            else:
                final_valid.append(message)
        kept_messages = final_valid
        logger.warning(
            "Snip: cleaned %d orphaned tool_use/tool_result messages (IDs: %s)",
            extra_removed, list(orphaned_ids),
        )

    removed_count = original_count - len(kept_messages)
    auto_added = final_ids - original_ids

    day_payload["messages"] = kept_messages
    day_payload["message_count"] = len(kept_messages)
    day_payload["updated_at"] = _working_memory._now()
    _working_memory._write_day(day_payload)

    result = {
        "removed": removed_count,
        "remaining": len(kept_messages),
        "matched_ids": list(target_ids),
        "auto_paired_ids": list(auto_added) if auto_added else [],
        "orphaned_cleaned": extra_removed,
        "removed_summaries": removed_summaries,
        "message": (
            f"已删除 {removed_count} 条消息（含 {len(auto_added)} 条自动配对"
            + (f"，{extra_removed} 条孤立消息" if extra_removed else "")
            + f"），剩余 {len(kept_messages)} 条"
        ),
    }

    logger.info(
        "Snip: removed %d (requested: %s, paired: %s, orphaned: %d)",
        removed_count, list(target_ids), list(auto_added), extra_removed,
    )
    return json.dumps(result, ensure_ascii=False)


# 注册工具
registry.register(
    name="snip",
    description=(
        "删除对话历史中指定 ID 的消息，释放上下文空间。"
        "传入一个或多个消息 ID（从历史消息的 [ID:xxx] 前缀获取），"
        "工具会永久移除这些消息。"
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要删除的消息 ID 列表，每个 ID 格式为 msg_xxxxxxxx",
            },
        },
        "required": ["ids"],
    },
    handler=snip_messages,
    group="core",
)
