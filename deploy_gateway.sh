#!/bin/bash
# Gateway 部署脚本 - 在服务器上运行

set -e

echo "=========================================="
echo "TableHelper Gateway 部署脚本"
echo "=========================================="
echo ""

# 1. 检查 Python 环境
echo "[1/6] 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi
python3 --version

# 2. 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "[2/6] 创建虚拟环境..."
    python3 -m venv venv
else
    echo "[2/6] 虚拟环境已存在"
fi

# 3. 激活虚拟环境并安装依赖
echo "[3/6] 安装依赖..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements_gateway.txt

# 4. 创建必要的目录
echo "[4/6] 创建运行时目录..."
mkdir -p runtime_memory/gateway
mkdir -p runtime_memory/daily
mkdir -p runtime_memory/topic
mkdir -p logs

# 5. 配置环境变量
echo "[5/6] 配置环境变量..."
if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    cat > .env << 'EOF'
# QQ Bot 配置
QQ_APP_ID=102839705
QQ_BOT_TOKEN=102839705.wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5

# MiniMax API 配置
ANTHROPIC_API_KEY=your_minimax_api_key_here

# 日志级别
LOG_LEVEL=INFO
EOF
    echo "请编辑 .env 文件，填入正确的 API Key"
    echo "  nano .env"
else
    echo ".env 文件已存在"
fi

# 6. 测试配置
echo "[6/6] 测试配置..."
python3 -c "
import yaml
with open('config_gateway.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
print('配置文件加载成功')
print(f'QQ Bot 已启用: {config[\"gateway\"][\"platforms\"][\"qq\"][\"enabled\"]}')
"

echo ""
echo "=========================================="
echo "部署完成！"
echo "=========================================="
echo ""
echo "下一步操作："
echo "1. 编辑 .env 文件，填入 MiniMax API Key:"
echo "   nano .env"
echo ""
echo "2. 启动 Gateway:"
echo "   ./start_gateway_server.sh"
echo ""
echo "3. 查看日志:"
echo "   tail -f logs/gateway.log"
echo ""
