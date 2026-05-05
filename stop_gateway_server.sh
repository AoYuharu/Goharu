#!/bin/bash
# Gateway 停止脚本

if [ -f "logs/gateway.pid" ]; then
    PID=$(cat logs/gateway.pid)
    echo "停止 Gateway (PID: $PID)..."
    kill $PID
    rm logs/gateway.pid
    echo "Gateway 已停止"
else
    echo "未找到 PID 文件，Gateway 可能未运行"
fi
