#!/bin/bash
# Gateway 重启脚本

GATEWAY_DIR="/root/TableHelper"
CONFIG_FILE="config_gateway.yaml"
LOG_FILE="$GATEWAY_DIR/logs/gateway.log"
PID_FILE="$GATEWAY_DIR/logs/gateway.pid"
API_KEY="sk-cp-l85evWXvhzS4grE7IWtSICMEWYUyW8FS35JUrCG6X0j4yRNOHMosPZriizXyOu2qSzXdogTjz5sQaJWesIKxkA5hQ_XqLfkOSqQkr9KZYD90lzCXJ58FV64"

cd "$GATEWAY_DIR" || exit 1

# 1. 杀死旧进程
echo "[Restart] Killing old Gateway processes..."
pkill -f "Gateway/run_gateway" 2>/dev/null
sleep 2

# 2. 检查是否还有残留进程
REMAINING=$(ps aux | grep "Gateway/run_gateway" | grep -v grep | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "[Restart] Warning: $REMAINING processes still running, force killing..."
    pkill -9 -f "Gateway/run_gateway" 2>/dev/null
    sleep 1
fi

# 3. 记录重启日志
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Gateway restarted" >> "$LOG_FILE"

# 4. 启动新进程
echo "[Restart] Starting Gateway..."
source venv/bin/activate
export CONFIG_FILE="$CONFIG_FILE"
export ANTHROPIC_API_KEY="$API_KEY"
nohup python3 -u Gateway/run_gateway.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!

echo "$NEW_PID" > "$PID_FILE"
echo "[Restart] Gateway started with PID: $NEW_PID"

# 5. 等待启动完成并检查状态
sleep 8
if ps -p $NEW_PID > /dev/null; then
    echo "[Restart] Gateway is running"
    tail -20 "$LOG_FILE"
else
    echo "[Restart] ERROR: Gateway failed to start"
    tail -50 "$LOG_FILE"
    exit 1
fi