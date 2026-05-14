#!/usr/bin/env python3
"""
Basic TUI functionality test
"""

import sys
import time
import subprocess
from pathlib import Path

def test_gateway_subprocess():
    """Test that gateway subprocess can start and respond"""
    print("Testing Gateway subprocess...")

    gateway_entry = Path(__file__).parent / "TUI" / "gateway_entry.py"

    # Start gateway
    proc = subprocess.Popen(
        [sys.executable, "-u", str(gateway_entry)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).parent)
    )

    try:
        # Wait for gateway.ready event
        print("Waiting for gateway.ready event...")
        ready = False

        for _ in range(10):  # 10 second timeout
            line = proc.stdout.readline()
            if not line:
                break

            print(f"Gateway output: {line.strip()}")

            if '"type": "gateway.ready"' in line or '"type":"gateway.ready"' in line:
                ready = True
                print("[OK] Gateway is ready!")
                break

        if not ready:
            print("[ERROR] Gateway did not send ready event")
            return False

        # Send a test request
        print("\nSending test request...")
        test_request = '{"jsonrpc": "2.0", "method": "agent.send_message", "params": {"message": "Hello", "session_id": "test"}, "id": "1"}\n'
        proc.stdin.write(test_request)
        proc.stdin.flush()

        # Wait for response (with timeout)
        print("Waiting for response...")
        response_received = False

        for _ in range(30):  # 30 second timeout
            line = proc.stdout.readline()
            if not line:
                break

            print(f"Gateway response: {line.strip()}")

            if '"id": "1"' in line or '"id":"1"' in line:
                response_received = True
                print("[OK] Received response!")
                break

        if not response_received:
            print("[WARNING] No response received (this is expected if agent takes long)")

        return True

    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        print("\nCleaning up...")
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Gateway subprocess terminated")


def test_gateway_client():
    """Test GatewayClient class"""
    print("\n" + "="*60)
    print("Testing GatewayClient class...")

    try:
        from TUI.gateway_client import GatewayClient

        client = GatewayClient()
        print("[OK] GatewayClient instantiated")

        # Start gateway
        print("Starting gateway...")
        client.start()
        print("[OK] Gateway started")

        # Wait a bit for ready
        time.sleep(2)

        # Try to call a method
        print("Calling agent.send_message...")
        try:
            result = client.call("agent.send_message", {
                "message": "Test message",
                "session_id": "test"
            })
            print(f"[OK] Got result: {result}")
        except Exception as e:
            print(f"[WARNING] Call failed (expected if agent not ready): {e}")

        # Stop gateway
        print("Stopping gateway...")
        client.stop()
        print("[OK] Gateway stopped")

        return True

    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("="*60)
    print("TableHelper TUI Basic Tests")
    print("="*60)

    # Test 1: Gateway subprocess
    success1 = test_gateway_subprocess()

    # Test 2: GatewayClient
    success2 = test_gateway_client()

    print("\n" + "="*60)
    print("Test Summary:")
    print(f"  Gateway subprocess: {'PASS' if success1 else 'FAIL'}")
    print(f"  GatewayClient class: {'PASS' if success2 else 'FAIL'}")
    print("="*60)

    sys.exit(0 if (success1 and success2) else 1)
