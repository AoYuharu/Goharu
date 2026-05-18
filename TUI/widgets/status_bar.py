"""
Status Bar Widget

Shows current status, model info, and stats
"""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static
from rich.text import Text


# Unicode braille spinner frames
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class StatusBar(Horizontal):
    """Status bar showing system status"""

    DEFAULT_CSS = """
    StatusBar {
        height: 3;
        background: $surface;
        layout: horizontal;
    }

    #status-spinner-container {
        width: auto;
        padding: 0 1;
        display: none;
    }

    #status-spinner {
        width: auto;
        color: $accent;
        content-align: left middle;
    }

    #status-text {
        width: 1fr;
        padding: 0 1;
        content-align: left middle;
    }

    #stats-text {
        width: auto;
        padding: 0 1;
        content-align: right middle;
    }
    """

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Horizontal(
            Static("", id="status-spinner"),
            id="status-spinner-container",
        )
        yield Static("Starting...", id="status-text")
        yield Static("", id="stats-text")

    def on_mount(self):
        """Called when widget is mounted"""
        self.status_text = self.query_one("#status-text", Static)
        self.stats_text = self.query_one("#stats-text", Static)
        self.spinner_container = self.query_one("#status-spinner-container", Horizontal)
        self.spinner_widget = self.query_one("#status-spinner", Static)
        self._spinner_frame = 0
        self._spinner_timer = None
        self._working = False
        self._working_label = ""

    def show_working(self, label: str):
        """Show animated spinner with working label"""
        self._working_label = str(label or "")
        self.spinner_widget.update(f"{_SPINNER_FRAMES[self._spinner_frame]} {self._working_label}")
        self.spinner_container.styles.display = "block"
        self.status_text.update("")
        self.refresh(layout=True)
        if not self._working:
            self._working = True
            self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def hide_working(self):
        """Hide spinner"""
        self._working = False
        self._working_label = ""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.spinner_container.styles.display = "none"
        self.spinner_widget.update("")

    def _tick_spinner(self):
        """Advance spinner frame"""
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self.spinner_widget.update(f"{_SPINNER_FRAMES[self._spinner_frame]} {self._working_label}")

    def set_status(self, message: str, status_type: str = "info"):
        """
        Update status message

        Args:
            message: Status message
            status_type: One of "success", "error", "warning", "thinking", "working", "info"
        """
        labels = {
            "success": "[OK]",
            "error": "[ERR]",
            "warning": "[WARN]",
            "thinking": "...",
            "working": "[BUSY]",
            "info": "[*]"
        }

        styles = {
            "success": "bold green",
            "error": "bold red",
            "warning": "bold yellow",
            "thinking": "bold magenta",
            "working": "bold cyan",
            "info": "bold blue"
        }

        prefix = labels.get(status_type, "[*]")
        style = styles.get(status_type, "bold")

        text = Text()
        text.append(f"{prefix} ", style=style)
        text.append(message, style=style)

        self.status_text.update(text)

    def set_stats(self, stats: dict):
        """
        Update statistics display

        Args:
            stats: Dictionary with compact token stats
        """
        current_tokens = stats.get("current_tokens", "0")
        prompt_cache_ratio = stats.get("prompt_cache_ratio", "0.0%")

        text = Text()
        text.append("Current Tokens: ", style="dim")
        text.append(str(current_tokens), style="bold cyan")
        text.append(" | Prompt Cache Ratio: ", style="dim")
        text.append(str(prompt_cache_ratio), style="bold green")

        self.stats_text.update(text)
