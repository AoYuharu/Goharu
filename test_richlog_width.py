#!/usr/bin/env python3
"""
测试RichLog宽度问题
"""

from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static
from textual.containers import Container
from rich.text import Text
from rich.markdown import Markdown

class TestApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    RichLog {
        height: 1fr;
        width: 100%;
        border: solid green;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("测试RichLog宽度", id="header")
        yield RichLog(wrap=True, highlight=True, markup=True)

    def on_mount(self):
        log = self.query_one(RichLog)

        # 测试1: 纯文本
        log.write(Text("=" * 100))
        log.write(Text("测试1: 这是一个很长的文本 " * 10))
        log.write("")

        # 测试2: Markdown
        log.write(Text("=" * 100))
        md_text = """# 测试Markdown

这是一个很长的段落 """ + "测试文本 " * 20 + """

- 列表项1
- 列表项2
- 列表项3

**粗体文本** 和 *斜体文本*
"""
        md = Markdown(md_text)
        log.write(md)
        log.write("")

        # 测试3: 检查内部属性
        log.write(Text(f"RichLog size: {log.size}"))
        log.write(Text(f"RichLog width: {log.size.width}"))
        if hasattr(log, '_console'):
            log.write(Text(f"Console width: {log._console.width}"))

if __name__ == "__main__":
    app = TestApp()
    app.run()
