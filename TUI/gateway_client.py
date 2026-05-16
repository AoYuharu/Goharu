"""
Gateway Client for TUI

Manages subprocess communication with the Gateway server
"""

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Dict, Any, Callable, Optional

# TUI 侧日志 — 写入 runtime_memory/logs/tui/gateway_client.log
_TUI_LOG_BASE = Path(__file__).parent.parent / "runtime_memory" / "logs" / "tui"
_TUI_LOG_BASE.mkdir(parents=True, exist_ok=True)
_tui_handler = logging.FileHandler(
    _TUI_LOG_BASE / "gateway_client.log",
    encoding="utf-8",
)
_tui_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
_log = logging.getLogger("TUI.GatewayClient")
_log.setLevel(logging.DEBUG)
_log.addHandler(_tui_handler)
_log.propagate = False  # 不向 root logger 传播

# trace 文件（直接 fsync，绕过 logging）
_TRACE_PATH = _TUI_LOG_BASE / "trace.log"


def _trace(msg: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.")
        ts += f"{datetime.now().microsecond // 1000:03d}"
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} [GC] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


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

        _log.info("Starting gateway subprocess: %s -u %s", python, gateway_entry)

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
        _log.info("Gateway subprocess started, pid=%d", self.process.pid)

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # Start stderr reader
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def stop(self):
        """Stop the gateway subprocess"""
        _trace("GatewayClient.stop() called")
        self._running = False

        if self.process:
            try:
                _trace("GatewayClient.stop() closing stdin...")
                self.process.stdin.close()
                self.process.wait(timeout=2)
                _trace("GatewayClient.stop() gateway exited cleanly")
            except:
                _trace("GatewayClient.stop() killing gateway...")
                self.process.kill()
            finally:
                self.process = None
                _trace("GatewayClient.stop() done")

    def force_kill(self):
        """Force kill gateway subprocess and all children (cross-platform)"""
        if not self.process:
            return
        self._killed_intentionally = True
        self._running = False
        pid = self.process.pid
        try:
            self.process.stdin.close()
        except Exception:
            pass
        try:
            # Try the platform-specific subprocess tree kill first
            from Tools.platform_utils import kill_process_tree
            kill_process_tree(pid)
        except Exception:
            try:
                self.process.kill()
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

        _trace("Reader thread started")
        _log.info("Gateway reader thread started")
        msg_count = 0
        try:
            for line in self.process.stdout:
                if not self._running:
                    _log.info("Reader stopping (not running)")
                    break

                msg_count += 1
                line = line.strip()
                if not line:
                    continue

                try:
                    message = json.loads(line)
                    if message.get("method") == "event":
                        params = message.get("params", {})
                        event_type = params.get("type", "")
                        if event_type == "gateway.ready":
                            self._ready_received = True
                            _log.info("Gateway ready signal received")
                        # 不记录高频事件（streaming, thinking_content）
                        if event_type not in ("agent.streaming", "agent.thinking_content", "token.stats"):
                            _log.debug("Event: %s", event_type)
                    self._handle_message(message)
                except json.JSONDecodeError:
                    _log.debug("Skipped malformed JSON: %.100s", line)
        except Exception as e:
            _log.error("Reader thread exception: %s", e, exc_info=True)
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
            _trace(f"Reader thread exit: {msg_count} messages processed")
            _log.info("Reader thread exit: %d messages processed", msg_count)
            # If killed intentionally, don't emit crash error
            if self._killed_intentionally:
                return
            # If subprocess stdout closed before we got gateway.ready,
            # the subprocess crashed during init → emit error to TUI
            if not self._ready_received and self._running:
                stderr_tail = "\n".join(self._stderr_lines[-5:]) if self._stderr_lines else "(no stderr output)"
                _log.error("Gateway exited before ready. Stderr tail: %s", stderr_tail)
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
        """Read stderr from gateway (for debugging). 同时写一份到 TUI 日志。"""
        if not self.process or not self.process.stderr:
            return

        try:
            for line in self.process.stderr:
                if not self._running:
                    break
                stripped = line.strip()
                self._stderr_lines.append(stripped)
                _log.debug("stderr: %s", stripped)
                print(f"[gateway stderr] {stripped}", file=sys.stderr)
        except Exception as e:
            _log.error("Stderr reader exception: %s", e)
        finally:
            _trace("Stderr reader exit")

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
