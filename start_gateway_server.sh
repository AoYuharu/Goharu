#!/bin/bash
# Gateway 启动脚本 - 在服务器上运行

set -e

echo "启动 TableHelper Gateway..."

# 激活虚拟环境
source venv/bin/activate

# 加载环境变量
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# 使用 config_gateway.yaml 配置文件
export CONFIG_FILE=config_gateway.yaml

# 启动 Gateway（后台运行，输出到日志，无缓冲）
nohup python3 -u Gateway/run_gateway.py > logs/gateway.log 2>&1 &

PID=$!
echo "Gateway 已启动，PID: $PID"
echo "PID 已保存到 logs/gateway.pid"
echo $PID > logs/gateway.pid

echo ""
echo "查看日志: tail -f logs/gateway.log"
echo "停止服务: kill \$(cat logs/gateway.pid)"
