"""
测试补丁式 Edit 和 Write 工具
"""
import sys
import codecs
import asyncio
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Tools.builtin.file_tools import Write, Edit, Read


async def test_write_tool():
    """测试 Write 工具创建新文件"""
    print("\n=== 测试 Write 工具 ===")

    test_file = Path("test_write_output.txt")

    # 清理旧文件
    if test_file.exists():
        test_file.unlink()

    # 测试创建新文件
    result = await Write(
        path=str(test_file),
        content="Hello, World!\nThis is a test file.\nLine 3.",
        actor_id="test"
    )
    print(f"Write 结果: {result}")

    # 验证文件存在
    assert test_file.exists(), "文件应该被创建"
    content = test_file.read_text(encoding="utf-8")
    assert "Hello, World!" in content, "文件内容应该包含 Hello, World!"
    print("[OK] Write 工具创建文件成功")

    # 测试重复创建（应该失败）
    result2 = await Write(
        path=str(test_file),
        content="Should fail",
        actor_id="test"
    )
    assert "error" in result2.lower(), "重复创建应该失败"
    print("[OK] Write 工具正确拒绝覆盖已存在的文件")

    # 清理
    test_file.unlink()
    return True


async def test_patch_edit():
    """测试补丁式 Edit 工具"""
    print("\n=== 测试补丁式 Edit 工具 ===")

    test_file = Path("test_edit_output.txt")

    # 创建测试文件
    test_file.write_text(
        "def hello():\n    print('Hello')\n\ndef world():\n    print('World')\n",
        encoding="utf-8"
    )

    # 先读取文件（授予权限）
    read_result = await Read(path=str(test_file), actor_id="test")
    print(f"Read 结果: {read_result[:100]}...")

    # 测试补丁式修改
    result = await Edit(
        path=str(test_file),
        old_string="def hello():\n    print('Hello')",
        new_string="def hello():\n    print('Hello, World!')",
        actor_id="test"
    )
    print(f"Edit 结果: {result}")

    # 验证修改
    content = test_file.read_text(encoding="utf-8")
    assert "Hello, World!" in content, "修改应该生效"
    assert "def world():" in content, "其他内容应该保持不变"
    print("[OK] 补丁式 Edit 成功")

    # 测试不唯一的 old_string（应该失败）
    result2 = await Edit(
        path=str(test_file),
        old_string="print",  # 出现多次
        new_string="print_new",
        actor_id="test"
    )
    assert "不唯一" in result2 or "多次" in result2, "不唯一的 old_string 应该失败"
    print("[OK] Edit 工具正确检测不唯一的 old_string")

    # 测试不存在的 old_string（应该失败）
    result3 = await Edit(
        path=str(test_file),
        old_string="this does not exist",
        new_string="new content",
        actor_id="test"
    )
    assert "不存在" in result3, "不存在的 old_string 应该失败"
    print("[OK] Edit 工具正确检测不存在的 old_string")

    # 清理
    test_file.unlink()
    return True


async def main():
    try:
        success1 = await test_write_tool()
        success2 = await test_patch_edit()

        if success1 and success2:
            print("\n[OK] 所有测试通过")
            print("  - Write 工具可以创建新文件")
            print("  - Write 工具拒绝覆盖已存在的文件")
            print("  - Edit 工具支持补丁式修改")
            print("  - Edit 工具检测不唯一和不存在的 old_string")
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
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
