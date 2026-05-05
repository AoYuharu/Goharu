"""
集成测试：验证防护系统在实际 agent 中的工作
"""
import sys
import codecs
import asyncio

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Tools.guard import ToolCallGuard
from Memory.ToolCall import ToolCall


async def test_integration():
    """测试防护系统与 ToolCall 解析器的集成"""

    # 模拟工具列表
    tools = [
        {
            "name": "run_cmd",
            "inputSchema": {
                "properties": {
                    "cmd": {"type": "string"},
                    "timeout": {"type": "integer"},
                }
            }
        },
        {
            "name": "Read",
            "inputSchema": {
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                }
            }
        }
    ]

    guard = ToolCallGuard(tools)

    # 测试场景1：MiniMax 格式 + 大小写错误 + 类型错误
    minimax_output = '<invoke name="RUN_CMD", "arguments": {"cmd": "ls", "timeout": "30"}}'

    tool_call = ToolCall.try_from_text(minimax_output)
    assert tool_call is not None, "ToolCall 解析失败"

    result = guard.guard(tool_call.tool_name, tool_call.arguments)

    print("场景1：MiniMax 格式 + 大小写错误 + 类型错误")
    print(f"  原始工具名: {tool_call.tool_name}")
    print(f"  修复后: {result['tool_name']}")
    print(f"  原始参数: {tool_call.arguments}")
    print(f"  修复后: {result['arguments']}")
    print(f"  防护日志:")
    for log in result['logs']:
        print(f"    {log}")

    assert result['success'] is True
    assert result['tool_name'] == 'run_cmd'
    assert result['arguments']['timeout'] == 30  # 字符串转整数
    print("  ✓ 通过\n")

    # 测试场景2：标准 JSON 格式（ToolCall 能解析）
    json_output = '{"tool": "read", "arguments": {"path": "E:\\\\test.py", "start_line": "1"}}'

    tool_call = ToolCall.try_from_text(json_output)
    assert tool_call is not None, "ToolCall 解析失败"

    result = guard.guard(tool_call.tool_name, tool_call.arguments)

    print("场景2：标准 JSON + 类型错误")
    print(f"  原始工具名: {tool_call.tool_name}")
    print(f"  修复后: {result['tool_name']}")
    print(f"  原始参数: {tool_call.arguments}")
    print(f"  修复后: {result['arguments']}")
    print(f"  防护日志:")
    for log in result['logs']:
        print(f"    {log}")

    assert result['success'] is True
    assert result['tool_name'] == 'Read'
    assert result['arguments']['start_line'] == 1  # 字符串转整数
    print("  ✓ 通过\n")

    # 测试场景3：工具名完全错误（应该失败）
    bad_output = '{"tool": "unknown_tool_xyz", "arguments": {"param": "value"}}'

    tool_call = ToolCall.try_from_text(bad_output)
    assert tool_call is not None, "ToolCall 解析失败"

    result = guard.guard(tool_call.tool_name, tool_call.arguments)

    print("场景3：工具名完全错误（预期失败）")
    print(f"  原始工具名: {tool_call.tool_name}")
    print(f"  结果: {result['success']}")
    print(f"  错误信息: {result['error']}")

    assert result['success'] is False
    print("  ✓ 正确拒绝\n")

    print("✅ 所有集成测试通过！")


if __name__ == "__main__":
    asyncio.run(test_integration())
