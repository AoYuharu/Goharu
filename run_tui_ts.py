#!/usr/bin/env python3
"""Launch the TypeScript TUI after ensuring an independent gateway daemon."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_ENTRY = PROJECT_ROOT / "tui-ts" / "dist" / "main.js"
NODE_MIN = 22


def _find_node() -> str | None:
    candidates = [os.environ.get("GOHARU_NODE")]
    exe = "node.exe" if os.name == "nt" else "node"
    candidates.extend(str(Path(p) / exe) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    home = Path.home()
    if os.name == "nt":
        candidates.extend(str(p) for p in home.glob("node*/node.exe"))
        candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs", "node.exe"))
    else:
        candidates.extend(str(p) for p in home.glob("node*/bin/node"))
        candidates.extend(str(p) for p in home.glob(".nvm/versions/node/*/bin/node"))
        candidates.extend(("/usr/local/bin/node", "/usr/bin/node"))
        candidates.extend(str(p) for p in Path("/opt").glob("node*/bin/node"))

    seen = set()
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            proc = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.match(r"v?(\d+)", proc.stdout.strip())
        if proc.returncode == 0 and match and int(match.group(1)) >= NODE_MIN:
            return os.path.abspath(candidate)
    return None


def _ensure_gateway() -> bool:
    from goharu_cli import _alive, gateway_start

    if _alive():
        return True
    print("[!] 网关未运行，先拉起独立守护…")
    if gateway_start():
        return True
    print("[x] 网关未就绪，TUI 不会启动；请先处理网关错误。", file=sys.stderr)
    return False


def main():
    os.chdir(PROJECT_ROOT)
    # 让 TUI spawn 的 proxy 沿用当前 venv Python；显式 PYTHON 配置优先。
    os.environ.setdefault("PYTHON", sys.executable)

    node = _find_node()
    if node is None:
        print(
            f"Error: Node.js >= {NODE_MIN} is required. Run bootstrap to install it, "
            "or set GOHARU_NODE to its absolute path.",
            file=sys.stderr,
        )
        sys.exit(1)
    os.environ["GOHARU_NODE"] = node
    os.environ["PATH"] = os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")

    if not DIST_ENTRY.exists():
        npm_name = "npm.cmd" if os.name == "nt" else "npm"
        bundled_npm = os.path.join(os.path.dirname(node), npm_name)
        npm = bundled_npm if os.path.isfile(bundled_npm) else shutil.which(npm_name)
        if npm is None:
            print("Error: tui-ts not built and npm not found.", file=sys.stderr)
            sys.exit(1)
        print("tui-ts not built, building...")
        # Windows 下 npm 是 .cmd 批处理必须走 shell；POSIX 下 shell=True 会丢弃
        # list 参数（"run"/"build" 变成 shell 的 $0/$1），必须直接 exec。
        subprocess.check_call(
            [npm, "run", "build"],
            cwd=PROJECT_ROOT / "tui-ts",
            shell=(os.name == "nt"),
        )

    if not _ensure_gateway():
        sys.exit(1)

    args = [str(DIST_ENTRY), *sys.argv[1:]]
    try:
        proc = subprocess.run([node, *args], cwd=PROJECT_ROOT)
        sys.exit(proc.returncode or 0)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
