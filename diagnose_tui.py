#!/usr/bin/env python3
"""
TUI 诊断脚本 - 检查布局和输入问题
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from textual.app import App
from TUI.app import TableHelperTUI


def diagnose_tui():
    """诊断 TUI 结构"""
    print("="*60)
    print("TUI 诊断")
    print("="*60)

    try:
        # 创建应用实例
        app = TableHelperTUI()
        print("[OK] TUI 应用创建成功")

        # 检查 CSS
        print("\n[INFO] CSS 定义:")
        print(f"  CSS 长度: {len(app.CSS)} 字符")

        # 检查绑定
        print("\n[INFO] 快捷键绑定:")
        for binding in app.BINDINGS:
            print(f"  {binding.key} -> {binding.action}")

        # 检查组件
        print("\n[INFO] 组件检查:")
        print(f"  Gateway: {type(app.gateway).__name__}")
        print(f"  Session ID: {app.session_id}")

        print("\n[SUCCESS] TUI 结构正常")
        print("\n提示:")
        print("  1. 启动后，输入框应该在左下角")
        print("  2. 如果看不到输入框，尝试调整终端窗口大小")
        print("  3. 按 Tab 键可以在组件间切换焦点")
        print("  4. 确保终端至少 80x24 大小")

        return True

    except Exception as e:
        print(f"\n[ERROR] 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = diagnose_tui()

    if success:
        print("\n" + "="*60)
        print("建议:")
        print("="*60)
        print("1. 运行: python run_tui.py")
        print("2. 等待 Gateway 启动（状态栏显示 'Gateway ready'）")
        print("3. 点击左下角输入框或按 Tab 键切换到输入框")
        print("4. 输入消息后按 Enter 发送")
        print("\n如果仍然无法输入，请尝试:")
        print("  - 调整终端窗口大小")
        print("  - 使用不同的终端（如 Windows Terminal）")
        print("  - 检查终端是否支持鼠标输入")

    sys.exit(0 if success else 1)
