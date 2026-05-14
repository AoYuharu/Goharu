#!/usr/bin/env python3
"""
测试TUI显示问题修复
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

def test_markdown_rendering():
    """测试Markdown渲染"""
    console = Console()

    print("=" * 60)
    print("测试1: Markdown渲染")
    print("=" * 60)

    test_text = """Hello! 👋

I'm ready to help you with:
- **File operations** (read, write, edit)
- **Command execution** (run_cmd)
- **Code analysis**

Let me know what you need!"""

    md = Markdown(test_text)
    console.print(md)

    print("\n" + "=" * 60)
    print("测试2: 宽度测试")
    print("=" * 60)

    long_text = "这是一个很长的文本 " * 20
    console.print(Text(long_text))

    print("\n" + "=" * 60)
    print("测试3: 流式输出模拟")
    print("=" * 60)

    chunks = ["Hello", "! 👋\n\n", "I'm ", "ready", " to ", "help!"]
    accumulated = ""

    for chunk in chunks:
        accumulated += chunk
        console.print(Text(chunk), end="")

    print("\n\n流式输出完成，现在渲染Markdown:")
    md = Markdown(accumulated)
    console.print(md)

if __name__ == "__main__":
    test_markdown_rendering()
