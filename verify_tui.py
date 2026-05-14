#!/usr/bin/env python3
"""
TUI 启动验证脚本
检查 TUI 是否可以正常启动
"""

import sys
from pathlib import Path

def check_dependencies():
    """检查依赖"""
    print("检查依赖...")

    try:
        import textual
        print(f"  [OK] textual {textual.__version__}")
    except ImportError:
        print("  [FAIL] textual not installed")
        print("    Run: pip install textual")
        return False

    return True


def check_imports():
    """检查模块导入"""
    print("\n检查模块导入...")

    try:
        from TUI.app import TableHelperTUI
        print("  [OK] TUI.app")
    except Exception as e:
        print(f"  [FAIL] TUI.app: {e}")
        return False

    try:
        from TUI.gateway_client import GatewayClient
        print("  [OK] TUI.gateway_client")
    except Exception as e:
        print(f"  [FAIL] TUI.gateway_client: {e}")
        return False

    try:
        from TUI.gateway_entry import main
        print("  [OK] TUI.gateway_entry")
    except Exception as e:
        print(f"  [FAIL] TUI.gateway_entry: {e}")
        return False

    try:
        from TUI.widgets import ChatPanel, StatusBar, ToolPanel
        print("  [OK] TUI.widgets")
    except Exception as e:
        print(f"  [FAIL] TUI.widgets: {e}")
        return False

    return True


def check_files():
    """检查必要文件"""
    print("\n检查必要文件...")

    files = [
        "TUI/__init__.py",
        "TUI/app.py",
        "TUI/entry.py",
        "TUI/gateway_client.py",
        "TUI/gateway_entry.py",
        "TUI/widgets/__init__.py",
        "TUI/widgets/chat_panel.py",
        "TUI/widgets/status_bar.py",
        "TUI/widgets/tool_panel.py",
        "run_tui.py",
    ]

    all_exist = True
    for file in files:
        path = Path(file)
        if path.exists():
            print(f"  [OK] {file}")
        else:
            print(f"  [FAIL] {file} not found")
            all_exist = False

    return all_exist


def check_instantiation():
    """检查 TUI 实例化"""
    print("\n检查 TUI 实例化...")

    try:
        from TUI.app import TableHelperTUI
        app = TableHelperTUI()
        print("  [OK] TUI app instantiated")
        print(f"  [OK] Gateway client: {type(app.gateway).__name__}")
        print(f"  [OK] Session ID: {app.session_id}")
        return True
    except Exception as e:
        print(f"  [FAIL] Instantiation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("="*60)
    print("TableHelper TUI 启动验证")
    print("="*60)

    checks = [
        ("依赖检查", check_dependencies),
        ("文件检查", check_files),
        ("导入检查", check_imports),
        ("实例化检查", check_instantiation),
    ]

    results = []
    for name, check_func in checks:
        result = check_func()
        results.append((name, result))

    print("\n" + "="*60)
    print("验证结果:")
    print("="*60)

    all_passed = True
    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} - {name}")
        if not result:
            all_passed = False

    print("="*60)

    if all_passed:
        print("\n[SUCCESS] All checks passed! TUI is ready.")
        print("\nTo start TUI:")
        print("  python run_tui.py")
        print("\nKeyboard shortcuts:")
        print("  Ctrl+C - Quit")
        print("  Ctrl+L - Clear chat")
        return 0
    else:
        print("\n[FAILED] Some checks failed. Please fix before starting.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
