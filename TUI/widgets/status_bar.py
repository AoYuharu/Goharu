"""
Status Bar Widget

Shows current status, model info, and stats
"""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static
from rich.text import Text


class StatusBar(Horizontal):
    """Status bar showing system status"""

    DEFAULT_CSS = """
    StatusBar {
        height: 3;
        background: $surface;
        layout: horizontal;
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
        yield Static("⏳ Starting...", id="status-text")
        yield Static("", id="stats-text")

    def on_mount(self):
        """Called when widget is mounted"""
        self.status_text = self.query_one("#status-text", Static)
        self.stats_text = self.query_one("#stats-text", Static)

    def set_status(self, message: str, status_type: str = "info"):
        """
        Update status message

        Args:
            message: Status message
            status_type: One of "success", "error", "warning", "thinking", "working", "info"
        """
        icons = {
            "success": "✓",
            "error": "✗",
            "warning": "⚠",
            "thinking": "💭",
            "working": "⚙",
            "info": "ℹ"
        }

        styles = {
            "success": "bold green",
            "error": "bold red",
            "warning": "bold yellow",
            "thinking": "bold magenta",
            "working": "bold cyan",
            "info": "bold blue"
        }

        icon = icons.get(status_type, "ℹ")
        style = styles.get(status_type, "bold")

        text = Text()
        text.append(f"{icon} ", style=style)
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
