#!/usr/bin/env bash
# Goharu 一键引导（Linux / macOS）
#
# 一行安装（粘贴到终端执行，自动 clone + 进入交互式向导）：
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.sh)"
#
# 环境变量：
#   GOHARU_DIR       安装目录（默认 ~/Goharu）
#   GOHARU_REPO      仓库地址（默认 github.com/AoYuharu/Goharu.git）
#   GOHARU_NODE_DIR  Node 自动安装目录（默认 ~/node22，测试时可覆盖）
#
# 组件探测原则：遍历 PATH 全部候选与常见安装位置，合格即用并注入 PATH；
# 只有所有候选都不合格时才自动安装（Node 官方二进制，npmmirror→官方回退，
# 无需 sudo）；自动安装也失败才报错。绝不因 PATH 里第一个是旧版就打断。
#
# 说明：用 bash -c "$(curl ...)"（命令替换）而非 curl|bash，是为了让向导
# 能读到终端 stdin，从而支持交互式输入密钥/选择。
set -euo pipefail

REPO_URL="${GOHARU_REPO:-https://github.com/AoYuharu/Goharu.git}"
TARGET_DIR="${GOHARU_DIR:-$HOME/Goharu}"
NODE_MIN=22
NODE_VERSION="v22.20.0"
NODE_HOME="${GOHARU_NODE_DIR:-$HOME/node22}"

info() { echo -e "\033[1;32m[+]\033[0m $*"; }
err()  { echo -e "\033[1;31m[x]\033[0m $*" >&2; }

# PATH 中某命令的全部实例（而非只看第一个）
_all_candidates() {
  which -a "$1" 2>/dev/null || command -v "$1" 2>/dev/null || true
}

node_ok() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  local major
  major="$("$1" --version 2>/dev/null | grep -oE '[0-9]+' | head -1)"
  [ "${major:-0}" -ge "$NODE_MIN" ]
}

find_node() {
  local cand
  while IFS= read -r cand; do
    if node_ok "$cand"; then echo "$cand"; return 0; fi
  done < <(_all_candidates node)
  for cand in "$NODE_HOME/bin/node" "$HOME"/node*/bin/node \
              /usr/local/bin/node /opt/node*/bin/node \
              "$HOME"/.nvm/versions/node/*/bin/node; do
    if node_ok "$cand"; then echo "$cand"; return 0; fi
  done
  return 1
}

install_node() {
  local os arch pkg base tmp
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    linux|darwin) ;;
    *) err "不支持在 $os 上自动安装 Node"; return 1;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64;;
    aarch64|arm64) arch=arm64;;
    armv7l) arch=armv7l;;
    *) err "不支持的架构 $(uname -m)，请手动安装 Node >= $NODE_MIN"; return 1;;
  esac
  # 统一 tar.gz，避免依赖 xz-utils
  pkg="node-${NODE_VERSION}-${os}-${arch}.tar.gz"
  tmp="$(mktemp -d)"
  for base in "https://registry.npmmirror.com/-/binary/node/${NODE_VERSION}" \
              "https://nodejs.org/dist/${NODE_VERSION}"; do
    info "下载 Node ${NODE_VERSION}（${base}）"
    if curl -fsSL --connect-timeout 10 --max-time 300 "${base}/${pkg}" -o "${tmp}/${pkg}"; then
      if ! tar -xzf "${tmp}/${pkg}" -C "$tmp"; then
        err "解压失败"; rm -rf "$tmp"; return 1
      fi
      if "${tmp}/node-${NODE_VERSION}-${os}-${arch}/bin/node" --version >/dev/null 2>&1; then
        rm -rf "$NODE_HOME"
        mv "${tmp}/node-${NODE_VERSION}-${os}-${arch}" "$NODE_HOME"
        rm -rf "$tmp"
        info "Node ${NODE_VERSION} 已安装到 $NODE_HOME"
        return 0
      fi
    fi
    info "该源失败，换下一个"
  done
  rm -rf "$tmp"
  err "Node 自动安装失败"
  return 1
}

# ── 前置依赖检查（全候选探测，合格即用）──
missing=0
if ! command -v git >/dev/null 2>&1; then err "需要 git"; missing=1; fi

PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
  while IFS= read -r cand; do
    [ -n "$cand" ] || continue
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$cand"; break 2
    fi
  done < <(_all_candidates "$p")
done
if [ -z "$PY" ]; then err "需要 Python >= 3.10"; missing=1; fi

NODE_BIN="$(find_node || true)"
if [ -z "$NODE_BIN" ]; then
  info "未找到 Node.js >= ${NODE_MIN}，自动安装到 $NODE_HOME"
  if install_node; then NODE_BIN="$NODE_HOME/bin/node"; fi
fi
if [ -n "$NODE_BIN" ]; then
  export PATH="$(dirname "$NODE_BIN"):$PATH"
  info "Node：$NODE_BIN（$("$NODE_BIN" --version)）"
else
  err "需要 Node.js >= ${NODE_MIN}（自动安装失败）"
  missing=1
fi

if [ "$missing" = "1" ]; then
  echo "" >&2
  echo "请先安装缺失依赖：" >&2
  echo "  Debian/Ubuntu : sudo apt install git python3 python3-venv" >&2
  echo "  macOS         : xcode-select --install && brew install python" >&2
  exit 1
fi

# ── 已在仓库内：直接进入向导 ──
if [ -f "$(pwd)/install.py" ] && [ -f "$(pwd)/run_tui_ts.py" ]; then
  info "已在 Goharu 仓库内，直接进入安装向导"
  exec "$PY" install.py
fi

# ── 克隆/更新到目标目录 ──
# 只有"有效仓库"（.git 完整 + install.py 在）才 pull；残缺 .git（删除/克隆中断
# 残留）或空目录自动清理重建；目录含其他文件则报错不动用户数据。
is_repo=0
if [ -d "$TARGET_DIR/.git" ] && [ -f "$TARGET_DIR/install.py" ] \
   && [ "$(git -C "$TARGET_DIR" rev-parse --is-inside-work-tree 2>/dev/null)" = "true" ]; then
  is_repo=1
fi
if [ "$is_repo" = "1" ]; then
  info "已存在 $TARGET_DIR，拉取最新代码"
  git -C "$TARGET_DIR" pull --ff-only || true
elif [ -e "$TARGET_DIR" ]; then
  left="$(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 ! -name ".git" 2>/dev/null | head -1)"
  if [ -n "$left" ]; then
    err "$TARGET_DIR 已存在但不是有效的 Goharu 仓库（含其他文件）；请删除该目录或设置 GOHARU_DIR 指定新目录后重跑"
    exit 1
  fi
  info "发现残缺目录 $TARGET_DIR，清理后重新克隆"
  rm -rf "$TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR" || { err "克隆失败，请检查网络后重试"; exit 1; }
else
  info "克隆仓库到 $TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR" || { err "克隆失败，请检查网络后重试"; exit 1; }
fi
if [ ! -f "$TARGET_DIR/install.py" ]; then
  err "克隆后 $TARGET_DIR 中没有 install.py，请检查网络后重试"
  exit 1
fi

cd "$TARGET_DIR"
info "进入安装向导…"
exec "$PY" install.py
