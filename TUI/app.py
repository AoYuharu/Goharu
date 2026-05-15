"""
TableHelper TUI Application

Main Textual application with full-screen interface
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Input, RichLog
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from datetime import datetime
import queue

from .gateway_client import GatewayClient
from .widgets.chat_panel import ChatPanel
from .widgets.status_bar import StatusBar
from .widgets.tool_panel import ToolPanel


class TableHelperTUI(App):
    """TableHelper TUI Application"""

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        dock: top;
    }

    #main-content {
        layout: horizontal;
        height: 1fr;
        width: 100%;
    }

    #chat-panel {
        width: 2fr;
        border: solid $primary;
        max-width: 100%;
    }

    #tool-panel {
        width: 1fr;
        border: solid $accent;
    }

    #input-container {
        height: 3;
        background: $surface;
        padding: 0 1;
    }

    Input {
        width: 100%;
    }

    RichLog {
        width: 100%;
    }

    .thinking {
        color: $warning;
        text-style: italic;
    }

    .tool-call {
        color: $accent;
        text-style: bold;
    }

    .error {
        color: $error;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+h", "toggle_help", "Help"),
        Binding("ctrl+shift+c", "copy_last_reply", "Copy last reply"),
        Binding("escape", "interrupt", "Interrupt", priority=True),
    ]

    def action_copy_last_reply(self):
        """Copy the last assistant response to clipboard"""
        chat_panel = self.query_one("#chat-panel")
        chat_panel.action_copy_last()

    def __init__(self):
        super().__init__()
        self.gateway = GatewayClient()
        self.session_id = "default"
        self._answer_rendered = False  # Track if answer was already rendered via agent.answer event

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Static("🤖 TableHelper TUI", id="header")
        yield Horizontal(
            ChatPanel(id="chat-panel"),
            ToolPanel(id="tool-panel"),
            id="main-content"
        )
        yield StatusBar(id="status-bar")

    def on_mount(self):
        """Called when app starts"""
        # Store reference to ToolPanel's thread-safe event queue
        # (used by reader-thread handlers to push tool events without call_from_thread)
        tool_panel = self.query_one(ToolPanel)
        self._tool_event_queue = tool_panel._event_queue

        # Start gateway
        self.gateway.start()

        # Subscribe to gateway events
        self.gateway.on_event("gateway.ready", self._on_gateway_ready)
        self.gateway.on_event("gateway.init_error", self._on_gateway_init_error)
        self.gateway.on_event("agent.ready", self._on_agent_ready)
        self.gateway.on_event("agent.thinking", self._on_agent_thinking)
        self.gateway.on_event("agent.thinking_content", self._on_agent_thinking_content)
        self.gateway.on_event("agent.step", self._on_agent_step)
        self.gateway.on_event("agent.answer", self._on_agent_answer)
        self.gateway.on_event("agent.streaming", self._on_agent_streaming)
        self.gateway.on_event("tool.call", self._on_tool_call)
        self.gateway.on_event("tool.result", self._on_tool_result)
        self.gateway.on_event("token.stats", self._on_token_stats)
        self.gateway.on_event("user.batch", self._on_user_batch)
        self.gateway.on_event("user.merge", self._on_user_merge)
        self.gateway.on_event("message.complete", self._on_message_complete)
        self.gateway.on_event("context.micro_compact", self._on_micro_compact)
        self.gateway.on_event("task.background.started", self._on_background_started)
        self.gateway.on_event("task.background.completed", self._on_background_completed)
        self.gateway.on_event("task.background.status", self._on_background_status)
        self.gateway.on_event("agent.interrupted", self._on_agent_interrupted)
        self.gateway.on_event("agent.error", self._on_agent_error)

        # Show welcome message
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message("Welcome to TableHelper TUI!")
        chat_panel.add_system_message("Type your message in the input box below and press Enter.")
        chat_panel.add_system_message("Press Tab to switch focus between panels.")
        chat_panel.add_system_message("Waiting for Gateway to start...")

        # Focus input
        self.query_one(ChatPanel).focus_input()

    def _on_gateway_init_error(self, payload):
        """Gateway failed to initialize - show fatal error"""
        error_msg = payload.get("message", "Unknown initialization error")

        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"FATAL: {error_msg}", "error")

        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message("══════════════════════════════════════════════")
        chat_panel.add_error_message(f"Gateway failed to start: {error_msg}")
        chat_panel.add_system_message("Please check your config.yaml and restart the application.")
        chat_panel.add_system_message("The gateway process has exited. No message processing is available.")
        chat_panel.add_system_message("══════════════════════════════════════════════")

    def _on_gateway_ready(self, payload):
        """Gateway is ready"""
        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Gateway ready - You can start chatting!", "success")

        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message("Gateway is ready! You can now send messages.")

        # 加载历史消息（异步，避免阻塞 UI）
        import threading
        def load_history_async():
            try:
                result = self.gateway.call("agent.get_history", {})
                messages = result.get("messages", [])
                if messages:
                    # Use call_from_thread to safely update UI from background thread
                    self.call_from_thread(chat_panel.replay_messages, messages)
            except Exception as e:
                # Use call_from_thread to safely update UI from background thread
                self.call_from_thread(chat_panel.add_system_message, f"(历史记录加载失败: {e})")

        thread = threading.Thread(target=load_history_async, daemon=True)
        thread.start()

    def _on_agent_ready(self, payload):
        """Agent is ready"""
        status_bar = self.query_one(StatusBar)
        tools = payload.get("tools", [])
        status_bar.set_status(f"Ready • {len(tools)} tools", "success")

        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"Agent ready with {len(tools)} tools")

    def _on_agent_thinking(self, payload):
        """Agent is thinking"""
        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Thinking...", "thinking")

        # Start streaming message
        chat_panel = self.query_one(ChatPanel)
        chat_panel.start_assistant_message()

    def _on_agent_thinking_content(self, payload):
        """Agent thinking content (called from gateway reader thread)"""
        step = payload.get("step", 0)
        thinking = payload.get("thinking", "")

        if thinking:
            self.call_from_thread(self._do_add_thinking, thinking, step)

    def _do_add_thinking(self, thinking: str, step: int):
        """Display thinking in chat (runs on main thread)"""
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_thinking_message(thinking, step)

    def _on_agent_step(self, payload):
        """Agent is processing a step"""
        step = payload.get("step", 0)
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"Processing step {step}...", "thinking")

    def _on_agent_answer(self, payload):
        """Agent has complete answer (non-streaming)"""
        answer = payload.get("answer", "")
        if answer:
            chat_panel = self.query_one(ChatPanel)
            chat_panel.add_assistant_message(answer)
            self._answer_rendered = True

    def _on_agent_streaming(self, payload):
        """Agent is streaming response"""
        chunk = payload.get("chunk", "")
        if chunk:
            chat_panel = self.query_one(ChatPanel)
            chat_panel.append_to_assistant_message(chunk)

    def _on_tool_call(self, payload):
        """Tool call started (called from gateway reader thread)"""
        tool_name = payload.get("tool", "unknown")
        step = payload.get("step", 0)
        arguments = payload.get("arguments", {})
        call_id = payload.get("call_id", "")

        # Push to thread-safe queue — ToolPanel._tick() drains it on the main thread
        self._tool_event_queue.put(("call", {
            "tool_name": tool_name,
            "arguments": arguments,
            "step": step,
            "call_id": call_id,
        }))

        # Update status bar (lightweight, via call_from_thread is fine)
        self.call_from_thread(self._set_status, f"Calling {tool_name}...", "working")

    def _do_tool_call(self, tool_name: str, arguments: dict, step: int):
        """Handle tool call on main thread"""
        tool_panel = self.query_one(ToolPanel)
        tool_panel.add_tool_call(tool_name, arguments, step)

    def _set_status(self, text: str, style: str = ""):
        """Set status bar text (runs on main thread)"""
        status_bar = self.query_one(StatusBar)
        if style:
            status_bar.set_status(text, style)
        else:
            status_bar.set_status(text)

    def _on_tool_result(self, payload):
        """Tool call completed (called from gateway reader thread)"""
        tool_name = payload.get("tool", "unknown")
        result = payload.get("result", "")
        call_id = payload.get("call_id", "")

        # Push to thread-safe queue — ToolPanel._tick() drains it on the main thread
        self._tool_event_queue.put(("result", {
            "tool_name": tool_name,
            "result": result,
            "call_id": call_id,
        }))

        # Update status bar (lightweight, via call_from_thread is fine)
        self.call_from_thread(self._set_status, "Tool finished", "success")

    def _do_tool_result(self, tool_name: str, result: str):
        """Handle tool result on main thread"""
        tool_panel = self.query_one(ToolPanel)
        tool_panel.add_tool_result(tool_name, result)

    def _on_token_stats(self, payload):
        """Token statistics update"""
        from Agent.TokenEstimator import TokenEstimator

        current_tokens = payload.get("current_tokens")
        prompt_cache_ratio = payload.get("prompt_cache_ratio")

        if current_tokens is not None or prompt_cache_ratio is not None:
            current_tokens = int(current_tokens or 0)
            prompt_cache_ratio = float(prompt_cache_ratio or 0.0)
            stats = {
                "current_tokens": TokenEstimator.format(current_tokens),
                "prompt_cache_ratio": f"{prompt_cache_ratio:.1%}",
            }
        else:
            level1 = payload.get("level1", 0)
            level2 = payload.get("level2", 0)
            level3 = payload.get("level3", 0)
            total = payload.get("total", 0)
            latest = payload.get("latest_turn", 0)
            cached = payload.get("cached", level1 + level2 + level3)
            ratio = (cached / total) if total else 0.0
            stats = {
                "current_tokens": TokenEstimator.format(latest or total),
                "prompt_cache_ratio": f"{ratio:.1%}",
            }

        status_bar = self.query_one(StatusBar)
        status_bar.set_stats(stats)

    def _on_message_complete(self, payload):
        """Message processing complete"""
        # Skip if answer was already rendered by _on_agent_answer
        if self._answer_rendered:
            self._answer_rendered = False
        else:
            answer = payload.get("answer", "")
            if answer:
                chat_panel = self.query_one(ChatPanel)
                chat_panel.add_assistant_message(answer)

        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Ready", "success")

    def _on_user_batch(self, payload):
        """User messages were batched at start"""
        count = payload.get("count", 0)
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"📦 Batched {count} messages into one context")

    def _on_user_merge(self, payload):
        """User messages were merged during processing"""
        count = payload.get("count", 0)
        merged = payload.get("merged", "")
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"🔄 Merged {count} new message(s) before next reasoning step")

    def _on_agent_error(self, payload):
        """Agent error occurred"""
        error_msg = payload.get("message", "Unknown error")

        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_error_message(error_msg)

        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"Error: {error_msg[:50]}", "error")

    def action_quit(self):
        """Quit the application"""
        self.gateway.stop()
        self.exit()

    def action_interrupt(self):
        """Interrupt current agent processing"""
        import threading

        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Interrupting...", "warning")

        def do_interrupt():
            try:
                result = self.gateway.call("agent.interrupt", {})
                if result and not result.get("interrupted"):
                    # Agent is idle — nothing to interrupt
                    self.call_from_thread(status_bar.set_status, "Ready", "success")
            except Exception:
                self.call_from_thread(status_bar.set_status, "Ready", "success")

        thread = threading.Thread(target=do_interrupt, daemon=True)
        thread.start()

    def action_clear(self):
        """Clear the chat"""
        chat_panel = self.query_one(ChatPanel)
        chat_panel.clear()

        # Clear session on gateway
        self.gateway.call("agent.clear_session", {"session_id": self.session_id})

    def action_toggle_help(self):
        """Toggle help overlay"""
        # TODO: Implement help overlay
        pass

    def _on_micro_compact(self, payload):
        """Micro-compact: older tool results collapsed into placeholder"""
        removed = payload.get("removed_results", 0)
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(
            f"📦 Micro-compact: {removed} older tool results compacted"
        )

    def _on_background_started(self, payload):
        """Background task started"""
        tool_calls = payload.get("tool_calls", [])
        bg_names = [tc.get("tool_name", "?") for tc in tool_calls]
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(
            f"🔧 Background task(s) started: {', '.join(bg_names)}"
        )
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"BG task: {', '.join(bg_names)}", "working")

    def _on_background_completed(self, payload):
        """Background task completed, results injected"""
        count = payload.get("count", 0)
        task_ids = payload.get("task_ids", [])
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(
            f"📥 {count} background task(s) completed (IDs: {task_ids})"
        )
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"BG tasks completed: {count}", "success")

    def _on_background_status(self, payload):
        """Background task status update"""
        pending = payload.get("pending_results", 0)
        if pending > 0:
            status_bar = self.query_one(StatusBar)
            status_bar.set_status(f"BG: {pending} pending", "working")

    def _on_agent_interrupted(self, payload):
        """Agent was interrupted by user"""
        message = payload.get("message", "请求已被中断")
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"⚠ {message}")

        # Clear stale tool indicators
        tool_panel = self.query_one(ToolPanel)
        tool_panel.clear()

        status_bar = self.query_one(StatusBar)
        status_bar.set_status("What should I do?", "thinking")

        # Focus input so user can type immediately
        self.query_one(ChatPanel).focus_input()


def run_tui():
    """Run the TUI application"""
    app = TableHelperTUI()
    app.run()


if __name__ == "__main__":
    run_tui()
