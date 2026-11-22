#!/usr/bin/env bash
# Goharu 一键卸载/清除（Linux / macOS）
#
#   ./uninstall.sh            # 停服务 + 删 venv/node_modules/dist（保留代码与数据）
#   ./uninstall.sh --all      # 追加删除 runtime_memory/（记忆、日志、缓存）与 models/
#   ./uninstall.sh --purge    # --all + 删除 .env（密钥）——彻底清零
set -uo pipefail
cd "$(dirname "$0")"

DEL_DATA=0
DEL_ENV=0
for arg in "$@"; do
  case "$arg" in
    --all) DEL_DATA=1 ;;
    --purge) DEL_DATA=1; DEL_ENV=1 ;;
    -h|--help) grep '^#' "$0" | head -6; exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

info() { echo -e "\033[1;32m[+]\033[0m $*"; }

# 1. 停 systemd 服务
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user list-unit-files 2>/dev/null | grep -q goharu; then
    systemctl --user stop goharu 2>/dev/null
    systemctl --user disable goharu 2>/dev/null
    rm -f ~/.config/systemd/user/goharu.service
    systemctl --user daemon-reload
    info "systemd 服务已移除"
  fi
fi

# 2. 杀掉残留进程
pkill -f "Gateway/gateway_entry.py" 2>/dev/null && info "gateway 进程已终止" || true

# 3. 依赖产物
rm -rf venv tui-ts/node_modules tui-ts/dist
# tsc 原地编译产物
find tui-ts/src tui-ts/test -name '*.js' -o -name '*.js.map' -o -name '*.d.ts' -o -name '*.d.ts.map' 2>/dev/null | xargs rm -f 2>/dev/null || true
info "venv / node_modules / dist 已清除"

# 4. 数据（可选）
if [ "$DEL_DATA" = "1" ]; then
  rm -rf runtime_memory models
  info "runtime_memory / models 已删除（记忆数据不可恢复）"
fi
if [ "$DEL_ENV" = "1" ]; then
  rm -f .env
  info ".env 已删除"
fi

info "卸载完成。仓库代码保留在当前目录，如需彻底删除请手动 rm -rf。"
