"""
Command Suggestions Widget

Shows available slash commands when user types /
"""

from textual.widgets import Static
from textual.containers import Container, Vertical
from rich.text import Text
from rich.table import Table


class CommandSuggestions(Container):
    """Widget showing available slash commands"""

    DEFAULT_CSS = """
    CommandSuggestions {
        display: none;
        layer: overlay;
        background: $surface;
        border: solid $primary;
        width: 50;
        height: auto;
        max-height: 15;
        padding: 1;
    }

    CommandSuggestions.visible {
        display: block;
    }

    .command-item {
        padding: 0 1;
    }

    .command-item.selected {
        background: $primary;
        color: $text;
    }
    """

    COMMANDS = [
        {
            "name": "/clear",
            "description": "Clear chat history and context",
            "usage": "/clear"
        },
        {
            "name": "/compact",
            "description": "Summarize conversation via LLM",
            "usage": "/compact"
        },
        {
            "name": "/config",
            "description": "View/set configuration parameters",
            "usage": "/config list"
        },
        {
            "name": "/copy",
            "description": "Copy last assistant message to clipboard",
            "usage": "/copy"
        },
        {
            "name": "/export",
            "description": "Export chat history to file",
            "usage": "/export"
        },
        {
            "name": "/help",
            "description": "Show help information",
            "usage": "/help"
        },
        {
            "name": "/prompt",
            "description": "Browse and edit prompt files in-app",
            "usage": "/prompt"
        },
        {
            "name": "/prompt-cache",
            "description": "Browse & reuse recent long prompts",
            "usage": "/prompt-cache"
        },
        {
            "name": "/exit",
            "description": "Exit the application",
            "usage": "/exit"
        },
        {
            "name": "/quit",
            "description": "Exit the application",
            "usage": "/quit"
        },
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected_index = 0
        self.filtered_commands = []

    def show_suggestions(self, filter_text: str = ""):
        """Show command suggestions filtered by text"""
        # Filter commands
        if filter_text:
            self.filtered_commands = [
                cmd for cmd in self.COMMANDS
                if cmd["name"].startswith(filter_text)
            ]
        else:
            self.filtered_commands = self.COMMANDS.copy()

        if not self.filtered_commands:
            self.add_class("hidden")
            return

        self.selected_index = 0
        self.remove_class("hidden")
        self.add_class("visible")
        self.render_suggestions()

    def hide_suggestions(self):
        """Hide command suggestions"""
        self.remove_class("visible")
        self.add_class("hidden")

    def render_suggestions(self):
        """Render the suggestions list"""
        self.remove_children()

        for i, cmd in enumerate(self.filtered_commands):
            item_class = "command-item selected" if i == self.selected_index else "command-item"

            text = Text()
            text.append(cmd["name"], style="bold cyan" if i == self.selected_index else "cyan")
            text.append(" - ", style="dim")
            text.append(cmd["description"], style="white" if i == self.selected_index else "dim")

            item = Static(text, classes=item_class)
            self.mount(item)

    def select_next(self):
        """Select next command"""
        if self.filtered_commands:
            self.selected_index = (self.selected_index + 1) % len(self.filtered_commands)
            self.render_suggestions()

    def select_previous(self):
        """Select previous command"""
        if self.filtered_commands:
            self.selected_index = (self.selected_index - 1) % len(self.filtered_commands)
            self.render_suggestions()

    def get_selected_command(self):
        """Get the currently selected command"""
        if self.filtered_commands and 0 <= self.selected_index < len(self.filtered_commands):
            return self.filtered_commands[self.selected_index]["name"]
        return None
