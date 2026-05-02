"""
测试 /sysprompt 命令功能
"""
import sys
import codecs
from unittest.mock import Mock, MagicMock
from Agent.ActorAgent import ActorAgent
from Memory.MemoryManager import MemoryManager

# 修复 Windows 编码问题
if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())


def test_system_prompt_extraction():
    """测试系统提示词提取功能"""
    print("测试系统提示词提取...")

    # 创建模拟的 tool_runtime 和 memory_manager
    mock_runtime = Mock()
    mock_runtime.last_tool_definitions = None

    memory_manager = MemoryManager()

    # 创建 ActorAgent
    actor = ActorAgent(mock_runtime, memory_manager)

    # 构建消息
    messages = actor.build_messages()

    # 提取系统消息
    system_messages = [msg for msg in messages if msg.get("role") == "system"]

    print(f"[OK] 找到 {len(system_messages)} 个系统提示词部分")

    if system_messages:
        total_chars = sum(len(msg.get("content", "")) for msg in system_messages)
        print(f"[OK] 系统提示词总字符数: {total_chars}")

        for i, msg in enumerate(system_messages, 1):
            content = msg.get("content", "")
            preview = content[:100].replace("\n", " ")
            print(f"  Section {i}: {len(content)} chars - {preview}...")
    else:
        print("[WARN] 警告: 没有找到系统提示词")

    return len(system_messages) > 0


if __name__ == "__main__":
    try:
        success = test_system_prompt_extraction()
        if success:
            print("\n[OK] 测试通过")
            sys.exit(0)
        else:
            print("\n[FAIL] 测试失败: 没有找到系统提示词")
            sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
