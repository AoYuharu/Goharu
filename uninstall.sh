#!/usr/bin/env bash
# Goharu 一键卸载（Linux / macOS）——**默认把端机清干净**
#
#   ./uninstall.sh              # 停服务 + 杀进程 + 删依赖产物/数据/密钥/全局命令
#   ./uninstall.sh --keep-data  # 保留 runtime_memory/ 与 models/（只清程序与产物）
#   ./uninstall.sh --purge      # 兼容旧用法，等同默认
#
# 说明：
#   - 仓库代码目录本身不删（如需彻底删除：rm -rf "$(pwd)"）
#   - 远端 HaruNet 的白名单登记在 hub 服务器侧，本脚本不处理
#   - 不依赖 pkill/ps（Debian 最小镜像没有 procps）：回退扫描 /proc 杀进程
set -uo pipefail
cd "$(dirname "$0")"

KEEP_DATA=0
for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP_DATA=1 ;;
    --all|--purge) KEEP_DATA=0 ;;
    -h|--help) grep '^#' "$0" | head -10; exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

info() { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }

# ── 1. 停 systemd 服务 ──────────────────────────────────────────────
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user list-unit-files 2>/dev/null | grep -q goharu; then
    systemctl --user stop goharu 2>/dev/null
    systemctl --user disable goharu 2>/dev/null
    rm -f "$HOME/.config/systemd/user/goharu.service"
    systemctl --user daemon-reload 2>/dev/null || true
    info "systemd 服务已移除"
  fi
fi

# ── 2. 杀 gateway / TUI 进程（无 pkill/ps 也可用）────────────────────
collect_pids() {
  local p cmd
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$cmd" in
      *Gateway/gateway_entry.py*|*run_tui_ts.py*)
        echo "${p#/proc/}" ;;
    esac
  done
}

# 多轮收敛：TUI 在 gateway 退出瞬间会 re-spawn 子进程，单轮快照杀不干净
killed=0
for round in 1 2 3 4 5; do
  PIDS=$(collect_pids)
  [ -z "$PIDS" ] && break
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null || true
  for _ in $(seq 1 12); do
    alive=0
    for pid in $PIDS; do
      [ -d "/proc/$pid" ] && alive=1
    done
    [ "$alive" = "0" ] && break
    sleep 0.5
  done
  for pid in $PIDS; do
    [ -d "/proc/$pid" ] && kill -9 "$pid" 2>/dev/null || true
  done
  killed=$((killed + $(echo $PIDS | wc -w)))
  sleep 1   # 给 re-spawn 留出窗口，下一轮兜住
done
if [ "$killed" -gt 0 ]; then
  info "已终止 gateway/TUI 进程（共 $killed 个）"
else
  info "无运行中的 gateway/TUI 进程"
fi
# 最终复核
if [ -n "$(collect_pids)" ]; then
  warn "仍有进程未被杀掉（请手动检查 /proc）"
fi

# ── 3. 全局命令与 shell 配置片段（install.py 注册的）─────────────────
if [ -e "$HOME/.local/bin/goharu" ]; then
  rm -f "$HOME/.local/bin/goharu"
  info "已删除全局命令 ~/.local/bin/goharu"
fi

clean_rc() {
  local rc="$1" line skip=0 changed=0 out=""
  [ -f "$rc" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$skip" = "1" ]; then
      case "$line" in
        export\ PATH=*.local/bin*) skip=0; changed=1; continue ;;
      esac
      skip=0
    fi
    if [ "$line" = "# Goharu 全局命令" ]; then
      skip=1; changed=1; continue
    fi
    out+="$line"$'\n'
  done < "$rc"
  if [ "$changed" = "1" ]; then
    printf '%s' "$out" > "$rc"
    info "已清理 $rc 中的 Goharu PATH 片段"
  fi
}
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  clean_rc "$rc"
done

# ── 4. 依赖产物 ─────────────────────────────────────────────────────
rm -rf venv tui-ts/node_modules tui-ts/dist
find tui-ts/src tui-ts/test \
  \( -name '*.js' -o -name '*.js.map' -o -name '*.d.ts' -o -name '*.d.ts.map' \) \
  -delete 2>/dev/null || true
info "venv / node_modules / dist / tsc 产物已清除"

# ── 5. 数据与密钥（默认删除；--keep-data 保留）───────────────────────
if [ "$KEEP_DATA" = "1" ]; then
  warn "按 --keep-data 保留 runtime_memory/ 与 models/"
else
  rm -rf runtime_memory models
  rm -f .env
  info "runtime_memory / models / .env 已删除（记忆与密钥不可恢复）"
fi

info "卸载完成：端机已清理。仓库代码目录保留（如需删除：rm -rf \"$(pwd)\"）。"
