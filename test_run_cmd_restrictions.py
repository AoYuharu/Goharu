"""
测试 run_cmd 工具的文件操作限制
"""
import sys
import codecs

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Tools.builtin.core_tools import run_cmd


def test_echo_file_operations():
    """测试 echo 文件操作被拒绝"""
    print("\n=== 测试 echo 文件操作限制 ===")

    test_cases = [
        ("echo hello > test.txt", "创建文件"),
        ("echo world >> test.txt", "追加文件"),
        ("echo content > output.log", "写入日志"),
        ("ECHO test > file.txt", "大写 ECHO"),
        ("  echo   data   >   file.txt  ", "带空格的 echo"),
    ]

    for cmd, description in test_cases:
        result = run_cmd(cmd)
        if "ERROR" in result and "Write tool" in result:
            print(f"[OK] {description}: 正确拒绝")
        else:
            print(f"[FAIL] {description}: 应该被拒绝但没有")
            print(f"  命令: {cmd}")
            print(f"  结果: {result[:100]}")
            return False

    return True


def test_other_forbidden_commands():
    """测试其他被禁止的文件操作命令"""
    print("\n=== 测试其他禁止命令 ===")

    test_cases = [
        ("cat file.txt", "cat"),
        ("head -n 10 file.txt", "head"),
        ("tail -f log.txt", "tail"),
        ("sed 's/old/new/' file.txt", "sed"),
        ("awk '{print $1}' file.txt", "awk"),
        ("type file.txt | findstr pattern", "type with pipe"),
    ]

    for cmd, description in test_cases:
        result = run_cmd(cmd)
        if "ERROR" in result and "not allowed" in result:
            print(f"[OK] {description}: 正确拒绝")
        else:
            print(f"[FAIL] {description}: 应该被拒绝但没有")
            print(f"  命令: {cmd}")
            print(f"  结果: {result[:100]}")
            return False

    return True


def test_allowed_commands():
    """测试允许的系统操作命令"""
    print("\n=== 测试允许的命令 ===")

    test_cases = [
        ("dir /b", "列出文件"),
        ("echo test", "纯 echo（不涉及文件）"),
        ("if exist test.txt echo exists", "检查文件存在"),
    ]

    for cmd, description in test_cases:
        result = run_cmd(cmd)
        if "ERROR" not in result:
            print(f"[OK] {description}: 正确允许")
        else:
            print(f"[FAIL] {description}: 不应该被拒绝")
            print(f"  命令: {cmd}")
            print(f"  结果: {result[:100]}")
            return False

    return True


def main():
    try:
        success1 = test_echo_file_operations()
        success2 = test_other_forbidden_commands()
        success3 = test_allowed_commands()

        if success1 and success2 and success3:
            print("\n[OK] 所有测试通过")
            print("  - run_cmd 正确拒绝 echo 文件操作")
            print("  - run_cmd 正确拒绝其他文件操作命令")
            print("  - run_cmd 正确允许系统操作命令")
            print("  - 错误消息引导使用专用工具")
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
