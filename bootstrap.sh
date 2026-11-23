#!/usr/bin/env bash
# Goharu 一键引导（Linux / macOS）
#
# 一行安装（粘贴到终端执行，自动 clone + 进入交互式向导）：
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.sh)"
#
# 说明：用 bash -c "$(curl ...)"（命令替换）而非 curl|bash，是为了让向导
# 能读到终端 stdin，从而支持交互式输入密钥/选择。
set -euo pipefail

REPO_URL="${GOHARU_REPO:-https://github.com/AoYuharu/Goharu.git}"
TARGET_DIR="${GOHARU_DIR:-$HOME/Goharu}"

info() { echo -e "\033[1;32m[+]\033[0m $*"; }
err()  { echo -e "\033[1;31m[x]\033[0m $*" >&2; }

# ── 前置依赖检查 ──
missing=0
if ! command -v git >/dev/null 2>&1; then err "需要 git"; missing=1; fi
PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$p" >/dev/null 2>&1; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then err "需要 Python >= 3.10"; missing=1;
elif ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  err "Python 版本太旧，需要 >= 3.10"; missing=1
fi
if ! command -v node >/dev/null 2>&1; then
  err "需要 Node.js >= 20（TUI 构建）"; missing=1
else
  nver="$(node --version 2>/dev/null | grep -oE '[0-9]+' | head -1)"
  if [ "${nver:-0}" -lt 20 ]; then err "Node.js 太旧（$(node --version)），需要 >= 20"; missing=1; fi
fi

if [ "$missing" = "1" ]; then
  echo "" >&2
  echo "请先安装缺失依赖：" >&2
  echo "  Debian/Ubuntu : sudo apt install git python3 python3-venv nodejs npm" >&2
  echo "  macOS         : xcode-select --install && brew install python node" >&2
  exit 1
fi

# ── 已在仓库内：直接进入向导 ──
if [ -f "$(pwd)/install.py" ] && [ -f "$(pwd)/run_tui_ts.py" ]; then
  info "已在 Goharu 仓库内，直接进入安装向导"
  exec "$PY" install.py
fi

# ── 克隆/更新到目标目录 ──
if [ -d "$TARGET_DIR/.git" ]; then
  info "已存在 $TARGET_DIR，拉取最新代码"
  git -C "$TARGET_DIR" pull --ff-only || true
else
  info "克隆仓库到 $TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
info "进入安装向导…"
exec "$PY" install.py
