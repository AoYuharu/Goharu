"""
Chat Panel Widget

Displays conversation history and input field with slash command support
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Input, RichLog
from textual.message import Message
from rich.text import Text
from rich.panel import Panel
from rich.markdown import Markdown
from datetime import datetime

from .command_suggestions import CommandSuggestions
from .prompt_cache import detect_and_cache
from .tool_panel import ToolPanel


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
            RichLog(id="chat-log", wrap=True, highlight=True, markup=True, auto_scroll=True, max_lines=5000),
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

        # Auto-cache long prompts (15+ chars) for /prompt-cache reuse
        detect_and_cache(message)

        # Add user message to display IMMEDIATELY
        self.add_user_message(message)

        # Then send to gateway (async, won't block UI)
        gateway = self.app.gateway
        try:
            # Use threading to avoid blocking
            import threading
            import logging
            _send_logger = logging.getLogger("TUI.SendMessage")

            def send_async():
                try:
                    gateway.call("agent.send_message", {
                        "message": message,
                        "session_id": self.app.session_id
                    })
                except Exception as e:
                    _send_logger.error(
                        "Failed to send message to gateway: %s (gateway process=%s)",
                        e,
                        gateway.process and gateway.process.pid,
                    )
                    # Attempt to restart gateway
                    try:
                        _send_logger.info("Attempting gateway restart...")
                        gateway.restart()
                        _send_logger.info("Gateway restarted, retrying message send...")
                        gateway.call("agent.send_message", {
                            "message": message,
                            "session_id": self.app.session_id
                        })
                    except Exception as retry_err:
                        _send_logger.critical(
                            "Gateway restart/retry also failed: %s", retry_err
                        )

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
            # Also clear tool panel (right side)
            try:
                self.app.query_one(ToolPanel).clear()
            except Exception:
                pass
            # Also clear on gateway (queued, executes between message batches)
            try:
                self.app.gateway.call("agent.clear_session", {"session_id": self.app.session_id})
            except:
                pass

        elif cmd == "/compact":
            # Summarize current conversation via LLM and replace with summary.
            # Queued for sequential execution between message batches —
            # result arrives asynchronously via command.complete event.
            try:
                result = self.app.gateway.call("agent.compact", {"session_id": self.app.session_id})
                if isinstance(result, dict) and result.get("queued"):
                    self.add_system_message("Compact queued — will execute after current tasks complete")
                else:
                    self.add_system_message("Conversation compacted — history summarized via LLM")
            except Exception as e:
                self.add_error_message(f"Compact failed: {e}")

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

        elif cmd == "/prompt-cache":
            # Browse & reuse cached long prompts
            from .prompt_cache import PromptCacheScreen, get_cached_prompts
            entries = get_cached_prompts()
            if not entries:
                self.add_system_message("Prompt cache is empty. Type a message of 15+ characters to start caching.")
                return

            def on_cache_done(text):
                if text:
                    self.chat_input.value = text
                    self.chat_input.focus()
                    self.chat_input.cursor_position = len(text)
                    self.add_system_message("📋 Prompt loaded into input — press Enter to send or edit first")
                else:
                    self.focus_input()

            self.app.push_screen(PromptCacheScreen(), on_cache_done)

        elif cmd == "/config":
            # Open interactive config editor
            from ..screens.config_editor import ConfigEditorScreen
            def on_config_editor_done(result):
                if result:
                    if result.get("saved"):
                        self.add_system_message(f"✅ {result.get('message', '配置已保存')}")
                        self.add_system_message("⚠️  部分配置需要重启应用才能生效")
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
            self.add_system_message("  /compact - Summarize conversation via LLM")
            self.add_system_message("  /config - Open interactive configuration editor")
            self.add_system_message("  /copy - Copy last assistant message")
            self.add_system_message("  /export - Export chat history to file")
            self.add_system_message("  /help - Show this help")
            self.add_system_message("  /prompt - Browse & edit prompt files")
            self.add_system_message("  /prompt-cache - Browse & reuse recent long prompts")
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

    # ── History replay with incremental rendering ──

    HISTORY_REPLAY_LIMIT = 5  # max recent messages to show on startup

    def replay_messages(self, messages: list):
        """Restore chat history — only user questions and AI answers, no tool noise.
        If more than HISTORY_REPLAY_LIMIT messages exist, only the most recent ones
        are shown, with a placeholder for older history."""
        if self._history_loaded:
            return
        self._history_loaded = True

        if not messages:
            self.add_system_message("--- No history to restore ---")
            return

        # Only keep user/assistant text messages (skip tool_calls, tool_results, thinking, context_only)
        chat_only = [
            m for m in messages
            if m.get("type") == "text"
            and m.get("role") in ("user", "assistant")
            and m.get("content", "")
            and m.get("_visibility", "full") != "context_only"
        ]
        if not chat_only:
            self.add_system_message("--- No history to restore ---")
            return

        total = len(chat_only)
        if total > self.HISTORY_REPLAY_LIMIT:
            skipped = total - self.HISTORY_REPLAY_LIMIT
            self.add_system_message(f"(更久远的对话... {skipped} messages)")
            chat_only = chat_only[-self.HISTORY_REPLAY_LIMIT:]

        self._replay_msgs = chat_only
        self._replay_idx = 0
        self._replay_count = 0
        self.add_system_message("Loading conversation history...")
        self._do_replay_step()

    @staticmethod
    def _parse_ts(msg: dict) -> str:
        """Extract HH:MM:SS from a message's timestamp field, or use current time."""
        raw = msg.get("timestamp", "")
        if raw:
            try:
                # ISO format: "2026-05-18T14:30:00" or "2026-05-18T14:30:00+08:00"
                ts = raw.replace("T", " ").split("+")[0].rstrip("Z")
                return datetime.fromisoformat(ts).strftime("%H:%M:%S")
            except (ValueError, TypeError):
                pass
        return datetime.now().strftime("%H:%M:%S")

    def _do_replay_step(self):
        """Render one chat message from the replay queue, then schedule the next."""
        if self._replay_idx >= len(self._replay_msgs):
            total = self._replay_count
            self.add_system_message(f"--- Restored {total} historical messages ---")
            return

        msg = self._replay_msgs[self._replay_idx]
        self._replay_idx += 1

        role = msg["role"]
        content = msg["content"]
        ts = self._parse_ts(msg)

        if role == "user":
            text = Text()
            text.append(f"[{ts}] ", style="dim")
            text.append("You", style="bold cyan")
            text.append(": ")
            text.append(content)
            self.chat_log.write(text)
            self.chat_history.append(f"[{ts}] You: {content}")
            self._replay_count += 1

        elif role == "assistant":
            self.last_assistant_message = content
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
            self._replay_count += 1

        # Schedule next step — 60ms gap for a natural typing feel
        self.set_timer(0.06, self._do_replay_step)

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

    def add_system_message(self, message: str):
        """Add a system message to the chat log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append("System", style="bold yellow")
        text.append(": ")
        text.append(message, style="italic")
        self.chat_log.write(text)

    def add_splash_message(self, message: str, style: str = ""):
        """Add a splash/branding message without role prefix or timestamp.
        Not recorded in chat history. Used for startup branding only."""
        if style:
            self.chat_log.write(Text(message, style=style))
        else:
            self.chat_log.write(Text(message))

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
