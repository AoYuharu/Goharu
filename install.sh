#!/usr/bin/env bash
# Goharu 一键安装（Linux / macOS）
#
#   ./install.sh                  # 核心安装（纯文本检索，轻量）
#   ./install.sh --with-embedding # 额外安装 torch+embedding（>2GB，语义检索增强）
#   ./install.sh --systemd        # 安装后注册 systemd user 服务（headless 常驻）
#
# 幂等：可重复执行，已完成的步骤自动跳过。
set -euo pipefail
cd "$(dirname "$0")"

WITH_EMBEDDING=0
WITH_SYSTEMD=0
for arg in "$@"; do
  case "$arg" in
    --with-embedding) WITH_EMBEDDING=1 ;;
    --systemd) WITH_SYSTEMD=1 ;;
    -h|--help) grep '^#' "$0" | head -8; exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

# 清华镜像（国内直连 PyPI 慢/超时）
PIP_MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"

info() { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
die()  { echo -e "\033[1;31m[x]\033[0m $*" >&2; exit 1; }

# ── 1. 依赖检查 ──
info "检查系统依赖…"
PYTHON_BIN=""
for p in python3.12 python3.11 python3.10 python3; do
  if command -v "$p" >/dev/null 2>&1; then PYTHON_BIN="$p"; break; fi
done
[ -n "$PYTHON_BIN" ] || die "需要 Python >= 3.10。Debian/Ubuntu: sudo apt install python3 python3-venv；macOS: brew install python@3.12"
PY_VER=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python $PY_VER 太旧，需要 >= 3.10"
info "Python $PY_VER ($PYTHON_BIN)"

command -v git >/dev/null 2>&1 || die "需要 git"
command -v node >/dev/null 2>&1 || die "需要 Node.js >= 18（TUI 构建）。https://nodejs.org 或 nvm 安装"
command -v npm  >/dev/null 2>&1 || die "需要 npm"
info "Node $(node --version)"

# ── 2. Python venv ──
if [ ! -d venv ]; then
  info "创建 venv…"
  "$PYTHON_BIN" -m venv venv || die "venv 创建失败。Debian/Ubuntu 需: sudo apt install python3-venv"
fi
VENV_PY="venv/bin/python"
[ -x "$VENV_PY" ] || die "venv 异常：$VENV_PY 不存在"

info "安装 Python 依赖（镜像: $PIP_MIRROR）…"
"$VENV_PY" -m pip install --upgrade pip -q -i "$PIP_MIRROR"
"$VENV_PY" -m pip install -r requirements.txt -q -i "$PIP_MIRROR"

if [ "$WITH_EMBEDDING" = "1" ]; then
  info "安装 embedding 依赖（torch 等，体积大）…"
  "$VENV_PY" -m pip install -r requirements-embedding.txt -i "$PIP_MIRROR"
else
  info "跳过 embedding 重依赖（向量检索将降级为 FTS5+BM25，需要时加 --with-embedding 重装）"
fi

# ── 3. TUI 构建 ──
if [ -f tui-ts/package.json ]; then
  info "构建 TUI（npm install + tsc）…"
  (cd tui-ts && npm install --silent && npm run build --silent)
fi

# ── 4. .env ──
if [ ! -f .env ]; then
  cp .env.example .env
  warn "已生成 .env 模板——请编辑填入 ANTHROPIC_API_KEY 等密钥后再运行"
fi

# ── 5. systemd user 服务（可选，headless 常驻）──
if [ "$WITH_SYSTEMD" = "1" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p ~/.config/systemd/user
    cat > ~/.config/systemd/user/goharu.service <<EOF
[Unit]
Description=Goharu Agent Gateway (headless daemon)
After=network.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=/bin/bash -c 'tail -f /dev/null | venv/bin/python Gateway/gateway_entry.py'
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable goharu
    info "systemd user 服务已注册：systemctl --user start goharu"
    warn "如需开机自启（无登录会话）：sudo loginctl enable-linger $USER"
  else
    warn "无 systemctl，跳过 systemd 注册"
  fi
fi

echo ""
info "安装完成。启动方式："
echo "    交互 TUI : ./start.sh            （或 venv/bin/python run_tui_ts.py）"
echo "    无头守护 : systemctl --user start goharu   （需 --systemd 安装）"
