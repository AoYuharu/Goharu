#!/bin/bash

# QQ Bot Gateway 启动脚本

echo "=========================================="
echo "QQ Bot Gateway Startup Script"
echo "=========================================="
echo ""

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "[ERROR] .env file not found!"
    echo "Please create .env file with:"
    echo "  ANTHROPIC_API_KEY=your_minimax_api_key"
    exit 1
fi

# 检查 API key
if grep -q "your_minimax_api_key_here" .env; then
    echo "[WARNING] API key not configured in .env"
    echo "Please edit .env and set your MiniMax API key"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 加载环境变量
export $(cat .env | xargs)

# 停止旧进程
echo "[1/3] Stopping old processes..."
pkill -f "test_qq_echo_standalone.py" 2>/dev/null
pkill -f "run_gateway.py" 2>/dev/null
sleep 2

# 创建运行时目录
echo "[2/3] Creating runtime directories..."
mkdir -p runtime_memory/gateway
mkdir -p runtime_memory/daily
mkdir -p runtime_memory/topic

# 启动 Gateway
echo "[3/3] Starting Gateway..."
echo ""
echo "=========================================="
echo "Gateway is starting..."
echo "Check logs below for status"
echo "Press Ctrl+C to stop"
echo "=========================================="
echo ""

python3 run_gateway.py
