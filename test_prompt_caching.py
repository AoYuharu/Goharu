"""
测试 Prompt Caching 实现
"""
import sys
import codecs

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Prompting.PromptSection import PromptSection
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


def test_prompt_section_cache_control():
    """测试 PromptSection 支持 cache_control"""
    print("\n=== 测试 PromptSection cache_control ===")

    # 创建带缓存标记的 section
    section = PromptSection(
        kind="system",
        title="test",
        content="Test content",
        cache_control={"type": "ephemeral"}
    )

    assert section.cache_control == {"type": "ephemeral"}, "cache_control 应该被保存"
    print("[OK] PromptSection 正确支持 cache_control")
    return True


def test_prompt_renderer_cache_control():
    """测试 PromptRenderer 传递 cache_control"""
    print("\n=== 测试 PromptRenderer 传递 cache_control ===")

    renderer = PromptRenderer()

    # 测试带缓存的 section
    section_with_cache = PromptSection(
        kind="system",
        title="cached",
        content="Cached content",
        cache_control={"type": "ephemeral"}
    )

    message = renderer.render_section(section_with_cache)
    assert message["role"] == "system", "角色应该是 system"
    assert message["content"] == "Cached content", "内容应该正确"
    assert message.get("cache_control") == {"type": "ephemeral"}, "cache_control 应该被传递"
    print("[OK] PromptRenderer 正确传递 cache_control")

    # 测试不带缓存的 section
    section_without_cache = PromptSection(
        kind="system",
        title="normal",
        content="Normal content"
    )

    message2 = renderer.render_section(section_without_cache)
    assert "cache_control" not in message2, "不应该有 cache_control"
    print("[OK] PromptRenderer 正确处理无缓存的 section")

    return True


def test_prompt_assembler_cache_marks():
    """测试 PromptAssembler 为关键部分添加缓存标记"""
    print("\n=== 测试 PromptAssembler 缓存标记 ===")

    assembler = PromptAssembler()

    # 测试 SOUL section
    soul_section = assembler._build_soul_section("# SOUL\nTest soul content")
    assert soul_section is not None, "SOUL section 应该被创建"
    assert soul_section.cache_control == {"type": "ephemeral"}, "SOUL 应该有缓存标记"
    print("[OK] SOUL section 有缓存标记")

    # 测试 Tool Directory section
    tool_section = assembler._build_tool_directory_section([{"name": "test_tool"}])
    assert tool_section is not None, "Tool Directory section 应该被创建"
    assert tool_section.cache_control == {"type": "ephemeral"}, "Tool Directory 应该有缓存标记"
    print("[OK] Tool Directory section 有缓存标记")

    # 测试 User Profile section（不应该有缓存）
    user_section = assembler._build_user_profile_section("User profile")
    assert user_section is not None, "User Profile section 应该被创建"
    assert user_section.cache_control is None, "User Profile 不应该有缓存标记"
    print("[OK] User Profile section 没有缓存标记（正确）")

    return True


def test_full_document_rendering():
    """测试完整文档渲染包含缓存标记"""
    print("\n=== 测试完整文档渲染 ===")

    assembler = PromptAssembler()
    renderer = PromptRenderer()

    document = assembler.build_actor_document(
        history=[{"role": "user", "content": "Hello"}],
        soul_markdown="# SOUL\nTest soul",
        tool_definitions=[{"name": "test_tool", "description": "Test"}]
    )

    messages = renderer.render_document(document)

    # 检查是否有带缓存标记的消息
    cached_messages = [msg for msg in messages if msg.get("cache_control")]
    print(f"[INFO] 总消息数: {len(messages)}")
    print(f"[INFO] 带缓存标记的消息数: {len(cached_messages)}")

    assert len(cached_messages) >= 2, "至少应该有 2 个带缓存标记的消息（SOUL + Tool Directory）"

    # 打印缓存标记的消息
    for i, msg in enumerate(cached_messages):
        print(f"[INFO] 缓存消息 {i+1}: role={msg['role']}, cache_control={msg['cache_control']}")

    print("[OK] 完整文档渲染包含缓存标记")
    return True


def main():
    try:
        success1 = test_prompt_section_cache_control()
        success2 = test_prompt_renderer_cache_control()
        success3 = test_prompt_assembler_cache_marks()
        success4 = test_full_document_rendering()

        if success1 and success2 and success3 and success4:
            print("\n[OK] 所有测试通过")
            print("  - PromptSection 支持 cache_control")
            print("  - PromptRenderer 正确传递 cache_control")
            print("  - PromptAssembler 为 SOUL 和 Tool Directory 添加缓存标记")
            print("  - 完整文档渲染包含缓存标记")
            print("\n缓存策略：")
            print("  ✅ SOUL.md - 缓存（角色设定稳定）")
            print("  ✅ Tool Directory - 缓存（工具定义稳定）")
            print("  ❌ User Profile - 不缓存（可能频繁更新）")
            print("  ❌ Memory - 不缓存（可能频繁更新）")
            print("  ❌ 对话历史 - 不缓存（每次都变化）")
            return 0
        else:
            print("\n[FAIL] 部分测试失败")
            return 1
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
