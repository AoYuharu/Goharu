"""
Test script for TUI Gateway communication
"""

import json
import subprocess
import sys
import time
from pathlib import Path
import io

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_gateway():
    """Test gateway communication"""
    print("Starting gateway...")

    # Start gateway process
    gateway_entry = Path(__file__).parent / "TUI" / "gateway" / "entry.py"
    proc = subprocess.Popen(
        [sys.executable, str(gateway_entry)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        # Read gateway.ready event
        line = proc.stdout.readline()
        print(f"Received: {line.strip()}")

        ready_event = json.loads(line)
        assert ready_event["method"] == "event"
        assert ready_event["params"]["type"] == "gateway.ready"
        print("✓ Gateway ready event received")

        # Test RPC call: agent.get_history
        request = {
            "jsonrpc": "2.0",
            "method": "agent.get_history",
            "params": {"session_id": "test", "limit": 10},
            "id": "1"
        }

        print(f"\nSending request: {request['method']}")
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()

        # Read response
        line = proc.stdout.readline()
        print(f"Received: {line.strip()}")

        response = json.loads(line)
        assert response["id"] == "1"
        assert "result" in response
        print("✓ RPC call successful")
        print(f"  Result: {response['result']}")

        print("\n✓ All tests passed!")

    finally:
        proc.terminate()
        proc.wait(timeout=2)

if __name__ == "__main__":
    test_gateway()
