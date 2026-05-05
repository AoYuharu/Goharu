"""
测试保护系统：LLM重试、工具调用沙箱化
"""
import asyncio
import sys
sys.path.insert(0, '.')

from Agent.LLMCore import LLMCore
from Tools.runtime import InProcessToolRuntime


async def test_tool_sandbox():
    """测试工具调用沙箱化"""
    print("=" * 60)
    print("测试1: 工具调用沙箱化")
    print("=" * 60)

    runtime = InProcessToolRuntime()
    await runtime.initialize()

    # 测试1: 调用不存在的工具
    print("\n[测试1.1] 调用不存在的工具...")
    result = await runtime.call_tool("non_existent_tool", {})
    print(f"结果类型: {type(result.content)}")

    # 检查是否返回错误（可能是JSON字符串或字典）
    import json
    error_found = False
    if isinstance(result.content, dict) and "error" in result.content:
        error_found = True
        print("[OK] 工具调用失败返回错误信息（字典格式），未崩溃")
        print(f"错误信息: {result.content['error'][:100]}")
    elif isinstance(result.content, str):
        try:
            parsed = json.loads(result.content)
            if "error" in parsed:
                error_found = True
                print("[OK] 工具调用失败返回错误信息（JSON字符串格式），未崩溃")
                print(f"错误信息: {parsed['error'][:100]}")
        except:
            pass

    if not error_found:
        print("[FAIL] 未正确处理工具调用失败")

    # 测试2: 调用存在的工具但参数错误
    print("\n[测试1.2] 调用工具但参数错误...")
    result = await runtime.call_tool("Read", {"invalid_param": "test"})
    print(f"结果类型: {type(result.content)}")

    error_found = False
    if isinstance(result.content, dict) and "error" in result.content:
        error_found = True
        print("[OK] 参数错误返回错误信息（字典格式），未崩溃")
        print(f"错误信息: {result.content['error'][:100]}")
    elif isinstance(result.content, str):
        try:
            parsed = json.loads(result.content)
            if "error" in parsed:
                error_found = True
                print("[OK] 参数错误返回错误信息（JSON字符串格式），未崩溃")
                print(f"错误信息: {parsed['error'][:100]}")
        except:
            pass

    if not error_found:
        print("[FAIL] 未正确处理参数错误")

    await runtime.close()
    print("\n[总结] 工具调用沙箱化测试完成")


def test_llm_retry_config():
    """测试LLM重试配置"""
    print("\n" + "=" * 60)
    print("测试2: LLM重试机制配置")
    print("=" * 60)

    try:
        core = LLMCore()
        print(f"[OK] LLMCore 初始化成功")
        print(f"Provider: {core.provider}")

        # 检查是否有重试相关的代码
        import inspect
        source = inspect.getsource(core._generate_anthropic_compatible)

        if "max_api_retries" in source:
            print("[OK] 检测到 API 重试机制")
        else:
            print("[WARN] 未检测到 API 重试机制")

        if "max_truncation_retries" in source:
            print("[OK] 检测到输出截断重试机制")
        else:
            print("[WARN] 未检测到输出截断重试机制")

        if "time.sleep" in source or "delay" in source:
            print("[OK] 检测到指数退避延迟")
        else:
            print("[WARN] 未检测到指数退避延迟")

        print("\n[总结] LLM重试机制配置检查完成")

    except Exception as e:
        print(f"[ERROR] LLMCore 初始化失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    print("保护系统测试")
    print("=" * 60)
    print()

    # 测试1: 工具调用沙箱化
    await test_tool_sandbox()

    # 测试2: LLM重试配置
    test_llm_retry_config()

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
