#!/usr/bin/env bash
# 在容器内跑一遍「全新用户」的真实安装 + 卸载流程并断言结果。
# 用法：由 Dockerfile 作为 CMD 调用（仓库根在 build 时 COPY 到 /src）。
set -uo pipefail

PASS=0; FAIL=0
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
check(){ if [ "$1" ]; then ok "$2"; else bad "$2"; fi; }

GH_DIR="$HOME/Goharu"

echo "== [1] 克隆 GitHub master（真实用户拿到的代码）=="
git clone --depth 1 https://github.com/AoYuharu/Goharu.git "$GH_DIR" >/dev/null 2>&1
check "$([ -f "$GH_DIR/install.py" ] && echo 1 || echo '')" "clone 成功且含 install.py"
check "$([ -f "$GH_DIR/run_tui_ts.py" ] && echo 1 || echo '')" "clone 含 run_tui_ts.py"

echo "== [2] 跑交互式安装向导（脚本化输入：DeepSeek / 不填key / 轻量 / TUI / 不立即启动）=="
cd "$GH_DIR"
# 引导输入序列：厂商=1(DeepSeek) · 密钥=空 · QQ=空(跳过) · HaruNet=空(跳过) · 代理=空(跳过) · 检索=1(轻量) · 运行方式=1(TUI) · 立即启动=空(否)
printf '1\n\n\n\n\n1\n1\n\n' | python3 install.py > /tmp/install.log 2>&1
INSTALL_RC=$?
tail -n 6 /tmp/install.log
check "$([ "$INSTALL_RC" = "0" ] && echo 1 || echo '')" "install.py 退出码 0"

echo "== [3] 断言安装产物 =="
check "$([ -x "$GH_DIR/venv/bin/python" ] && echo 1 || echo '')" "venv 已建（venv/bin/python 可执行）"
check "$("$GH_DIR/venv/bin/python" -c 'import yaml,anthropic,openai,mcp' 2>/dev/null && echo 1 || echo '')" "venv 里 pyyaml/anthropic/openai/mcp 可导入"
check "$([ -f "$GH_DIR/.env" ] && echo 1 || echo '')" ".env 已生成"
check "$([ -f "$GH_DIR/tui-ts/dist/App.js" ] || ls "$GH_DIR"/tui-ts/src/*.js >/dev/null 2>&1 && echo 1 || echo '')" "TUI 已构建（产物存在）"
check "$([ -x "$HOME/.local/bin/goharu" ] && echo 1 || echo '')" "全局命令 goharu 已注册（~/.local/bin/goharu）"
grep -q "model:" "$GH_DIR/config.yaml" && ok "config.yaml 含模型配置" || bad "config.yaml 缺模型配置"

echo "== [4] goharu 命令可用性（仅探测帮助/版本，不真启动 TUI）==="
"$GH_DIR/venv/bin/python" -c "import ast,sys; ast.parse(open('$GH_DIR/run_tui_ts.py').read()); print('run_tui_ts.py 语法 OK')" && ok "run_tui_ts.py 语法有效" || bad "run_tui_ts.py 语法错误"

echo "== [5] 卸载（--all：连数据一起清）==="
bash "$GH_DIR/uninstall.sh" --all > /tmp/uninstall.log 2>&1
UN_RC=$?
tail -n 4 /tmp/uninstall.log
check "$([ "$UN_RC" = "0" ] && echo 1 || echo '')" "uninstall.sh 退出码 0"
check "$([ ! -d "$GH_DIR/venv" ] && echo 1 || echo '')" "venv 已删除"
check "$([ ! -d "$GH_DIR/tui-ts/node_modules" ] && echo 1 || echo '')" "node_modules 已删除"
check "$([ ! -d "$GH_DIR/runtime_memory" ] && echo 1 || echo '')" "runtime_memory 已删除（--all）"

echo ""
echo "============================================================"
echo "结果: $PASS 通过, $FAIL 失败"
[ "$FAIL" = "0" ] && echo "VERDICT: PASS" || echo "VERDICT: FAIL"
exit "$FAIL"
