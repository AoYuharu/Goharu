#!/usr/bin/env python3
"""
Simple Gateway startup test
"""

import sys
import subprocess
import time
from pathlib import Path

def test_gateway_ready():
    """Test that gateway can start and send ready event"""
    print("Testing Gateway startup...")

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
        # Wait for gateway.ready event (with timeout)
        print("Waiting for gateway.ready event...")
        start_time = time.time()
        ready = False

        while time.time() - start_time < 10:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue

            print(f"Gateway: {line.strip()}")

            if '"type": "gateway.ready"' in line or '"type":"gateway.ready"' in line:
                ready = True
                print("\n[SUCCESS] Gateway is ready!")
                break

        if not ready:
            print("\n[FAILED] Gateway did not send ready event within 10 seconds")
            # Print stderr
            print("\nStderr output:")
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                print(f"  {line.strip()}")
            return False

        return True

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
    success = test_gateway_ready()
    sys.exit(0 if success else 1)
