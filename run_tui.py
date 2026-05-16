#!/usr/bin/env python3
"""
Quick launcher for TableHelper TUI
"""

import sys
import os
import traceback
import atexit
from datetime import datetime
from pathlib import Path

# Set up environment
project_root = Path(__file__).parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

# ── TUI 日志系统 ─────────────────────────────────────
_TUI_LOG_DIR = project_root / "runtime_memory" / "logs" / "tui"
_TUI_LOG_DIR.mkdir(parents=True, exist_ok=True)

# TUI crash log (追加模式)
_TUI_CRASH_LOG = _TUI_LOG_DIR / "tui_crash.log"


def _log_tui_crash(exc_type, exc_value, exc_tb):
    """将未捕获异常写入 TUI crash 日志。"""
    try:
        ts = datetime.now().isoformat()
        lines = [f"\n=== TUI crash · {ts} ==="]
        lines.append(f"Exception: {exc_type.__name__}: {exc_value}")
        if exc_tb:
            lines.extend(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(_TUI_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _log_tui_memory():
    """在 crash 时记录内存使用。"""
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        ts = datetime.now().isoformat()
        with open(_TUI_LOG_DIR / "tui_memory.log", "a", encoding="utf-8") as f:
            f.write(
                f"{ts} RSS={mem.rss / 1024 / 1024:.1f}MB "
                f"VMS={mem.vms / 1024 / 1024:.1f}MB "
                f"threads={proc.num_threads()}\n"
            )
    except ImportError:
        pass


def _tui_shutdown_log():
    """退出时记录内存最终状态。"""
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        ts = datetime.now().isoformat()
        with open(_TUI_LOG_DIR / "tui_memory.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} SHUTDOWN RSS={mem.rss / 1024 / 1024:.1f}MB\n")
    except ImportError:
        pass


# 安装 crash handler（异常退出时写日志）
sys.excepthook = lambda exc_type, exc_value, exc_tb: (
    _log_tui_crash(exc_type, exc_value, exc_tb),
    _log_tui_memory(),
    sys.__excepthook__(exc_type, exc_value, exc_tb),
)

atexit.register(_tui_shutdown_log)

# Import and run
from TUI.app import run_tui

if __name__ == "__main__":
    try:
        run_tui()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
