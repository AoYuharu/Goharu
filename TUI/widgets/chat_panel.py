"""
Chat Panel Widget

Displays conversation history and input field with slash command support
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Input, RichLog, Static
from textual.message import Message
from rich.text import Text
from rich.panel import Panel
from rich.markdown import Markdown
from datetime import datetime

from .command_suggestions import CommandSuggestions


class ChatPanel(Container):
    """Chat panel with message history and input"""

    BINDINGS = [
        (Binding("ctrl+shift+c", "copy_last", "Copy last reply", priority=True)),
        (Binding("f2", "view_last", "View selectable text", priority=True)),
    ]

    def action_copy_last(self):
        """Copy the last assistant response to clipboard"""
        if self.last_assistant_message:
            try:
                import pyperclip
                pyperclip.copy(self.last_assistant_message)
                self.add_system_message("✅ Copied last assistant response to clipboard")
            except Exception as e:
                self.add_error_message(f"Failed to copy: {e}")
        else:
            self.add_system_message("No assistant message to copy")

    def action_view_last(self):
        """Open a selectable popup viewer for the last assistant response"""
        if not self.last_assistant_message:
            self.add_system_message("No assistant message to view")
            return
        from ..screens.text_viewer import TextViewerScreen
        self.app.push_screen(
            TextViewerScreen(
                title="Agent Response (F2: select & copy text)",
                content=self.last_assistant_message,
            )
        )

    DEFAULT_CSS = """
    ChatPanel {
        layout: vertical;
        width: 100%;
    }

    #chat-log {
        height: 1fr;
        border: solid $primary;
        width: 100%;
        max-width: 100%;
    }

    #input-container {
        height: 3;
        background: $surface;
        padding: 0 1;
        width: 100%;
    }

    #chat-input {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Vertical(
            RichLog(id="chat-log", wrap=True, highlight=True, markup=True, auto_scroll=True),
            CommandSuggestions(id="command-suggestions"),
            Container(
                Input(placeholder="Type your message or / for commands...", id="chat-input"),
                id="input-container"
            )
        )

    def on_mount(self):
        """Called when widget is mounted"""
        self.chat_log = self.query_one("#chat-log", RichLog)
        self.chat_input = self.query_one("#chat-input", Input)
        self.command_suggestions = self.query_one("#command-suggestions", CommandSuggestions)
        self.current_assistant_message = None  # Track streaming message
        self.streaming_line_count = 0  # Track how many lines were written during streaming
        self.streaming_text = Text()  # Accumulate streaming chunks
        self.last_assistant_message = ""  # Store last assistant message for copying
        self.chat_history = []  # Store all messages for export
        self._history_loaded = False  # Whether history has been restored

    def on_input_changed(self, event: Input.Changed):
        """Handle input changes for command suggestions"""
        if event.input.id != "chat-input":
            return

        value = event.value

        # Check if user is typing a command
        if value.startswith("/"):
            # Show command suggestions
            self.command_suggestions.show_suggestions(value)
        else:
            # Hide suggestions
            self.command_suggestions.hide_suggestions()

    def on_key(self, event):
        """Handle key presses for command navigation"""
        # Only handle if suggestions are visible
        if not self.command_suggestions.has_class("visible"):
            return

        if event.key == "down":
            self.command_suggestions.select_next()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            self.command_suggestions.select_previous()
            event.prevent_default()
            event.stop()
        elif event.key == "tab":
            # Auto-complete selected command
            selected = self.command_suggestions.get_selected_command()
            if selected:
                self.chat_input.value = selected + " "
                self.command_suggestions.hide_suggestions()
                # Keep focus on input
                self.chat_input.focus()
            event.prevent_default()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted):
        """Handle input submission"""
        if event.input.id != "chat-input":
            return

        message = event.value.strip()
        if not message:
            return

        # Hide command suggestions
        self.command_suggestions.hide_suggestions()

        # Clear input immediately
        event.input.value = ""

        # Check if it's a command
        if message.startswith("/"):
            self.handle_command(message)
            return

        # Add user message to display IMMEDIATELY
        self.add_user_message(message)

        # Then send to gateway (async, won't block UI)
        gateway = self.app.gateway
        try:
            # Use threading to avoid blocking
            import threading
            def send_async():
                try:
                    gateway.call("agent.send_message", {
                        "message": message,
                        "session_id": self.app.session_id
                    })
                except Exception as e:
                    # Error will be handled by gateway error event
                    pass

            thread = threading.Thread(target=send_async, daemon=True)
            thread.start()
        except Exception as e:
            self.add_error_message(f"Failed to send message: {e}")

    def handle_command(self, command: str):
        """Handle slash commands"""
        cmd = command.lower().split()[0]

        if cmd == "/clear":
            self.clear()
            self.add_system_message("Chat history cleared")
            self.chat_history = []
            # Also clear on gateway
            try:
                self.app.gateway.call("agent.clear_session", {"session_id": self.app.session_id})
            except:
                pass

        elif cmd == "/copy":
            # Copy last assistant message to clipboard
            if self.last_assistant_message:
                try:
                    import pyperclip
                    pyperclip.copy(self.last_assistant_message)
                    self.add_system_message("Last assistant message copied to clipboard!")
                except ImportError:
                    # Fallback: save to file
                    with open("last_message.txt", "w", encoding="utf-8") as f:
                        f.write(self.last_assistant_message)
                    self.add_system_message("Last message saved to last_message.txt (pyperclip not installed)")
                except Exception as e:
                    self.add_error_message(f"Failed to copy: {e}")
            else:
                self.add_system_message("No assistant message to copy")

        elif cmd == "/export":
            # Export chat history to file
            try:
                from datetime import datetime
                filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    for msg in self.chat_history:
                        f.write(f"{msg}\n\n")
                self.add_system_message(f"Chat history exported to {filename}")
            except Exception as e:
                self.add_error_message(f"Failed to export: {e}")

        elif cmd == "/prompt":
            # Open prompt editor
            from .prompt_editor import PromptListScreen
            def on_prompt_editor_done(result):
                if result:
                    if result.get("success"):
                        self.add_system_message(f"✅ Saved: {result['name']} ({result['file']})")
                    elif "error" in result:
                        self.add_error_message(f"Failed to save {result['name']}: {result.get('error', '')}")
                # Refocus input after returning
                self.focus_input()

            self.app.push_screen(PromptListScreen(), on_prompt_editor_done)

        elif cmd == "/config":
            # Open interactive config editor
            from ..screens.config_editor import ConfigEditorScreen
            def on_config_editor_done(result):
                if result:
                    if result.get("saved"):
                        self.add_system_message(f"✅ {result.get('message', '配置已保存')}")
                        self.add_system_message("⚠️  部分配置需要重启 Gateway 才能生效")
                    elif "error" in result:
                        self.add_error_message(f"配置编辑失败: {result['error']}")
                    else:
                        self.add_system_message(result.get("message", "已取消"))
                # Refocus input after returning
                self.focus_input()

            self.app.push_screen(ConfigEditorScreen(), on_config_editor_done)

        elif cmd == "/help":
            self.add_system_message("Available commands:")
            self.add_system_message("  /clear - Clear chat history")
            self.add_system_message("  /config - Open interactive configuration editor")
            self.add_system_message("  /copy - Copy last assistant message")
            self.add_system_message("  /export - Export chat history to file")
            self.add_system_message("  /help - Show this help")
            self.add_system_message("  /prompt - Browse & edit prompt files")
            self.add_system_message("  /exit or /quit - Exit application")
            self.add_system_message("\nKeyboard shortcuts:")
            self.add_system_message("  Ctrl+C - Exit")
            self.add_system_message("  Ctrl+L - Clear chat")
            self.add_system_message("  Ctrl+Shift+C - Copy last agent reply")
            self.add_system_message("  F2 - Open selectable text viewer (mouse select + copy)")
            self.add_system_message("  Tab - Switch focus / Auto-complete command")

        elif cmd in ["/exit", "/quit"]:
            self.app.exit()

        else:
            self.add_error_message(f"Unknown command: {cmd}")
            self.add_system_message("Type /help for available commands")

    def replay_messages(self, messages: list):
        """Restore chat history in the display — 像续上对话一样自然回放"""
        if self._history_loaded:
            return
        self._history_loaded = True

        if not messages:
            return

        tool_panel = None
        try:
            tool_panel = self.app.query_one("#tool-panel")
        except Exception:
            pass

        restored_count = 0
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            msg_type = msg.get("type", "text")
            if not content:
                continue

            if msg_type == "tool_call":
                # 工具调用在正常交互时不显示，跳过
                restored_count += 1
                continue

            if msg_type == "tool_result":
                # 工具结果路由到右侧 ToolPanel
                if tool_panel:
                    # 从内容中尝试提取工具名（取首行前30字符做标题）
                    tool_label = content.split("\n")[0][:30] if content else "result"
                    tool_panel.add_tool_result(tool_label, content)
                else:
                    self.add_system_message(f"📋 {content[:200]}")
                restored_count += 1
                continue

            if role == "user":
                self.add_user_message(content)
                self.chat_history.append(f"[history] You: {content}")
                restored_count += 1

            elif role == "assistant":
                # 像正常对话一样渲染 assistant 回复（markdown 格式）
                self.last_assistant_message = content
                from datetime import datetime
                ts = datetime.now().strftime("%H:%M:%S")
                self.chat_history.append(f"[{ts}] Assistant:\n{content}")

                separator = Text("─" * 80, style="bold cyan")
                self.chat_log.write(separator)

                header = Text()
                header.append(f"[{ts}] ", style="dim")
                header.append("Assistant", style="bold green")
                header.append(":")
                self.chat_log.write(header)

                try:
                    from rich.markdown import Markdown
                    self.chat_log.write(Markdown(content))
                except Exception:
                    self.chat_log.write(Text(content))

                self.chat_log.write(separator)
                restored_count += 1

        if restored_count > 0:
            self.add_system_message(f"--- 已恢复 {restored_count} 条历史对话 ---")
        else:
            self.add_system_message("--- 无历史消息 ---")

    def add_user_message(self, message: str):
        """Add a user message to the chat log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append("You", style="bold cyan")
        text.append(": ")
        text.append(message)
        self.chat_log.write(text)

        # Store in history
        self.chat_history.append(f"[{timestamp}] You: {message}")

    def add_assistant_message(self, message: str):
        """Add an assistant message to the chat log with visual separators"""
        # Full-width separator line
        separator = Text("─" * 80, style="bold cyan")

        # If we have accumulated streaming content, flush any remaining text and render markdown
        if self.current_assistant_message:
            # Store the message
            self.last_assistant_message = self.current_assistant_message
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.chat_history.append(f"[{timestamp}] Assistant:\n{self.current_assistant_message}")

            # Flush any remaining streaming text
            if self.streaming_text and len(self.streaming_text.plain) > 0:
                self.chat_log.write(self.streaming_text)
                self.streaming_text = Text()

            # Separator before
            self.chat_log.write(separator)

            # Render markdown
            try:
                md = Markdown(self.current_assistant_message)
                self.chat_log.write(md)
            except Exception as e:
                self.chat_log.write(Text(self.current_assistant_message))

            # Separator after
            self.chat_log.write(separator)
        else:
            # Non-streaming message, render normally
            self.last_assistant_message = message
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.chat_history.append(f"[{timestamp}] Assistant:\n{message}")

            # Separator before
            self.chat_log.write(separator)

            text = Text()
            text.append(f"[{timestamp}] ", style="dim")
            text.append("Assistant", style="bold green")
            text.append(": ")
            self.chat_log.write(text)

            # Render as markdown
            try:
                md = Markdown(message)
                self.chat_log.write(md)
            except Exception as e:
                self.chat_log.write(Text(message))

            # Separator after
            self.chat_log.write(separator)

        self.current_assistant_message = None  # Reset streaming state
        self.streaming_line_count = 0
        self.streaming_text = Text()

    def start_assistant_message(self):
        """Start a new streaming assistant message"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append("Assistant", style="bold green")
        text.append(": ")
        self.chat_log.write(text)
        self.current_assistant_message = ""
        self.streaming_line_count = 1  # Count the header line
        self.streaming_text = Text()  # Accumulate text for current line

    def append_to_assistant_message(self, chunk: str):
        """Append a chunk to the current streaming message"""
        if self.current_assistant_message is None:
            self.start_assistant_message()

        self.current_assistant_message += chunk

        # Accumulate chunks into a single Text object
        self.streaming_text.append(chunk)

        # Flush on newline or when accumulated enough text
        accumulated_text = self.streaming_text.plain

        if '\n' in chunk or len(accumulated_text) > 100:
            self.chat_log.write(self.streaming_text)
            self.streaming_text = Text()  # Reset for next batch
            self.streaming_line_count += 1

    def add_system_message(self, message: str):
        """Add a system message to the chat log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append("System", style="bold yellow")
        text.append(": ")
        text.append(message, style="italic")
        self.chat_log.write(text)

    def add_error_message(self, message: str):
        """Add an error message to the chat log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append("Error", style="bold red")
        text.append(": ")
        text.append(message, style="red")
        self.chat_log.write(text)

    def add_thinking_message(self, thinking: str, step: int):
        """Add agent thinking content to the chat log"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Header
        header = Text()
        header.append(f"[{timestamp}] ", style="dim")
        header.append("💭 Thinking", style="bold cyan")
        header.append(f" (Step {step})", style="dim")
        self.chat_log.write(header)

        # Thinking content with indentation
        thinking_text = Text()
        for line in thinking.split('\n'):
            thinking_text.append(f"  {line}\n", style="italic yellow")
        self.chat_log.write(thinking_text)

        # Empty line for spacing
        self.chat_log.write("")

    def clear(self):
        """Clear the chat log"""
        self.chat_log.clear()

    def focus_input(self):
        """Focus the input field"""
        self.chat_input.focus()
