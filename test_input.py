#!/usr/bin/env python3
"""
测试 TUI 输入功能
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static
from textual.containers import Container, Vertical


class SimpleInputTest(App):
    """简单的输入测试应用"""

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 3;
        background: blue;
        color: white;
        content-align: center middle;
    }

    #log {
        height: 1fr;
        border: solid green;
    }

    #input-container {
        height: 3;
        background: gray;
        padding: 0 1;
    }

    Input {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Input Test - Type and press Enter", id="header")
        yield RichLog(id="log", wrap=True)
        yield Container(
            Input(placeholder="Type here and press Enter...", id="test-input"),
            id="input-container"
        )

    def on_mount(self):
        """Focus input on start"""
        self.query_one("#test-input", Input).focus()
        log = self.query_one("#log", RichLog)
        log.write("[green]Ready! Type something and press Enter.[/green]")

    def on_input_submitted(self, event: Input.Submitted):
        """Handle input submission"""
        if event.input.id != "test-input":
            return

        message = event.value.strip()
        if not message:
            return

        # Clear input
        event.input.value = ""

        # Add to log
        log = self.query_one("#log", RichLog)
        log.write(f"[cyan]You typed:[/cyan] {message}")


if __name__ == "__main__":
    app = SimpleInputTest()
    app.run()
