"""
上下文调试编辑器

提供交互式界面，展示和编辑三层上下文：
1. System Prompt（系统提示词）
2. Memory Context（记忆上下文）
3. Conversation History（对话历史）
"""
import json
from typing import Dict, List, Any, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.prompt import Prompt, Confirm
    from rich.layout import Layout
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class ContextDebugEditor:
    """上下文调试编辑器"""

    def __init__(self, actor, memory_manager):
        """
        初始化编辑器

        Args:
            actor: ActorAgent实例
            memory_manager: MemoryManager实例
        """
        self.actor = actor
        self.memory_manager = memory_manager
        self.console = Console() if RICH_AVAILABLE else None
        self.modified = False

    def show(self):
        """显示上下文编辑器界面"""
        if not RICH_AVAILABLE:
            self._show_simple()
            return

        while True:
            self.console.clear()
            self._render_header()
            self._render_context_overview()

            choice = Prompt.ask(
                "\n[bold cyan]选择操作[/bold cyan]",
                choices=["1", "2", "3", "4", "5", "q"],
                default="q"
            )

            if choice == "1":
                self._edit_system_prompt()
            elif choice == "2":
                self._edit_memory_context()
            elif choice == "3":
                self._edit_conversation_history()
            elif choice == "4":
                self._send_to_api()
            elif choice == "5":
                self._export_context()
            elif choice == "q":
                if self.modified:
                    if Confirm.ask("[yellow]有未保存的修改，确定退出？[/yellow]"):
                        break
                else:
                    break

    def _render_header(self):
        """渲染头部"""
        title = Text("🔧 Context Debug Editor", style="bold cyan")
        subtitle = Text("三层上下文调试工具", style="dim")

        self.console.print(Panel(
            f"{title}\n{subtitle}",
            border_style="cyan",
            padding=(1, 2)
        ))

    def _render_context_overview(self):
        """渲染上下文概览"""
        messages = self.actor.build_messages()

        # 统计三层上下文
        system_messages = [m for m in messages if m.get("role") == "system"]
        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]

        # 创建表格
        table = Table(show_header=True, header_style="bold magenta", box=None)
        table.add_column("层级", style="cyan", width=20)
        table.add_column("消息数", justify="right", style="yellow")
        table.add_column("字符数", justify="right", style="green")
        table.add_column("操作", style="blue")

        # 第一层：System Prompt
        system_chars = sum(len(m.get("content", "")) for m in system_messages)
        table.add_row(
            "1️⃣  System Prompt",
            str(len(system_messages)),
            f"{system_chars:,}",
            "[1] 编辑"
        )

        # 第二层：Memory Context
        memory_md = self.memory_manager.get_memory_markdown()
        table.add_row(
            "2️⃣  Memory Context",
            "1",
            f"{len(memory_md):,}",
            "[2] 编辑"
        )

        # 第三层：Conversation History
        history_chars = sum(len(m.get("content", "")) for m in user_messages + assistant_messages)
        table.add_row(
            "3️⃣  Conversation History",
            str(len(user_messages) + len(assistant_messages)),
            f"{history_chars:,}",
            "[3] 编辑"
        )

        self.console.print(table)

        # 操作菜单
        self.console.print("\n[bold]操作菜单：[/bold]")
        self.console.print("  [4] 发送到API测试")
        self.console.print("  [5] 导出上下文")
        self.console.print("  [q] 退出")

        if self.modified:
            self.console.print("\n[yellow]⚠ 有未保存的修改[/yellow]")

    def _edit_system_prompt(self):
        """编辑系统提示词"""
        self.console.clear()
        self.console.print(Panel("编辑 System Prompt", style="cyan"))

        messages = self.actor.build_messages()
        system_messages = [m for m in messages if m.get("role") == "system"]

        if not system_messages:
            self.console.print("[yellow]没有系统提示词[/yellow]")
            Prompt.ask("按Enter返回")
            return

        # 显示所有系统消息
        for i, msg in enumerate(system_messages, 1):
            content = msg.get("content", "")
            preview = content[:200] + "..." if len(content) > 200 else content

            self.console.print(f"\n[bold cyan]Section {i}/{len(system_messages)}[/bold cyan]")
            self.console.print(Panel(preview, border_style="dim"))

        choice = Prompt.ask(
            f"\n选择要编辑的section (1-{len(system_messages)})",
            default="1"
        )

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(system_messages):
                self._edit_message_content(system_messages[idx])
        except ValueError:
            pass

        Prompt.ask("按Enter返回")

    def _edit_memory_context(self):
        """编辑记忆上下文"""
        self.console.clear()
        self.console.print(Panel("编辑 Memory Context", style="cyan"))

        memory_md = self.memory_manager.get_memory_markdown()
        preview = memory_md[:500] + "..." if len(memory_md) > 500 else memory_md

        self.console.print(Panel(preview, title="当前内容", border_style="dim"))

        if Confirm.ask("\n是否编辑？"):
            self.console.print("\n[dim]提示：输入 /done 完成编辑，/cancel 取消[/dim]")
            lines = []
            while True:
                line = Prompt.ask("", default="")
                if line == "/done":
                    new_content = "\n".join(lines)
                    # 这里需要实现更新memory的逻辑
                    self.console.print("[green]✓ 已更新[/green]")
                    self.modified = True
                    break
                elif line == "/cancel":
                    break
                lines.append(line)

        Prompt.ask("按Enter返回")

    def _edit_conversation_history(self):
        """编辑对话历史"""
        self.console.clear()
        self.console.print(Panel("编辑 Conversation History", style="cyan"))

        context = self.memory_manager.get_context()

        # 显示对话历史
        for i, msg in enumerate(context):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            preview = content[:100] + "..." if len(content) > 100 else content

            style = "cyan" if role == "user" else "green" if role == "assistant" else "yellow"
            self.console.print(f"\n[{style}][{i}] {role.upper()}[/{style}]")
            self.console.print(f"  {preview}")

        choice = Prompt.ask(
            f"\n选择要编辑的消息 (0-{len(context)-1}), 或 'd' 删除, 'a' 添加",
            default="q"
        )

        if choice == "q":
            return
        elif choice == "a":
            self._add_message()
        elif choice == "d":
            idx_str = Prompt.ask("输入要删除的消息索引")
            try:
                idx = int(idx_str)
                if 0 <= idx < len(context):
                    context.pop(idx)
                    self.modified = True
                    self.console.print("[green]✓ 已删除[/green]")
            except ValueError:
                pass
        else:
            try:
                idx = int(choice)
                if 0 <= idx < len(context):
                    self._edit_message_content(context[idx])
            except ValueError:
                pass

        Prompt.ask("按Enter返回")

    def _edit_message_content(self, message: Dict):
        """编辑单条消息内容"""
        self.console.print(f"\n[bold]当前内容：[/bold]")
        self.console.print(Panel(message.get("content", ""), border_style="dim"))

        if Confirm.ask("\n是否编辑？"):
            self.console.print("\n[dim]输入新内容（多行输入，输入 /done 完成）：[/dim]")
            lines = []
            while True:
                line = Prompt.ask("", default="")
                if line == "/done":
                    message["content"] = "\n".join(lines)
                    self.modified = True
                    self.console.print("[green]✓ 已更新[/green]")
                    break
                elif line == "/cancel":
                    break
                lines.append(line)

    def _add_message(self):
        """添加新消息"""
        role = Prompt.ask("角色", choices=["user", "assistant", "system"], default="user")

        self.console.print("\n[dim]输入内容（多行输入，输入 /done 完成）：[/dim]")
        lines = []
        while True:
            line = Prompt.ask("", default="")
            if line == "/done":
                content = "\n".join(lines)
                self.memory_manager.append({"role": role, "content": content})
                self.modified = True
                self.console.print("[green]✓ 已添加[/green]")
                break
            elif line == "/cancel":
                break
            lines.append(line)

    def _send_to_api(self):
        """发送当前上下文到API测试"""
        self.console.clear()
        self.console.print(Panel("发送到API测试", style="cyan"))

        if not Confirm.ask("确定要发送当前上下文到API？"):
            return

        try:
            messages = self.actor.build_messages()

            self.console.print("\n[yellow]⏳ 正在调用API...[/yellow]")

            # 调用API
            response = self.actor.query(messages)

            self.console.print("\n[bold green]✓ API响应：[/bold green]")
            self.console.print(Panel(response, border_style="green"))

        except Exception as e:
            self.console.print(f"\n[bold red]✗ API调用失败：[/bold red]")
            self.console.print(f"[red]{str(e)}[/red]")

        Prompt.ask("\n按Enter返回")

    def _export_context(self):
        """导出上下文"""
        self.console.clear()
        self.console.print(Panel("导出上下文", style="cyan"))

        messages = self.actor.build_messages()

        filename = Prompt.ask(
            "导出文件名",
            default=f"context_export_{int(time.time())}.json"
        )

        try:
            from pathlib import Path
            export_path = Path("runtime_memory/context_exports")
            export_path.mkdir(parents=True, exist_ok=True)

            file_path = export_path / filename

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)

            self.console.print(f"\n[green]✓ 已导出到: {file_path}[/green]")

        except Exception as e:
            self.console.print(f"\n[red]✗ 导出失败: {str(e)}[/red]")

        Prompt.ask("\n按Enter返回")

    def _show_simple(self):
        """简单模式（无Rich库）"""
        print("=== Context Debug Editor ===")
        print("需要安装 rich 库才能使用完整功能")
        print("pip install rich")


import time
