"""
工具使用演示 - 展示 Agent 如何使用 Glob, Grep, Read 工具
"""
import asyncio
import sys
import os

# 设置 UTF-8 编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Agent.ActorAgent import ActorAgent
from Agent.ReflectionAgent import ReflectionAgent
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from configurationLoader import config


async def demo_glob_tool():
    """演示 Glob 工具的使用"""
    print("\n" + "=" * 60)
    print("演示 1: Glob 工具 - 查找文件")
    print("=" * 60)

    runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))

    try:
        await runtime.initialize()

        print("\n场景：查找所有 Python 测试文件")
        print("工具调用：Glob(pattern='test_*.py')")

        result = await runtime.call_tool("Glob", {"pattern": "test_*.py"})

        import json
        data = json.loads(result.content)

        print(f"\n结果：")
        print(f"  - 找到 {data['numFiles']} 个文件")
        print(f"  - 耗时 {data['durationMs']} ms")
        print(f"  - 是否截断：{data['truncated']}")

        if data['filenames']:
            print(f"\n文件列表（前 5 个）：")
            for filename in data['filenames'][:5]:
                print(f"  - {filename}")

    finally:
        await runtime.close()


async def demo_grep_tool():
    """演示 Grep 工具的使用"""
    print("\n" + "=" * 60)
    print("演示 2: Grep 工具 - 搜索代码")
    print("=" * 60)

    runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))

    try:
        await runtime.initialize()

        print("\n场景：搜索所有包含 'async def' 的代码")
        print("工具调用：Grep(pattern='async def', max_results=5)")

        result = await runtime.call_tool("Grep", {
            "pattern": "async def",
            "path": ".",
            "max_results": 5
        })

        import json
        data = json.loads(result.content)

        print(f"\n结果：")
        print(f"  - 找到 {data['count']} 个匹配")

        if data['matches']:
            print(f"\n匹配列表（前 3 个）：")
            for match in data['matches'][:3]:
                print(f"  - {match['file']}:{match['line']}")
                print(f"    {match['content']}")

    finally:
        await runtime.close()


async def demo_read_tool():
    """演示 Read 工具的使用"""
    print("\n" + "=" * 60)
    print("演示 3: Read 工具 - 读取文件")
    print("=" * 60)

    runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))

    try:
        await runtime.initialize()

        print("\n场景：读取 config.yaml 的前 10 行")
        print("工具调用：Read(path='config.yaml', start_line=1, end_line=10)")

        result = await runtime.call_tool("Read", {
            "path": os.path.abspath("config.yaml"),
            "start_line": 1,
            "end_line": 10
        })

        import json
        data = json.loads(result.content)

        print(f"\n结果：")
        print(f"  - 文件：{data['file']}")
        print(f"  - 总行数：{data['total_lines']}")
        print(f"  - 读取范围：{data['start_line']}-{data['end_line']}")

        print(f"\n内容预览（前 5 行）：")
        for item in data['content'][:5]:
            print(f"  {item['line']:3d} | {item['text']}")

    finally:
        await runtime.close()


async def demo_workflow():
    """演示完整的工作流程"""
    print("\n" + "=" * 60)
    print("演示 4: 完整工作流程")
    print("=" * 60)

    runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))

    try:
        await runtime.initialize()

        print("\n场景：找到所有测试文件，搜索特定函数，然后读取文件")

        # 步骤 1: 使用 Glob 查找测试文件
        print("\n步骤 1: 使用 Glob 查找测试文件")
        glob_result = await runtime.call_tool("Glob", {"pattern": "test_*.py"})
        import json
        glob_data = json.loads(glob_result.content)
        print(f"  ✓ 找到 {glob_data['numFiles']} 个测试文件")

        # 步骤 2: 使用 Grep 搜索特定函数
        print("\n步骤 2: 使用 Grep 搜索 'def test_' 函数")
        grep_result = await runtime.call_tool("Grep", {
            "pattern": "def test_",
            "path": ".",
            "max_results": 3
        })
        grep_data = json.loads(grep_result.content)
        print(f"  ✓ 找到 {grep_data['count']} 个匹配")

        # 步骤 3: 使用 Read 读取第一个匹配的文件
        if grep_data['matches']:
            first_match = grep_data['matches'][0]
            file_path = first_match['file']
            line_num = first_match['line']

            print(f"\n步骤 3: 使用 Read 读取 {os.path.basename(file_path)} 的前 20 行")
            read_result = await runtime.call_tool("Read", {
                "path": file_path,
                "start_line": 1,
                "end_line": 20
            })
            read_data = json.loads(read_result.content)
            print(f"  ✓ 成功读取 {read_data['end_line']} 行")
            print(f"  ✓ 文件总行数：{read_data['total_lines']}")

        print("\n✓ 工作流程完成！")

    finally:
        await runtime.close()


async def main():
    """主函数"""
    print("=" * 60)
    print("TableHelper 工具使用演示")
    print("=" * 60)

    # 演示 1: Glob 工具
    await demo_glob_tool()

    # 演示 2: Grep 工具
    await demo_grep_tool()

    # 演示 3: Read 工具
    await demo_read_tool()

    # 演示 4: 完整工作流程
    await demo_workflow()

    print("\n" + "=" * 60)
    print("演示完成！")
    print("=" * 60)
    print("\n提示：")
    print("  - 使用 Glob 查找文件（按名称模式）")
    print("  - 使用 Grep 搜索内容（按文本模式）")
    print("  - 使用 Read 读取文件（授予 Edit 权限）")
    print("  - Grep 不授予 Edit 权限，必须先 Read")


if __name__ == "__main__":
    asyncio.run(main())
