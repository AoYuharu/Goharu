#!/usr/bin/env python3
"""
Launcher for the TypeScript TUI (tui-ts/).

Runs `node tui-ts/dist/main.js` with inherited stdio (interactive).
Build first if needed: `cd tui-ts && npm run build`.
Fallback Python TUI: `python run_tui.py`.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_ENTRY = PROJECT_ROOT / "tui-ts" / "dist" / "main.js"


def main():
    os.chdir(PROJECT_ROOT)

    if shutil.which("node") is None:
        print("Error: node not found in PATH. Install Node.js >= 22.", file=sys.stderr)
        sys.exit(1)

    if not DIST_ENTRY.exists():
        npm = shutil.which("npm")
        if npm is None:
            print("Error: tui-ts not built and npm not found.", file=sys.stderr)
            sys.exit(1)
        print("tui-ts not built, building...")
        subprocess.check_call([npm, "run", "build"], cwd=PROJECT_ROOT / "tui-ts", shell=True)

    args = [str(DIST_ENTRY), *sys.argv[1:]]
    try:
        proc = subprocess.run(["node", *args], cwd=PROJECT_ROOT)
        sys.exit(proc.returncode or 0)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
