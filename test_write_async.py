"""
测试 Write 工具的 async 检测和执行
"""
import sys
import codecs
import asyncio

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Tools.registry import registry
from Tools.builtin.file_tools import Write
from pathlib import Path


def test_write_registration():
    """测试 Write 工具是否正确注册为 async"""
    print("\n=== 测试 Write 工具注册 ===")

    entry = registry.get_entry("Write")
    if entry is None:
        print("[FAIL] Write 工具未注册")
        return False

    print(f"[INFO] Write 工具已注册")
    print(f"[INFO] is_async: {entry.is_async}")
    print(f"[INFO] handler: {entry.handler}")
    print(f"[INFO] asyncio.iscoroutinefunction: {asyncio.iscoroutinefunction(entry.handler)}")

    if not entry.is_async:
        print("[FAIL] Write 工具未标记为 async")
        return False

    print("[OK] Write 工具正确标记为 async")
    return True


def test_write_sync_dispatch():
    """测试通过 dispatch_sync 调用 Write"""
    print("\n=== 测试 dispatch_sync 调用 Write ===")

    test_file = Path("test_write_sync.txt")
    if test_file.exists():
        test_file.unlink()

    try:
        result = registry.dispatch_sync("Write", {
            "path": str(test_file),
            "content": "Hello from dispatch_sync",
            "actor_id": "test"
        })

        print(f"[INFO] 结果: {result}")

        if test_file.exists():
            content = test_file.read_text(encoding="utf-8")
            print(f"[INFO] 文件内容: {content}")
            print("[OK] dispatch_sync 成功调用 Write")
            test_file.unlink()
            return True
        else:
            print("[FAIL] 文件未创建")
            return False
    except Exception as e:
        print(f"[FAIL] 调用失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    try:
        success1 = test_write_registration()
        success2 = test_write_sync_dispatch()

        if success1 and success2:
            print("\n[OK] 所有测试通过")
            print("  - Write 工具正确注册为 async")
            print("  - dispatch_sync 可以正确调用 Write")
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
