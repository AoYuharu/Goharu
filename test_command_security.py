"""
测试命令安全检查功能
验证危险命令拦截和安全命令放行
"""
from Tools.security import CommandSecurityChecker, check_command_safety


def test_dangerous_commands():
    """测试危险命令拦截"""
    print("=" * 60)
    print("Testing Dangerous Commands")
    print("=" * 60)

    dangerous_commands = [
        # 系统关机/重启
        "shutdown /s /t 0",
        "shutdown -s -t 0",
        "restart",
        "reboot",
        "poweroff",

        # 危险删除
        "rm -rf /",
        "rm -rf *",
        "del /s /q C:\\*",
        "rmdir /s /q C:\\Users",
        "format C:",

        # 磁盘操作
        "diskpart",
        "fdisk /dev/sda",

        # 权限提升
        "sudo rm -rf /",
        "runas /user:Administrator cmd",

        # 注册表
        "reg delete HKLM\\Software",
        "reg add HKLM\\System",

        # 进程终止
        "taskkill /f /im *",

        # 危险脚本
        "powershell -encodedcommand ABC123",
        "powershell -enc ABC123",
    ]

    passed = 0
    failed = 0

    for cmd in dangerous_commands:
        is_safe, error_msg = check_command_safety(cmd)
        if not is_safe:
            print(f"\n[PASS] Blocked: {cmd}")
            print(f"       Reason: {error_msg.split('Matched pattern:')[1].split()[0] if 'Matched pattern:' in error_msg else 'Security check'}")
            passed += 1
        else:
            print(f"\n[FAIL] NOT blocked: {cmd}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Dangerous Commands Test: {passed} passed, {failed} failed")
    print(f"{'=' * 60}\n")
    return failed == 0


def test_safe_commands():
    """测试安全命令放行"""
    print("=" * 60)
    print("Testing Safe Commands")
    print("=" * 60)

    safe_commands = [
        # 目录操作
        "dir",
        "dir /s",
        "mkdir test_folder",
        "cd test_folder",

        # 文件查看（非文件操作工具）
        "if exist file.txt echo exists",

        # 脚本执行
        "python script.py",
        "python -m pytest",
        "powershell -File script.ps1",

        # 进程查看
        "tasklist",
        "tasklist | findstr python",

        # 网络操作
        "ping 8.8.8.8",
        "ipconfig",
        "ipconfig /all",

        # 系统信息
        "systeminfo",
        "ver",
        "echo Hello World",

        # 文件复制（非破坏性）
        "copy file1.txt file2.txt",
        "move file1.txt folder\\",
    ]

    passed = 0
    failed = 0

    for cmd in safe_commands:
        is_safe, error_msg = check_command_safety(cmd)
        if is_safe:
            print(f"\n[PASS] Allowed: {cmd}")
            passed += 1
        else:
            print(f"\n[FAIL] Blocked (should be allowed): {cmd}")
            print(f"       Error: {error_msg[:100]}...")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Safe Commands Test: {passed} passed, {failed} failed")
    print(f"{'=' * 60}\n")
    return failed == 0


def test_confirmation_required():
    """测试需要确认的命令"""
    print("=" * 60)
    print("Testing Confirmation Required Commands")
    print("=" * 60)

    confirmation_commands = [
        "del file.txt",
        "del /p file.txt",
        "rmdir folder",
        "taskkill /pid 1234",
        "reg query HKLM\\Software",
    ]

    passed = 0
    failed = 0

    for cmd in confirmation_commands:
        is_safe, error_msg = check_command_safety(cmd)
        # 根据配置，这些命令可能被拦截或放行
        if not is_safe and "SECURITY WARNING" in error_msg:
            print(f"\n[INFO] Requires confirmation: {cmd}")
            print(f"       Status: Blocked (allow_confirmation=false)")
            passed += 1
        elif is_safe:
            print(f"\n[INFO] Allowed: {cmd}")
            print(f"       Status: Allowed (allow_confirmation=true or not in list)")
            passed += 1
        else:
            print(f"\n[FAIL] Unexpected behavior: {cmd}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Confirmation Commands Test: {passed} checked, {failed} unexpected")
    print(f"{'=' * 60}\n")
    return failed == 0


def test_pattern_matching():
    """测试模式匹配逻辑"""
    print("=" * 60)
    print("Testing Pattern Matching Logic")
    print("=" * 60)

    test_cases = [
        # (command, should_block, reason)
        ("shutdown /s", True, "shutdown command"),
        ("echo shutdown", False, "shutdown in echo (safe)"),
        ("format C:", True, "format command"),
        ("reformat code", False, "reformat (not format command)"),
        ("rm -rf /tmp", True, "rm -rf pattern"),
        ("echo rm -rf", False, "rm -rf in echo (safe)"),
        ("del /s /q C:\\temp", True, "del /s pattern"),
        ("model.delete()", False, "delete method (not del command)"),
    ]

    passed = 0
    failed = 0

    for cmd, should_block, reason in test_cases:
        is_safe, error_msg = check_command_safety(cmd)
        blocked = not is_safe

        if blocked == should_block:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
            failed += 1

        print(f"\n[{status}] {cmd}")
        print(f"       Expected: {'blocked' if should_block else 'allowed'}")
        print(f"       Actual: {'blocked' if blocked else 'allowed'}")
        print(f"       Reason: {reason}")

    print(f"\n{'=' * 60}")
    print(f"Pattern Matching Test: {passed} passed, {failed} failed")
    print(f"{'=' * 60}\n")
    return failed == 0


def test_security_checker_config():
    """测试安全检查器配置"""
    print("=" * 60)
    print("Testing Security Checker Configuration")
    print("=" * 60)

    checker = CommandSecurityChecker()

    print(f"\nSecurity enabled: {checker.enabled}")
    print(f"Allow confirmation: {checker.allow_confirmation}")
    print(f"Dangerous commands count: {len(checker.dangerous_commands)}")
    print(f"Confirmation required count: {len(checker.require_confirmation)}")

    print(f"\nDangerous commands list:")
    for cmd in checker.dangerous_commands[:10]:  # 显示前10个
        print(f"  - {cmd}")
    if len(checker.dangerous_commands) > 10:
        print(f"  ... and {len(checker.dangerous_commands) - 10} more")

    print(f"\nConfirmation required list:")
    for cmd in checker.require_confirmation:
        print(f"  - {cmd}")

    print(f"\n{'=' * 60}\n")
    return True


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("COMMAND SECURITY CHECKER - TEST SUITE")
    print("=" * 60 + "\n")

    results = []

    results.append(("Configuration", test_security_checker_config()))
    results.append(("Dangerous Commands", test_dangerous_commands()))
    results.append(("Safe Commands", test_safe_commands()))
    results.append(("Confirmation Required", test_confirmation_required()))
    results.append(("Pattern Matching", test_pattern_matching()))

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)

    for test_name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {test_name}")

    all_passed = all(result[1] for result in results)

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED!")
    else:
        print("SOME TESTS FAILED!")
    print("=" * 60 + "\n")

    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
