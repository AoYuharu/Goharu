#!/usr/bin/env python3
"""
TableHelper TUI Entry Point

Launch the Textual-based Terminal User Interface
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from TUI.app import run_tui


if __name__ == "__main__":
    run_tui()
