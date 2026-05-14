#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Gateway 消息处理
"""

import sys
import subprocess
import json
import time
from pathlib import Path

# Set UTF-8 encoding for stdout on Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

def test_gateway_message():
    """测试发送消息到 Gateway"""
    print("Testing Gateway message processing...")

    gateway_entry = Path(__file__).parent / "TUI" / "gateway_entry.py"

    # Start gateway with UTF-8 encoding
    proc = subprocess.Popen(
        [sys.executable, "-u", str(gateway_entry)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).parent),
        encoding='utf-8',
        errors='replace'  # Replace invalid characters instead of crashing
    )

    try:
        # Wait for gateway.ready
        print("Waiting for gateway.ready...")
        ready = False
        for _ in range(10):
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.5)
                continue

            print(f"[Gateway] {line.strip()}")

            if '"type": "gateway.ready"' in line or '"type":"gateway.ready"' in line:
                ready = True
                print("\n[OK] Gateway is ready!\n")
                break

        if not ready:
            print("[ERROR] Gateway not ready")
            return False

        # Send test message
        print("Sending message: 'hello'")
        request = {
            "jsonrpc": "2.0",
            "method": "agent.send_message",
            "params": {
                "message": "hello",
                "session_id": "test"
            },
            "id": "1"
        }

        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        print("[OK] Message sent\n")

        # Wait for response (30 seconds timeout)
        print("Waiting for response...")
        start_time = time.time()
        events_received = []
        response_received = False

        while time.time() - start_time < 30:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue

            print(f"[Gateway] {line.strip()}")

            try:
                msg = json.loads(line)

                # Check for events
                if msg.get("method") == "event":
                    event_type = msg.get("params", {}).get("type")
                    events_received.append(event_type)
                    print(f"  -> Event: {event_type}")

                # Check for response
                if msg.get("id") == "1":
                    response_received = True
                    result = msg.get("result", {})
                    answer = result.get("answer", "")
                    print(f"\n[SUCCESS] Got answer: {answer}\n")
                    break

            except json.JSONDecodeError:
                pass

        if not response_received:
            print("\n[WARNING] No response received within 30 seconds")
            print(f"Events received: {events_received}")
            return False

        print(f"\nEvents received: {events_received}")
        return True

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        print("\nTerminating gateway...")
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Done")


if __name__ == "__main__":
    success = test_gateway_message()
    sys.exit(0 if success else 1)
