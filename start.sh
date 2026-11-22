#!/usr/bin/env bash
# Goharu 启动脚本：venv 激活 + 环境加载 + TUI
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
export PYTHON="$PWD/venv/bin/python"
exec "$PYTHON" run_tui_ts.py "$@"
