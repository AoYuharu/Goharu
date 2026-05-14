"""
Selectable text viewer popup — 可选中复制文本的弹窗

用于解决 RichLog 无法鼠标选中文字的问题
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import TextArea, Static


class TextViewerScreen(ModalScreen):
    """显示文本的模态弹窗，支持鼠标选中 + Ctrl+C 复制"""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("ctrl+c", "copy_selection", "Copy", priority=True),
    ]

    DEFAULT_CSS = """
    TextViewerScreen {
        align: center middle;
    }

    #viewer-container {
        width: 90%;
        height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #viewer-title {
        height: 1;
        content-align: center middle;
        background: $accent;
        color: $text;
        margin-bottom: 1;
    }

    #viewer-hint {
        height: 1;
        content-align: center middle;
        color: $text-disabled;
        margin-top: 1;
    }

    #viewer-text {
        height: 1fr;
        border: solid $primary;
    }
    """

    def __init__(self, title: str = "", content: str = ""):
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        yield Container(
            Static(self._title, id="viewer-title"),
            TextArea(self._content, id="viewer-text", read_only=True, language=None),
            Static("🖱  Drag to select text  ·  Ctrl+C to copy  ·  Esc to close", id="viewer-hint"),
            id="viewer-container",
        )

    def on_mount(self):
        text_area = self.query_one("#viewer-text", TextArea)
        text_area.focus()

    def action_copy_selection(self):
        """Copy selected text (TextArea handles this natively with Ctrl+C)"""
        pass

    def action_dismiss(self):
        self.dismiss()
