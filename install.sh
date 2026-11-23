#!/usr/bin/env bash
# Goharu 安装入口（Linux / macOS）—— 转入交互式安装向导 install.py
# 直接运行本脚本即进入分步引导页（选厂商/贴密钥/选模式/装依赖/构建/写配置）。
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$p" >/dev/null 2>&1; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "[x] 需要 Python >= 3.10。Debian/Ubuntu: sudo apt install python3 python3-venv；macOS: brew install python@3.12" >&2
  exit 1
fi

exec "$PY" install.py "$@"
