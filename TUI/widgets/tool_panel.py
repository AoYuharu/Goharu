"""
Tool Panel Widget

Displays two sections:
- "Tool Calling": shows tools currently being executed with real-time count-up timer
- "Tool Result": shows completed tool execution results (accumulates)
"""

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import RichLog, Static
from rich.text import Text
from rich.syntax import Syntax
from datetime import datetime
import json
import queue
import time


class ToolPanel(Container):
    """Panel showing tool execution activity, split into Calling and Result sections"""

    DEFAULT_CSS = """
    ToolPanel {
        layout: vertical;
    }

    #tool-header {
        height: 1;
        background: $accent;
        color: $text;
        content-align: center middle;
    }

    #calling-section {
        height: 1fr;
        border: solid $warning;
        margin: 0 0 1 0;
    }

    #calling-header {
        height: 1;
        background: $warning;
        color: $text;
        content-align: center middle;
    }

    #calling-log {
        height: 1fr;
    }

    #result-section {
        height: 2fr;
        border: solid $success;
    }

    #result-header {
        height: 1;
        background: $success;
        color: $text;
        content-align: center middle;
    }

    #result-log {
        height: 1fr;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Active tool calls: {call_id: {tool_name, arguments, start_time}}
        self._active_calls: dict[str, dict] = {}
        # Completed results: [{tool_name, result, timestamp}]
        self._completed_results: list[dict] = []
        # Counter for generating unique call IDs
        self._call_counter = 0
        # Thread-safe queue for tool events (reader thread → main thread)
        self._event_queue = queue.Queue()

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Static("🔧 Tool Activity", id="tool-header")
        yield Vertical(
            Static("▶ Tool Calling", id="calling-header"),
            RichLog(id="calling-log", wrap=True, highlight=True, markup=True),
            id="calling-section",
        )
        yield Vertical(
            Static("✓ Tool Result", id="result-header"),
            RichLog(id="result-log", wrap=True, highlight=True, markup=True),
            id="result-section",
        )

    def on_mount(self):
        """Called when widget is mounted"""
        self.calling_log = self.query_one("#calling-log", RichLog)
        self.result_log = self.query_one("#result-log", RichLog)

        # Fix RichLog width to avoid truncation
        for log in [self.calling_log, self.result_log]:
            if hasattr(log, '_console'):
                log._console.width = 200

        # Start periodic tick for elapsed time display (every second)
        self.set_interval(1.0, self._tick)

    def _tick(self):
        """Called every second — drain pending tool events, then refresh UI"""
        # Drain pending events from reader thread (thread-safe queue)
        had_events = False
        while True:
            try:
                event_type, data = self._event_queue.get_nowait()
                had_events = True
            except queue.Empty:
                break

            if event_type == "call":
                self.add_tool_call(
                    data.get("tool_name", "unknown"),
                    data.get("arguments", {}),
                    data.get("step", 0),
                )
            elif event_type == "result":
                self.add_tool_result(
                    data.get("tool_name", "unknown"),
                    data.get("result", ""),
                )

        # Always refresh UI if there are active calls or new results
        if had_events or self._active_calls:
            self._refresh_calling()
        if had_events or self._completed_results:
            self._refresh_results()

    def add_tool_call(self, tool_name: str, arguments: dict, step: int):
        """Add a tool call to the active (Calling) section"""
        call_id = f"{tool_name}_{self._call_counter}_{int(time.time() * 1000)}"
        self._call_counter += 1

        self._active_calls[call_id] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "start_time": time.time(),
        }

        self._refresh_calling()

    def add_tool_result(self, tool_name: str, result: str):
        """Move tool from Calling to Result section"""
        # Find and remove the first matching active call
        call_id_to_remove = None
        for cid, call in self._active_calls.items():
            if call["tool_name"] == tool_name:
                call_id_to_remove = cid
                break

        if call_id_to_remove:
            call_info = self._active_calls.pop(call_id_to_remove)
        else:
            call_info = {
                "tool_name": tool_name,
                "start_time": time.time(),
            }

        # Add to completed results (elapsed is fixed at completion time)
        elapsed = int(time.time() - call_info.get("start_time", time.time()))
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{mins:02d}:{secs:02d}"

        self._completed_results.append({
            "tool_name": tool_name,
            "result": result,
            "elapsed_str": elapsed_str,
        })

        # Re-render both sections
        self._refresh_calling()
        self._refresh_results()

    def _format_elapsed(self, start_time: float) -> str:
        """Format elapsed time as MM:SS"""
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        return f"{mins:02d}:{secs:02d}"

    def _build_arg_string(self, arguments: dict) -> str:
        """Build a compact one-line argument string"""
        if not arguments:
            return ""
        parts = []
        for key, value in arguments.items():
            value_str = str(value)
            if len(value_str) > 40:
                value_str = value_str[:40] + "..."
            if isinstance(value, str):
                parts.append(f"{key}={repr(value_str)}")
            else:
                parts.append(f"{key}={value_str}")
        return ", ".join(parts)

    def _refresh_calling(self):
        """Re-render the Calling section (clear + rewrite all active calls)"""
        self.calling_log.clear()

        if not self._active_calls:
            self.calling_log.write(Text("  (no active tools)", style="dim italic"))
            return

        for call_id, call in self._active_calls.items():
            self._write_single_call(call)

    def _write_single_call(self, call: dict):
        """Write a single tool call as one line: 🔧 ToolName(args)    MM:SS"""
        tool_name = call.get("tool_name", "unknown")
        arguments = call.get("arguments", {})
        start_time = call.get("start_time", time.time())

        elapsed_str = self._format_elapsed(start_time)
        arg_str = self._build_arg_string(arguments)

        line = Text()
        line.append("🔧 ", style="bold yellow")
        line.append(tool_name, style="bold cyan")

        if arg_str:
            # Truncate long argument strings to keep on one line
            if len(arg_str) > 50:
                arg_str = arg_str[:50] + "..."
            line.append(f"({arg_str})", style="green")
        else:
            line.append("()", style="green")

        # Pad to align timer on the right
        line.append("    ", style="dim")

        # Append elapsed time
        line.append(elapsed_str, style="bold yellow")

        self.calling_log.write(line)

    def _refresh_results(self):
        """Re-render the Result section (clear + rewrite all completed results)"""
        self.result_log.clear()

        if not self._completed_results:
            self.result_log.write(Text("  (no results yet)", style="dim italic"))
            return

        for entry in self._completed_results:
            self._write_single_result(entry)

    def _write_single_result(self, entry: dict):
        """Write a single completed result entry to the result log"""
        tool_name = entry.get("tool_name", "unknown")
        result = entry.get("result", "")
        elapsed_str = entry.get("elapsed_str", "")

        # One-line header: ✓ ToolName    MM:SS
        header = Text()
        header.append("✓ ", style="bold green")
        header.append(tool_name, style="bold cyan")
        if elapsed_str:
            header.append(f"    {elapsed_str}", style="dim")
        self.result_log.write(header)

        # Result content
        if not result:
            self.result_log.write(Text("  (empty result)", style="dim"))
            return

        try:
            result_obj = json.loads(result)
            formatted = json.dumps(result_obj, indent=2, ensure_ascii=False)

            lines = formatted.split('\n')
            if len(lines) > 10:
                display_lines = lines[:8]
                remaining = len(lines) - 8
                display_lines.append(f"  ... ({remaining} more lines)")
                formatted = '\n'.join(display_lines)
            elif len(formatted) > 500:
                formatted = formatted[:500] + "\n  ... (truncated)"

            syntax = Syntax(formatted, "json", theme="monokai", line_numbers=False)
            self.result_log.write(syntax)
        except (json.JSONDecodeError, ValueError):
            if len(result) > 300:
                result = result[:300] + "..."
            self.result_log.write(Text(f"  {result}", style="green"))

    def add_thinking(self, step: int, thinking: str):
        """Add agent thinking/reasoning (unused currently, kept for compatibility)"""
        pass

    def clear(self):
        """Clear both sections"""
        self._active_calls.clear()
        self._completed_results.clear()
        self.calling_log.clear()
        self.result_log.clear()
