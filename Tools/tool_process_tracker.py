"""
Tool Process Tracker

Tracks subprocess PIDs spawned by tools (e.g. run_cmd).
On interrupt, enables precise kill of all active tool subprocesses
without killing the Gateway itself.
"""

import subprocess
import threading


class ToolProcessTracker:
    """Thread-safe tracker for tool subprocess PIDs"""

    def __init__(self):
        self._pids: set[int] = set()
        self._lock = threading.Lock()

    def register(self, pid: int):
        """Register a tool subprocess PID"""
        with self._lock:
            self._pids.add(pid)

    def unregister(self, pid: int):
        """Remove a PID after process completes"""
        with self._lock:
            self._pids.discard(pid)

    def kill_all(self):
        """Force kill all tracked PIDs via taskkill"""
        with self._lock:
            pids = list(self._pids)
            self._pids.clear()

        for pid in pids:
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass


# Module-level singleton
tool_process_tracker = ToolProcessTracker()
