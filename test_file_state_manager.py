"""
单元测试：FileStateManager
"""
from Memory.FileStateManager import FileStateManager
import json


def test_file_state_manager():
    """测试 FileStateManager 基本功能"""
    print("=" * 60)
    print("测试 FileStateManager")
    print("=" * 60)

    fsm = FileStateManager()

    # 测试 1: 记录文件读取
    print("\n[测试 1] 记录文件读取")
    fsm.record_file_read(
        path="test.py",
        content="def hello():\n    print('Hello')",
        start_line=1,
        end_line=2,
        total_lines=2
    )
    assert fsm.has_file("test.py"), "应该记录了 test.py"
    assert fsm.get_file_content("test.py") is not None, "应该有文件内容"
    print("[OK] 文件读取记录成功")

    # 测试 2: 记录工具调用
    print("\n[测试 2] 记录工具调用")
    fsm.record_tool_call(
        tool_name="Read",
        arguments={"path": "test.py"},
        result='{"file": "test.py", "content": [...]}',
        result_preview="Read test.py"
    )
    stats = fsm.get_stats()
    assert stats["tool_calls_count"] == 1, "应该记录了 1 次工具调用"
    print("[OK] 工具调用记录成功")

    # 测试 3: 获取 Reflection 上下文
    print("\n[测试 3] 获取 Reflection 上下文")
    context = fsm.get_reflection_context()
    assert "files" in context, "应该包含 files"
    assert "tool_calls" in context, "应该包含 tool_calls"
    assert "files_summary" in context, "应该包含 files_summary"
    assert "tool_calls_summary" in context, "应该包含 tool_calls_summary"
    print("[OK] Reflection 上下文获取成功")

    # 测试 4: 文件摘要
    print("\n[测试 4] 文件摘要")
    summary = fsm.get_files_summary()
    assert "test.py" in summary, "摘要应该包含文件名"
    assert "def hello()" in summary, "摘要应该包含文件内容"
    print("[OK] 文件摘要生成成功")
    print(f"摘要预览:\n{summary[:200]}...")

    # 测试 5: 工具调用摘要
    print("\n[测试 5] 工具调用摘要")
    tool_summary = fsm.get_tool_calls_summary()
    assert "Read" in tool_summary, "摘要应该包含工具名"
    print("[OK] 工具调用摘要生成成功")
    print(f"摘要预览:\n{tool_summary[:200]}...")

    # 测试 6: 统计信息
    print("\n[测试 6] 统计信息")
    stats = fsm.get_stats()
    print(f"文件数: {stats['files_count']}")
    print(f"工具调用数: {stats['tool_calls_count']}")
    print(f"内容大小: {stats['total_content_size']} 字节")
    assert stats["files_count"] == 1, "应该有 1 个文件"
    assert stats["tool_calls_count"] == 1, "应该有 1 次工具调用"
    print("[OK] 统计信息正确")

    # 测试 7: 清空
    print("\n[测试 7] 清空记录")
    fsm.clear()
    stats = fsm.get_stats()
    assert stats["files_count"] == 0, "清空后应该没有文件"
    assert stats["tool_calls_count"] == 0, "清空后应该没有工具调用"
    print("[OK] 清空成功")

    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    test_file_state_manager()
