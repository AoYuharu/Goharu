"""
JSON-RPC Gateway Server for TableHelper TUI

Handles communication between TUI frontend and Agent backend.
Protocol: JSON-RPC 2.0 over stdin/stdout
"""

import json
import sys
import threading
import traceback
from typing import Any, Dict, Optional, Callable
from datetime import datetime


class GatewayServer:
    """JSON-RPC server for agent communication"""

    def __init__(self):
        self.methods: Dict[str, Callable] = {}
        self._stdout_lock = threading.Lock()
        self._running = False

    def register_method(self, name: str, handler: Callable):
        """Register a JSON-RPC method handler"""
        self.methods[name] = handler

    def write_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """
        Send an event to the TUI client

        Args:
            event_type: Event type (e.g., "agent.thinking", "tool.call")
            payload: Event data

        Returns:
            bool: True if write succeeded, False if pipe broken
        """
        return self._write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": event_type,
                "payload": payload
            }
        })

    def _write_json(self, obj: Dict[str, Any]) -> bool:
        """
        Write JSON object to stdout

        Returns:
            bool: True on success, False if pipe broken
        """
        try:
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            with self._stdout_lock:
                sys.stdout.write(line)
                sys.stdout.flush()
            return True
        except (BrokenPipeError, OSError):
            # TUI client disconnected
            return False
        except Exception as e:
            # Log error but don't crash
            sys.stderr.write(f"[gateway] write error: {e}\n")
            sys.stderr.flush()
            return False

    def _dispatch(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Dispatch a JSON-RPC request

        Args:
            request: JSON-RPC request object

        Returns:
            JSON-RPC response or None for notifications
        """
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        # Validate request
        if not isinstance(method, str):
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": req_id
            }

        # Find handler
        handler = self.methods.get(method)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": req_id
            }

        # Execute handler
        try:
            result = handler(params)

            # Notification (no response needed)
            if req_id is None:
                return None

            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id
            }
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            sys.stderr.write(f"[gateway] handler error: {error_msg}\n")
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()

            if req_id is None:
                return None

            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": error_msg},
                "id": req_id
            }

    def run(self):
        """
        Main event loop - read JSON-RPC requests from stdin
        """
        self._running = True

        # Send ready event
        self.write_event("gateway.ready", {
            "timestamp": datetime.now().isoformat(),
            "version": "0.1.0"
        })

        try:
            for line in sys.stdin:
                if not self._running:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    # Send parse error
                    self._write_json({
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None
                    })
                    continue

                # Dispatch request
                response = self._dispatch(request)
                if response is not None:
                    if not self._write_json(response):
                        # Pipe broken, exit gracefully
                        break

        except KeyboardInterrupt:
            pass
        finally:
            self._running = False

    def stop(self):
        """Stop the gateway server"""
        self._running = False


# Global gateway instance
_gateway: Optional[GatewayServer] = None


def get_gateway() -> GatewayServer:
    """Get or create the global gateway instance"""
    global _gateway
    if _gateway is None:
        _gateway = GatewayServer()
    return _gateway


def emit_event(event_type: str, payload: Dict[str, Any]):
    """
    Convenience function to emit an event

    Args:
        event_type: Event type
        payload: Event data
    """
    gateway = get_gateway()
    gateway.write_event(event_type, payload)
