"""
测试多级缓存实现
验证 User Profile、Memory 和历史对话的缓存策略
"""
import json
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


def test_multilevel_cache():
    """测试多级缓存策略"""
    assembler = PromptAssembler()
    renderer = PromptRenderer()

    # 模拟数据
    soul_markdown = "# SOUL\n你是一个智能助手。"
    user_profile_markdown = "# USER PROFILE\n用户偏好：简洁回答"
    memory_markdown = "# MEMORY\n- 用户上次询问了Python问题"

    tool_definitions = [
        {"name": "Read", "description": "读取文件"},
        {"name": "Write", "description": "写入文件"},
    ]

    # 模拟多轮对话历史
    history = [
        {"role": "user", "content": "第一轮：什么是Python？", "timestamp": "2024-01-01T10:00:00"},
        {"role": "assistant", "content": "Python是一种编程语言。", "timestamp": "2024-01-01T10:00:05"},
        {"role": "user", "content": "第二轮：如何安装Python？", "timestamp": "2024-01-01T10:01:00"},
        {"role": "assistant", "content": "可以从官网下载安装。", "timestamp": "2024-01-01T10:01:05"},
        {"role": "user", "content": "第三轮（最新）：Python有哪些特点？", "timestamp": "2024-01-01T10:02:00"},
    ]

    # 构建文档
    document = assembler.build_actor_document(
        history=history,
        soul_markdown=soul_markdown,
        user_profile_markdown=user_profile_markdown,
        memory_markdown=memory_markdown,
        tool_definitions=tool_definitions,
    )

    # 渲染消息
    messages = renderer.render_document(document)

    # 验证缓存策略
    print("=" * 60)
    print("多级缓存测试结果")
    print("=" * 60)

    cache_stats = {
        "cached_sections": [],
        "non_cached_sections": [],
    }

    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        has_cache = "cache_control" in msg

        # 提取内容预览
        content = msg.get("content", "")
        if isinstance(content, list):
            content_preview = f"[{len(content)} blocks]"
        else:
            content_preview = content[:50] + "..." if len(content) > 50 else content

        print(f"\n[{i}] Role: {role}")
        print(f"    Cache: {'[CACHED]' if has_cache else '[NOT CACHED]'}")
        print(f"    Content: {content_preview}")

        if has_cache:
            cache_stats["cached_sections"].append((i, role, content_preview))
        else:
            cache_stats["non_cached_sections"].append((i, role, content_preview))

    # 统计结果
    print("\n" + "=" * 60)
    print("缓存统计")
    print("=" * 60)
    print(f"总消息数: {len(messages)}")
    print(f"已缓存: {len(cache_stats['cached_sections'])}")
    print(f"未缓存: {len(cache_stats['non_cached_sections'])}")

    # 验证缓存策略是否正确
    print("\n" + "=" * 60)
    print("缓存策略验证")
    print("=" * 60)

    # 检查系统消息中的缓存
    system_messages = [msg for msg in messages if msg.get("role") == "system"]
    print(f"\n系统消息总数: {len(system_messages)}")

    for msg in system_messages:
        content = msg.get("content", "")
        has_cache = "cache_control" in msg

        if "SOUL" in content:
            print(f"  - SOUL.md: {'[CACHED]' if has_cache else '[NOT CACHED]'} {'(expected)' if has_cache else '(ERROR!)'}")
        elif "USER PROFILE" in content or "用户画像" in content:
            print(f"  - User Profile: {'[CACHED]' if has_cache else '[NOT CACHED]'} {'(expected)' if has_cache else '(ERROR!)'}")
        elif "MEMORY" in content or "长期记忆" in content:
            print(f"  - Memory: {'[CACHED]' if has_cache else '[NOT CACHED]'} {'(expected)' if has_cache else '(ERROR!)'}")
        elif "工具目录" in content or "Tool" in content:
            print(f"  - Tool Directory: {'[CACHED]' if has_cache else '[NOT CACHED]'} {'(expected)' if has_cache else '(ERROR!)'}")

    # 检查历史对话中的缓存
    user_messages = [msg for msg in messages if msg.get("role") == "user"]
    assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]

    print(f"\n用户消息总数: {len(user_messages)}")
    for i, msg in enumerate(user_messages):
        has_cache = "cache_control" in msg
        content_preview = msg.get("content", "")[:30]
        is_last = (i == len(user_messages) - 1)
        expected = not is_last
        status = "[OK]" if (has_cache == expected) else "[FAIL]"
        print(f"  {status} User msg {i+1}: {'cached' if has_cache else 'not cached'} - {content_preview}... {'(latest)' if is_last else '(history)'}")

    print(f"\nAssistant messages: {len(assistant_messages)}")
    for i, msg in enumerate(assistant_messages):
        has_cache = "cache_control" in msg
        content_preview = msg.get("content", "")[:30]
        # 助手消息应该跟随对应的用户消息缓存策略
        print(f"  {'[OK]' if has_cache else '[FAIL]'} Assistant msg {i+1}: {'cached' if has_cache else 'not cached'} - {content_preview}...")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    # 返回验证结果
    return {
        "total_messages": len(messages),
        "cached_count": len(cache_stats["cached_sections"]),
        "non_cached_count": len(cache_stats["non_cached_sections"]),
        "messages": messages,
    }


if __name__ == "__main__":
    result = test_multilevel_cache()
    print(f"\n最终结果: {result['cached_count']}/{result['total_messages']} 消息已缓存")
