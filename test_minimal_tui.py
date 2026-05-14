"""
Minimal TUI test - verify Textual rendering works
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Static, Input, RichLog
from textual.binding import Binding
from rich.text import Text


class MinimalTUI(App):
    """Minimal TUI for testing"""

    CSS = """
    Screen {
        background: $surface;
    }

    #header {
        background: $primary;
        color: $text;
        height: 3;
        content-align: center middle;
    }

    #log {
        border: solid $primary;
        height: 1fr;
    }

    #input {
        dock: bottom;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("🤖 TableHelper TUI - Test Mode", id="header")
        yield RichLog(id="log", wrap=True, highlight=True)
        yield Input(placeholder="Type a message...", id="input")
        yield Footer()

    def on_mount(self):
        log = self.query_one("#log", RichLog)
        log.write(Text("Welcome to TableHelper TUI!", style="bold green"))
        log.write(Text("This is a minimal test version.", style="dim"))
        log.write("")
        log.write(Text("Type a message and press Enter to test input.", style="italic"))

    def on_input_submitted(self, event: Input.Submitted):
        log = self.query_one("#log", RichLog)
        input_widget = self.query_one("#input", Input)

        message = event.value.strip()
        if message:
            # Echo the message
            text = Text()
            text.append("You: ", style="bold cyan")
            text.append(message)
            log.write(text)

            # Clear input
            input_widget.value = ""

            # Simulate response
            response = Text()
            response.append("Echo: ", style="bold green")
            response.append(message, style="italic")
            log.write(response)


if __name__ == "__main__":
    app = MinimalTUI()
    app.run()
