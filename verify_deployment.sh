#!/bin/bash

echo "========================================="
echo "QQ Bot Deployment Verification"
echo "========================================="
echo ""

PASS=0
FAIL=0

# Test 1: Check Python
echo "[1/10] Checking Python..."
if command -v python3 &> /dev/null; then
    echo "  [OK] Python3: $(python3 --version)"
    ((PASS++))
else
    echo "  [FAIL] Python3 not found"
    ((FAIL++))
fi

# Test 2: Check dependencies
echo "[2/10] Checking dependencies..."
python3 -c 'import yaml, aiohttp, httpx, anthropic' 2>/dev/null
if [ $? -eq 0 ]; then
    echo "  [OK] All dependencies installed"
    ((PASS++))
else
    echo "  [FAIL] Missing dependencies"
    ((FAIL++))
fi

# Test 3: Check config file
echo "[3/10] Checking config file..."
if [ -f config_server.yaml ]; then
    echo "  [OK] config_server.yaml exists"
    ((PASS++))
else
    echo "  [FAIL] config_server.yaml not found"
    ((FAIL++))
fi

# Test 4: Check .env file
echo "[4/10] Checking .env file..."
if [ -f .env ]; then
    echo "  [OK] .env file exists"
    ((PASS++))
else
    echo "  [WARN] .env file not found"
    ((FAIL++))
fi

# Test 5: Check Gateway module
echo "[5/10] Checking Gateway module..."
python3 -c 'import sys; sys.path.insert(0, "."); from Gateway.gateway_runner import GatewayRunner' 2>/dev/null
if [ $? -eq 0 ]; then
    echo "  [OK] Gateway module can be imported"
    ((PASS++))
else
    echo "  [FAIL] Gateway module import failed"
    ((FAIL++))
fi

# Test 6: Check QQ Adapter
echo "[6/10] Checking QQ Adapter..."
python3 -c 'import sys; sys.path.insert(0, "."); from Gateway.platforms.qq_adapter import QQAdapter' 2>/dev/null
if [ $? -eq 0 ]; then
    echo "  [OK] QQ Adapter can be imported"
    ((PASS++))
else
    echo "  [FAIL] QQ Adapter import failed"
    ((FAIL++))
fi

# Test 7: Check Agent modules
echo "[7/10] Checking Agent modules..."
python3 -c 'import sys; sys.path.insert(0, "."); from Agent.ActorAgent import ActorAgent' 2>/dev/null
if [ $? -eq 0 ]; then
    echo "  [OK] Agent modules can be imported"
    ((PASS++))
else
    echo "  [FAIL] Agent modules import failed"
    ((FAIL++))
fi

# Test 8: Check runtime directories
echo "[8/10] Checking runtime directories..."
if [ -d runtime_memory ]; then
    echo "  [OK] runtime_memory directory exists"
    ((PASS++))
else
    echo "  [WARN] runtime_memory directory not found"
    mkdir -p runtime_memory/gateway runtime_memory/daily runtime_memory/topic
    echo "  [OK] Created runtime directories"
    ((PASS++))
fi

# Test 9: Check startup scripts
echo "[9/10] Checking startup scripts..."
if [ -x start_gateway.sh ] && [ -f run_gateway.py ]; then
    echo "  [OK] Startup scripts exist"
    ((PASS++))
else
    echo "  [FAIL] Startup scripts missing or not executable"
    ((FAIL++))
fi

# Test 10: Test configuration loading
echo "[10/10] Testing configuration loading..."
python3 << 'PYEOF' 2>/dev/null
import sys
sys.path.insert(0, '.')
from configurationLoader import Configuration
config = Configuration('config_server.yaml')
assert config.get('gateway.platforms.qq.enabled') == True
print('  [OK] Configuration loads correctly')
PYEOF
if [ $? -eq 0 ]; then
    ((PASS++))
else
    echo "  [FAIL] Configuration loading failed"
    ((FAIL++))
fi

# Summary
echo ""
echo "========================================="
echo "Verification Summary"
echo "========================================="
echo "Passed: $PASS/10"
echo "Failed: $FAIL/10"
echo ""

if [ $FAIL -eq 0 ]; then
    echo "[SUCCESS] All checks passed!"
    echo ""
    echo "Next steps:"
    echo "1. Edit .env and set your API key"
    echo "2. Run: ./start_gateway.sh"
    echo ""
    exit 0
else
    echo "[WARNING] Some checks failed"
    echo "Please review the errors above"
    echo ""
    exit 1
fi
