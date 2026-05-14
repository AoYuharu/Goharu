"""
Gateway Client for TUI

Manages subprocess communication with the Gateway server
"""

import json
import subprocess
import sys
import threading
from typing import Dict, Any, Callable, Optional
from pathlib import Path
from queue import Queue


class GatewayClient:
    """Client for communicating with Gateway subprocess"""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.event_handlers: Dict[str, list[Callable]] = {}
        self.pending_requests: Dict[str, Queue] = {}
        self.request_id = 0
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._ready_received = False
        self._stderr_lines: list = []
        self._killed_intentionally = False

    def start(self):
        """Start the gateway subprocess"""
        if self.process is not None:
            return

        self._ready_received = False
        self._stderr_lines.clear()

        # Get Python executable
        python = sys.executable

        # Get gateway entry script (corrected path)
        gateway_entry = Path(__file__).parent / "gateway_entry.py"

        if not gateway_entry.exists():
            raise FileNotFoundError(f"Gateway entry script not found: {gateway_entry}")

        # Start subprocess with unbuffered output and UTF-8 encoding
        # IMPORTANT: On Windows, we need to be very explicit about UTF-8
        import locale
        import os

        # Force UTF-8 environment
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'

        self.process = subprocess.Popen(
            [python, "-u", str(gateway_entry)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(Path(__file__).parent.parent),  # Set working directory to project root
            encoding='utf-8',
            errors='replace',  # Replace invalid characters instead of crashing
            env=env  # Pass UTF-8 environment
        )

        self._running = True

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # Start stderr reader
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def stop(self):
        """Stop the gateway subprocess"""
        self._running = False

        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except:
                self.process.kill()
            finally:
                self.process = None

    def force_kill(self):
        """Force kill gateway subprocess and all children via taskkill"""
        if not self.process:
            return
        self._killed_intentionally = True
        self._running = False
        pid = self.process.pid
        try:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(pid)],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
        try:
            self.process.wait(timeout=3)
        except Exception:
            pass
        self.process = None

    def restart(self):
        """Restart the gateway subprocess"""
        self.force_kill()
        self._killed_intentionally = False
        self.start()

    def on_event(self, event_type: str, handler: Callable):
        """
        Register an event handler

        Args:
            event_type: Event type to listen for
            handler: Callback function(payload)
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)

    def call(self, method: str, params: Dict[str, Any]) -> Any:
        """
        Call a JSON-RPC method (blocking)

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Method result
        """
        if not self.process:
            raise RuntimeError("Gateway not started")

        # Generate request ID
        self.request_id += 1
        req_id = str(self.request_id)

        # Create response queue
        response_queue = Queue()
        self.pending_requests[req_id] = response_queue

        # Send request
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id
        }

        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        except Exception as e:
            del self.pending_requests[req_id]
            raise RuntimeError(f"Failed to send request: {e}")

        # Wait for response (with timeout)
        try:
            response = response_queue.get(timeout=30)
            if "error" in response:
                raise RuntimeError(response["error"]["message"])
            return response.get("result")
        except Exception as e:
            raise RuntimeError(f"Request timeout or error: {e}")
        finally:
            if req_id in self.pending_requests:
                del self.pending_requests[req_id]

    def _read_loop(self):
        """Read messages from gateway stdout"""
        if not self.process or not self.process.stdout:
            return

        try:
            for line in self.process.stdout:
                if not self._running:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    message = json.loads(line)
                    if message.get("method") == "event":
                        params = message.get("params", {})
                        if params.get("type") == "gateway.ready":
                            self._ready_received = True
                    self._handle_message(message)
                except json.JSONDecodeError:
                    # Ignore malformed JSON
                    pass
        except Exception:
            # If killed intentionally, don't emit crash error
            if self._killed_intentionally:
                return
            # If gateway crashes after ready, emit error to TUI
            if self._ready_received and self._running:
                stderr_tail = "\n".join(self._stderr_lines[-5:]) if self._stderr_lines else "(no stderr output)"
                self._handle_message({
                    "method": "event",
                    "params": {
                        "type": "agent.error",
                        "payload": {
                            "message": f"Gateway subprocess crashed unexpectedly.\n\nStderr:\n{stderr_tail}"
                        }
                    }
                })
        finally:
            # If killed intentionally, don't emit crash error
            if self._killed_intentionally:
                return
            # If subprocess stdout closed before we got gateway.ready,
            # the subprocess crashed during init → emit error to TUI
            if not self._ready_received and self._running:
                stderr_tail = "\n".join(self._stderr_lines[-5:]) if self._stderr_lines else "(no stderr output)"
                self._handle_message({
                    "method": "event",
                    "params": {
                        "type": "gateway.init_error",
                        "payload": {
                            "message": f"Gateway subprocess exited unexpectedly before initialization completed.\n\nStderr:\n{stderr_tail}"
                        }
                    }
                })

    def _read_stderr(self):
        """Read stderr from gateway (for debugging)"""
        if not self.process or not self.process.stderr:
            return

        try:
            for line in self.process.stderr:
                if not self._running:
                    break
                stripped = line.strip()
                self._stderr_lines.append(stripped)
                print(f"[gateway stderr] {stripped}", file=sys.stderr)
        except Exception:
            pass

    def _handle_message(self, message: Dict[str, Any]):
        """Handle a message from gateway"""
        # Check if it's an event
        if message.get("method") == "event":
            params = message.get("params", {})
            event_type = params.get("type")
            payload = params.get("payload", {})

            # Dispatch to handlers
            handlers = self.event_handlers.get(event_type, [])
            for handler in handlers:
                try:
                    handler(payload)
                except Exception as e:
                    print(f"[gateway client] event handler error: {e}", file=sys.stderr)

        # Check if it's a response
        elif "id" in message:
            req_id = str(message["id"])
            if req_id in self.pending_requests:
                self.pending_requests[req_id].put(message)
