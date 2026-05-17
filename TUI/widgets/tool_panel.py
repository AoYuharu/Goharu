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
from rich.panel import Panel
from rich import box
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
        yield Static("Tool Activity", id="tool-header")
        yield Vertical(
            Static("> Calling", id="calling-header"),
            RichLog(id="calling-log", wrap=True, highlight=True, markup=True, max_lines=500),
            id="calling-section",
        )
        yield Vertical(
            Static("> Results", id="result-header"),
            RichLog(id="result-log", wrap=True, highlight=True, markup=True, max_lines=2000),
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
        """Called every second — drain pending tool events, then update elapsed timers"""
        # Drain pending events from reader thread (thread-safe queue)
        while True:
            try:
                event_type, data = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "call":
                self.add_tool_call(
                    data.get("tool_name", "unknown"),
                    data.get("arguments", {}),
                    data.get("step", 0),
                    data.get("call_id", ""),
                )
            elif event_type == "result":
                self.add_tool_result(
                    data.get("tool_name", "unknown"),
                    data.get("result", ""),
                    data.get("call_id", ""),
                )

        # Only refresh calling section to update elapsed timers.
        # Results section is NOT refreshed here — it only updates on new results
        # via add_tool_result(), avoiding the auto-scroll-to-bottom on every tick.
        if self._active_calls:
            self._refresh_calling()

    def add_tool_call(self, tool_name: str, arguments: dict, step: int, call_id: str = ""):
        """Add a tool call to the active (Calling) section"""
        if not call_id:
            call_id = f"{tool_name}_{self._call_counter}_{int(time.time() * 1000)}"
            self._call_counter += 1

        self._active_calls[call_id] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "start_time": time.time(),
        }

        self._refresh_calling()

    def add_tool_result(self, tool_name: str, result: str, call_id: str = ""):
        """Move tool from Calling to Result section"""
        # Find by exact call_id first, fallback to name matching
        call_id_to_remove = None
        if call_id and call_id in self._active_calls:
            call_id_to_remove = call_id
        else:
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

        # 限制 _completed_results 列表大小，防止内存无限增长
        if len(self._completed_results) > 100:
            self._completed_results = self._completed_results[-50:]

        # 只更新 calling 区（通常只有 1-3 个活跃调用，不贵）
        self._refresh_calling()

        # 增量追加结果，不用 clear+rewrite 所有历史
        # （之前的 _refresh_results 会 O(n) 重渲全部结果，几十步后主线程吃满）
        self._write_single_result(self._completed_results[-1])

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
        line.append("> ", style="bold yellow")
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

        # One-line header
        header = Text()
        header.append("[OK] ", style="bold green")
        header.append(tool_name, style="bold cyan")
        if elapsed_str:
            header.append(f"    {elapsed_str}", style="dim")
        self.result_log.write(header)

        # Format result body with a clean Panel
        if not result:
            self.result_log.write(Text("  (empty result)", style="dim"))
            return

        body = self._format_result_content(result)
        panel = Panel(body, box=box.ROUNDED, border_style="dim green",
                      padding=(0, 1))
        self.result_log.write(panel)

    def _format_result_content(self, result: str):
        """Format result content — use clean readable text, not raw JSON."""
        try:
            obj = json.loads(result)

            # ── Unwrap {"content": "..."} ──
            if isinstance(obj, dict) and list(obj.keys()) == ["content"]:
                inner = obj["content"]
                if isinstance(inner, str):
                    return self._truncate_text(inner, 600)

            # ── List of dicts → compact table ──
            if isinstance(obj, list) and obj and all(isinstance(i, dict) for i in obj):
                return self._build_compact_table(obj)

            # ── Plain dict → key: value lines ──
            if isinstance(obj, dict):
                return self._build_dict_lines(obj)

            # ── Other JSON (list, scalar) → pretty text ──
            formatted = json.dumps(obj, indent=2, ensure_ascii=False)
            return self._truncate_text(formatted, 600)

        except (json.JSONDecodeError, ValueError):
            return self._truncate_text(result.strip(), 500)

    @staticmethod
    def _build_dict_lines(obj: dict, indent: int = 0) -> Text:
        """Render a dict as clean key: value lines."""
        text = Text()
        prefix = "  " * indent
        for i, (k, v) in enumerate(obj.items()):
            if i > 0:
                text.append("\n")
            text.append(f"{prefix}", style="")
            text.append(f"{k}", style="bold yellow")
            text.append(": ", style="dim")
            if isinstance(v, dict):
                text.append("\n")
                text.append(ToolPanel._build_dict_lines(v, indent + 1))
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                text.append(f"[{len(v)} items]", style="dim")
            elif isinstance(v, list):
                items = ", ".join(str(x) for x in v[:6])
                text.append(items, style="green")
                if len(v) > 6:
                    text.append(f" ... +{len(v) - 6}", style="dim")
            else:
                s = str(v)
                if len(s) > 80:
                    s = s[:80] + "..."
                text.append(s, style="green")
        return text

    @staticmethod
    def _build_compact_table(items: list) -> Text:
        """Compact table for list-of-dict results."""
        if not items:
            return Text("(empty list)", style="dim")

        total = len(items)
        keys = [k for k in items[0].keys() if not k.startswith("_")][:4]

        text = Text()
        text.append(f"{total} items", style="bold dim")
        text.append("\n")

        # Header
        col_width = 24
        header_text = "  " + "  ".join(k.ljust(col_width)[:col_width] for k in keys)
        text.append(header_text, style="bold cyan underline")
        text.append("\n")

        for item in items[:8]:
            row = "  " + "  ".join(
                str(item.get(k, ""))[:col_width].ljust(col_width)[:col_width]
                for k in keys
            )
            text.append(row, style="green")
            text.append("\n")

        if total > 8:
            text.append(f"  ... {total - 8} more items", style="dim")
        return text

    @staticmethod
    def _truncate_text(s: str, limit: int) -> Text:
        """Truncate long text with a note."""
        if len(s) <= limit:
            return Text(s, style="green")
        text = Text(s[:limit], style="green")
        text.append(f"\n  ... ({len(s) - limit} more chars)", style="dim")
        return text

    def clear(self):
        """Clear both sections"""
        self._active_calls.clear()
        self._completed_results.clear()
        self.calling_log.clear()
        self.result_log.clear()
