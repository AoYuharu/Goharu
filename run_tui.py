#!/usr/bin/env python3
"""
Quick launcher for TableHelper TUI
"""

import sys
import os
from pathlib import Path

# Set up environment
project_root = Path(__file__).parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

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
        import traceback
        traceback.print_exc()
        sys.exit(1)
