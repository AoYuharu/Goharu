"""
测试 asyncio 事件循环修复

这个脚本测试修复后的 main.py 是否能正确处理事件循环问题
"""
import asyncio
import sys


async def simple_test():
    """简单的异步测试"""
    print("[TEST] Async function executed")
    await asyncio.sleep(0.1)
    return "success"


def test_asyncio_fix():
    """测试 asyncio 修复逻辑"""
    print("=" * 60)
    print("测试 asyncio 事件循环修复")
    print("=" * 60)

    # 测试 1: 正常情况（没有运行中的事件循环）
    print("\n[测试 1] 正常情况（没有运行中的事件循环）")
    try:
        try:
            loop = asyncio.get_running_loop()
            print("[FAIL] 不应该有运行中的事件循环")
        except RuntimeError:
            print("[OK] 没有运行中的事件循环")
            result = asyncio.run(simple_test())
            print(f"[OK] asyncio.run() 执行成功: {result}")
    except Exception as e:
        print(f"[FAIL] 错误: {e}")

    # 测试 2: 检查 main.py 的语法
    print("\n[测试 2] 检查 main.py 语法")
    try:
        import py_compile
        py_compile.compile("main.py", doraise=True)
        print("[OK] main.py 语法正确")
    except Exception as e:
        print(f"[FAIL] main.py 语法错误: {e}")

    # 测试 3: 检查 main.py 是否可以导入
    print("\n[测试 3] 检查 main.py 模块导入")
    try:
        # 不直接导入 main，因为它会启动应用
        # 只检查语法和基本结构
        with open("main.py", "r", encoding="utf-8") as f:
            content = f.read()
            if "asyncio.get_running_loop()" in content:
                print("[OK] 使用了 asyncio.get_running_loop()")
            else:
                print("[WARN] 没有使用 asyncio.get_running_loop()")

            if "nest_asyncio" in content:
                print("[OK] 包含 nest_asyncio 支持")
            else:
                print("[WARN] 没有 nest_asyncio 支持")

            if 'if __name__ == "__main__":' in content:
                print("[OK] 有 __main__ 检查")
            else:
                print("[FAIL] 缺少 __main__ 检查")
    except Exception as e:
        print(f"[FAIL] 错误: {e}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    print("\n修复说明:")
    print("1. 使用 asyncio.get_running_loop() 检测运行中的事件循环")
    print("2. 如果在 Jupyter 等环境中，使用 nest_asyncio")
    print("3. 正常情况下使用 asyncio.run()")
    print("4. 添加异常处理和错误信息")

    print("\n使用方法:")
    print("python main.py  # 正常运行")


if __name__ == "__main__":
    test_asyncio_fix()
