#!/bin/bash

echo "========================================="
echo "QQ Bot Gateway Status"
echo "========================================="
echo ""

# Check process
echo "[Process Status]"
if ps aux | grep -q '[r]un_gateway.py'; then
    echo "  Status: RUNNING"
    PID=$(ps aux | grep '[r]un_gateway.py' | awk '{print $2}')
    echo "  PID: $PID"
    
    # Get process info
    ps aux | grep '[r]un_gateway.py' | awk '{print "  CPU: " $3 "%, Memory: " $4 "%"}'
    
    # Uptime
    START_TIME=$(ps -p $PID -o lstart= 2>/dev/null)
    if [ -n "$START_TIME" ]; then
        echo "  Started: $START_TIME"
    fi
else
    echo "  Status: NOT RUNNING"
fi

echo ""
echo "[Configuration]"
echo "  Server: travelnote.online (159.75.26.204)"
echo "  Bot Name: NyaNya-测试中"
echo "  Bot ID: 14004041982952838788"

echo ""
echo "[API Key]"
if [ -f .env ]; then
    if grep -q "sk-cp-" .env; then
        echo "  Status: CONFIGURED"
    else
        echo "  Status: NOT CONFIGURED"
    fi
else
    echo "  Status: .env file not found"
fi

echo ""
echo "[Quick Commands]"
echo "  View logs: tail -f gateway.log"
echo "  Stop: pkill -f run_gateway.py"
echo "  Restart: ./start_gateway.sh"
echo ""
echo "========================================="
