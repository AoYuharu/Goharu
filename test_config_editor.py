"""
Test script for the interactive config editor

This script tests the config editor screen in isolation
"""

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer
from TUI.screens.config_editor import ConfigEditorScreen


class TestConfigApp(App):
    """Test application for config editor"""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self):
        """Show config editor on mount"""
        def on_result(result):
            if result:
                if result.get("saved"):
                    print(f"✅ Configuration saved: {result.get('message')}")
                elif "error" in result:
                    print(f"❌ Error: {result['error']}")
                else:
                    print(f"ℹ️  {result.get('message', 'Cancelled')}")
            self.exit()

        self.push_screen(ConfigEditorScreen(), on_result)


if __name__ == "__main__":
    app = TestConfigApp()
    app.run()
