#!/usr/bin/env bash
# Goharu 一键卸载（Linux / macOS）——薄封装，逻辑在 goharu_cli.py uninstall（跨平台同一份）
#
#   ./uninstall.sh                 停服务 + 杀 gateway/TUI（含幽灵守护）+ 删依赖产物/数据/密钥/全局命令
#   ./uninstall.sh --keep-data     保留 runtime_memory/ 与 models/
#   ./uninstall.sh --stop-only     只停进程/服务，不删任何文件
#   ./uninstall.sh --dry-run       只列出将杀/将删的内容
#   ./uninstall.sh --yes           跳过确认（非交互环境必须）
#
# 等价命令：goharu uninstall [选项]（安装后任意目录可用）
set -euo pipefail
cd "$(dirname "$0")"

PY=""
if [ -x venv/bin/python ]; then PY=venv/bin/python
elif command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "[x] 需要 Python >= 3.10 才能运行卸载逻辑（或手动删除本目录）" >&2
  exit 1
fi
exec "$PY" goharu_cli.py uninstall "$@"
