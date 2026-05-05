#!/bin/bash

# QQ Bot 完整部署脚本
# 用于首次部署或重新部署整个系统

echo "=========================================="
echo "QQ Bot Complete Deployment Script"
echo "=========================================="
echo ""

# 1. 检查环境
echo "[1/6] Checking environment..."
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found!"
    exit 1
fi
echo "[OK] Python3 found: $(python3 --version)"

# 2. 安装依赖
echo ""
echo "[2/6] Installing dependencies..."
pip3 install -r requirements_server.txt -q
if [ $? -eq 0 ]; then
    echo "[OK] Dependencies installed"
else
    echo "[ERROR] Failed to install dependencies"
    exit 1
fi

# 3. 创建目录结构
echo ""
echo "[3/6] Creating directory structure..."
mkdir -p runtime_memory/gateway
mkdir -p runtime_memory/daily
mkdir -p runtime_memory/topic
mkdir -p logs
echo "[OK] Directories created"

# 4. 配置 API Key
echo ""
echo "[4/6] Configuring API key..."
if [ ! -f .env ]; then
    echo "ANTHROPIC_API_KEY=your_minimax_api_key_here" > .env
    echo "[WARNING] .env file created with placeholder"
    echo "[ACTION REQUIRED] Please edit .env and set your API key:"
    echo "  nano .env"
else
    echo "[OK] .env file already exists"
fi

# 5. 测试配置
echo ""
echo "[5/6] Testing configuration..."
python3 << 'PYEOF'
import sys
sys.path.insert(0, '.')
from configurationLoader import Configuration
config = Configuration('config_server.yaml')
print(f'[OK] Config loaded: {config.get("gateway.platforms.qq.enabled")}')
PYEOF

if [ $? -ne 0 ]; then
    echo "[ERROR] Configuration test failed"
    exit 1
fi

# 6. 显示下一步
echo ""
echo "[6/6] Deployment complete!"
echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "1. Set your API key:"
echo "   nano .env"
echo ""
echo "2. Start the Gateway:"
echo "   ./start_gateway.sh"
echo ""
echo "3. Or run in background:"
echo "   nohup ./start_gateway.sh > gateway.log 2>&1 &"
echo ""
echo "4. Check status:"
echo "   ps aux | grep run_gateway"
echo "   tail -f gateway.log"
echo ""
echo "=========================================="
