"""
TableHelper TUI Application

Main Textual application with full-screen interface
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Input, RichLog
from textual.binding import Binding
from textual import events
from rich.text import Text
from rich.panel import Panel
from rich.style import Style
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
import os
import queue
import threading
import time as _time
from pathlib import Path

from .gateway_client import GatewayClient
from .widgets.chat_panel import ChatPanel
from .widgets.status_bar import StatusBar
from .widgets.tool_panel import ToolPanel

# TUI 侧日志
_TUI_LOG_BASE = Path(__file__).parent.parent / "runtime_memory" / "logs" / "tui"
_TUI_LOG_BASE.mkdir(parents=True, exist_ok=True)
_tui_handler = logging.FileHandler(
    _TUI_LOG_BASE / "app.log",
    encoding="utf-8",
)
_tui_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
_tui_log = logging.getLogger("TUI.App")
_tui_log.setLevel(logging.DEBUG)
_tui_log.addHandler(_tui_handler)
_tui_log.propagate = False

_TRACE_PATH = _TUI_LOG_BASE / "trace.log"


def _trace(msg: str):
    """直接写文件，绕过 logging 缓冲和线程问题。用于关键关口的取证。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.")
        ts += f"{datetime.now().microsecond // 1000:03d}"
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} [TRACE] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


_MEMORY_MONITOR_INTERVAL = 30  # 每30秒记录一次内存


class _TypewriterHeader(Static):
    """Header widget that displays text character by character with a typing animation."""

    def __init__(self, text: str, interval: float = 0.04, **kwargs):
        kwargs.setdefault("id", "header")
        super().__init__("", **kwargs)
        self._full_text = text
        self._type_interval = interval
        self._pos = 0
        self._timer = None

    def on_mount(self):
        self._timer = self.set_interval(self._type_interval, self._type_next_char)

    def _type_next_char(self):
        if self._pos < len(self._full_text):
            self._pos += 1
            revealed = self._full_text[:self._pos]
            self.update(Text(revealed, style=Style(bold=True)))
        else:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None

    def set_text(self, text: str):
        """Update the header text and restart the typing animation."""
        self._full_text = text
        self._pos = 0
        self.update("")
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_interval(self._type_interval, self._type_next_char)


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
        yield _TypewriterHeader("Welcome back — Goharu is ready. What can I help you with?")
        yield Horizontal(
            ChatPanel(id="chat-panel"),
            ToolPanel(id="tool-panel"),
            id="main-content"
        )
        yield StatusBar(id="status-bar")

    def on_mount(self):
        """Called when app starts"""
        _tui_log.info("TUI app mounting...")

        # Store reference to ToolPanel's thread-safe event queue
        # (used by reader-thread handlers to push tool events without call_from_thread)
        tool_panel = self.query_one(ToolPanel)
        self._tool_event_queue = tool_panel._event_queue

        # ── Thread-safe queues for reader thread → main thread ──
        # Streaming chunks (VERY high frequency, don't use call_from_thread)
        self._stream_queue = queue.Queue()
        # Status bar updates (medium frequency)
        self._status_queue = queue.Queue()

        # Start gateway
        self.gateway.start()

        # Subscribe to gateway events
        # NOTE: All handlers below are called from the gateway reader thread.
        # Textual is NOT thread-safe — handlers must NOT touch widgets directly.
        # Use call_from_thread() or thread-safe queues instead.
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
        self.gateway.on_event("command.complete", self._on_command_complete)

        # ── Goharu splash ──
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_splash_message("")
        chat_panel.add_splash_message("  ██████╗  ██████╗ ██╗  ██╗ █████╗ ██████╗ ██╗   ██╗", "bold cyan")
        chat_panel.add_splash_message("  ██╔════╝ ██╔═══██╗██║  ██║██╔══██╗██╔══██╗██║   ██║", "bold cyan")
        chat_panel.add_splash_message("  ██║  ███╗██║   ██║███████║███████║██████╔╝██║   ██║", "bold blue")
        chat_panel.add_splash_message("  ██║   ██║██║   ██║██╔══██║██╔══██║██╔══██╗██║   ██║", "bold blue")
        chat_panel.add_splash_message("  ╚██████╔╝╚██████╔╝██║  ██║██║  ██║██║  ██║╚██████╔╝", "bold magenta")
        chat_panel.add_splash_message("   ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝", "bold magenta")
        chat_panel.add_splash_message("")
        chat_panel.add_splash_message("  Your Personal Coding Agent — Program, Debug, Automate.", "italic cyan")
        chat_panel.add_splash_message("")
        chat_panel.add_system_message("Type your message and press Enter to start. Waiting for Gateway...")

        # Focus input
        self.query_one(ChatPanel).focus_input()

        # 内存监控定时器
        self._step_count = 0
        self.set_interval(_MEMORY_MONITOR_INTERVAL, self._log_memory)

        # 主线程心跳：每 5 秒写 trace.log（不通过 logging，直接 fsync）
        # 如果 heartbeat 停了 → 主线程卡死
        self._heartbeat_count = 0
        self.set_interval(5.0, self._heartbeat)

        # 快速排空队列：每 100ms 把 reader 线程的 stream / status 消息投递到主线程 UI
        self.set_interval(0.1, self._drain_reader_queues)

        # 安装 Textual 级异常钩子（捕获 Textual 事件循环内部的异常）
        self._install_textual_exception_hook()

    def _drain_reader_queues(self):
        """Drain thread-safe queues from reader thread onto main-thread UI.
        Called every 100ms on the main thread. Never touches I/O-heavy objects.
        """
        # 1) Drain streaming text chunks (very high frequency)
        chat_panel = self.query_one(ChatPanel)
        while True:
            try:
                chunk = self._stream_queue.get_nowait()
                chat_panel.append_to_assistant_message(chunk)
            except queue.Empty:
                break

        # 2) Drain status bar updates
        status_bar = self.query_one(StatusBar)
        while True:
            try:
                text, style, extra = self._status_queue.get_nowait()
                if extra is not None:
                    # extra is for set_stats() call
                    status_bar.set_stats(extra)
                elif style:
                    status_bar.set_status(text, style)
                else:
                    status_bar.set_status(text)
            except queue.Empty:
                break

    def _heartbeat(self):
        self._heartbeat_count += 1
        mem_str = ""
        try:
            import psutil
            proc = psutil.Process()
            mem = proc.memory_info()
            mem_str = f" RSS={mem.rss / 1024 / 1024:.1f}MB threads={proc.num_threads()}"
        except ImportError:
            pass
        _trace(f"HEARTBEAT #{self._heartbeat_count} steps={self._step_count}{mem_str}")

        # ── Gateway health check & auto-restart ──
        gw = self.gateway
        if gw.process is not None:
            exit_code = gw.process.poll()
            if exit_code is not None:
                _tui_log.warning(
                    "Gateway process (pid=%s) died with exit_code=%s — restarting...",
                    gw.process.pid, exit_code,
                )
                _trace(f"Gateway died (pid={gw.process.pid}, exit={exit_code}), restarting...")
                try:
                    gw.restart()
                    _tui_log.info("Gateway restarted, new pid=%s", gw.process.pid if gw.process else "?")
                    _trace(f"Gateway restarted, new pid={gw.process.pid if gw.process else '?'})")
                except Exception as restart_err:
                    _tui_log.critical("Gateway restart failed: %s", restart_err)
                    _trace(f"Gateway restart FAILED: {restart_err}")

    def _install_textual_exception_hook(self):
        """安装 Textual 事件循环的全局异常处理器。

        Textual 内部会 catch 异常显示 Traceback 屏幕，不会自动调用
        sys.excepthook。通过 monkey-patch 底层 handler 来捕获。
        """
        import asyncio
        original_handler = None

        try:
            loop = asyncio.get_running_loop()
            original_handler = loop.get_exception_handler()

            def _tui_exception_handler(loop, context):
                exc = context.get("exception")
                message = context.get("message", "")
                _tui_log.critical(
                    "Textual event loop exception: message=%s exc=%s",
                    message, exc,
                    exc_info=exc if exc else None,
                )
                # 如果 Textual 的异常是有 traceback 的，也写到 crash 日志
                if exc and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    import traceback as tb
                    crash_log = _TUI_LOG_BASE.parent / "gateway" / "crash.log"
                    try:
                        with open(crash_log, "a", encoding="utf-8") as f:
                            f.write(f"\n=== TUI event loop crash · {datetime.now().isoformat()} ===\n")
                            f.write(f"message: {message}\n")
                            tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
                    except Exception:
                        pass
                # Call original handler if it exists
                if original_handler and original_handler is not _tui_exception_handler:
                    try:
                        original_handler(loop, context)
                    except Exception:
                        pass

            loop.set_exception_handler(_tui_exception_handler)
            _tui_log.debug("Textual exception hook installed")
        except RuntimeError:
            _tui_log.warning("Could not install exception hook: no running loop")

    def _log_memory(self):
        """定期记录内存使用和 chat_log 行数。"""
        try:
            import psutil
            proc = psutil.Process()
            mem = proc.memory_info()
            chat_panel = self.query_one(ChatPanel)
            chat_lines = chat_panel.chat_log.line_count if hasattr(chat_panel.chat_log, 'line_count') else '?'
            _tui_log.info(
                "MEM: RSS=%.1fMB VMS=%.1fMB threads=%d chat_lines=%s steps=%d",
                mem.rss / 1024 / 1024, mem.vms / 1024 / 1024,
                proc.num_threads(), chat_lines, self._step_count,
            )
        except ImportError:
            _tui_log.debug("psutil not installed, skipping memory logging")
        except Exception as e:
            _tui_log.debug("Memory log error: %s", e)

    def _on_gateway_init_error(self, payload):
        """Gateway failed to initialize (called from reader thread)"""
        error_msg = payload.get("message", "Unknown initialization error")
        self.call_from_thread(self._do_gateway_init_error, error_msg)

    def _on_gateway_ready(self, payload):
        """Gateway is ready (called from reader thread)"""
        self.call_from_thread(self._do_gateway_ready)

    def _on_agent_ready(self, payload):
        """Agent is ready (called from reader thread)"""
        tools_len = len(payload.get("tools", []))
        self.call_from_thread(self._do_agent_ready, tools_len)

    def _on_agent_thinking(self, payload):
        """Agent is thinking (called from reader thread)"""
        self._status_queue.put(("Thinking...", "thinking", None))
        # Start streaming message on main thread
        self.call_from_thread(self._do_start_assistant_message)

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
        """Agent is processing a step (called from reader thread)"""
        step = payload.get("step", 0)
        self._step_count = step  # atomic int, safe from any thread
        self._status_queue.put((f"Processing step {step}...", "thinking", None))

    def _on_agent_answer(self, payload):
        """Agent has complete answer, non-streaming (called from reader thread)"""
        answer = payload.get("answer", "")
        if answer:
            self.call_from_thread(self._do_agent_answer, answer)

    def _on_agent_streaming(self, payload):
        """Agent is streaming response (called from reader thread, VERY high frequency)"""
        chunk = payload.get("chunk", "")
        if chunk:
            self._stream_queue.put(chunk)

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

        # Update status bar via thread-safe queue
        self._status_queue.put((f"Calling {tool_name}...", "working", None))

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

        # Update status bar via thread-safe queue
        self._status_queue.put(("Tool finished", "success", None))

    def _on_token_stats(self, payload):
        """Token statistics update (called from reader thread)"""
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

        self._status_queue.put(("", "", stats))

    def _on_message_complete(self, payload):
        """Message processing complete (called from reader thread)"""
        answer = "" if self._answer_rendered else payload.get("answer", "")
        self._answer_rendered = False
        self.call_from_thread(self._do_message_complete, answer)

    def _on_user_batch(self, payload):
        """User messages were batched at start (called from reader thread)"""
        count = payload.get("count", 0)
        self.call_from_thread(self._do_chat_system_message, f"Batched {count} messages into one context")

    def _on_user_merge(self, payload):
        """User messages were merged during processing (called from reader thread)"""
        count = payload.get("count", 0)
        self.call_from_thread(self._do_chat_system_message, f"Merged {count} new message(s) before next reasoning step")

    def _on_agent_error(self, payload):
        """Agent error occurred (called from reader thread)"""
        error_msg = payload.get("message", "Unknown error")
        _tui_log.error("Agent error event: %s", error_msg[:200])
        self.call_from_thread(self._do_agent_error, error_msg)

    def _on_command_complete(self, payload):
        """Command execution completed (called from reader thread)"""
        command = payload.get("command", "")
        success = payload.get("success", False)
        error = payload.get("error", "")
        self.call_from_thread(self._do_command_complete, command, success, error)

    def _do_command_complete(self, command: str, success: bool, error: str):
        """Display command result (runs on main thread)"""
        chat_panel = self.query_one(ChatPanel)
        if success:
            if command == "compact":
                chat_panel.add_system_message(
                    "Conversation compacted \u2014 history summarized via LLM"
                )
            elif command == "clear_session":
                chat_panel.add_system_message("Session cleared on gateway")
            else:
                chat_panel.add_system_message(f"Command '{command}' completed")
        else:
            chat_panel.add_error_message(f"Command '{command}' failed: {error}")

    def action_quit(self):
        """Quit the application"""
        import traceback
        _trace("ACTION_QUIT called")
        _tui_log.warning(
            "action_quit called! Stack: %s",
            "".join(traceback.format_stack()[-5:]),
        )
        _trace("ACTION_QUIT stopping gateway...")
        self.gateway.stop()
        _trace("ACTION_QUIT exiting app...")
        self.exit()
        _trace("ACTION_QUIT done")

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
        tool_panel = self.query_one(ToolPanel)
        tool_panel.clear()

        # Clear session on gateway
        self.gateway.call("agent.clear_session", {"session_id": self.session_id})

    def action_toggle_help(self):
        """Toggle help overlay"""
        # TODO: Implement help overlay
        pass

    def _on_micro_compact(self, payload):
        """Micro-compact: older tool results collapsed (called from reader thread)"""
        removed = payload.get("removed_results", 0)
        logger.info(f"Micro-compact: {removed} older tool results compacted")

    def _on_background_started(self, payload):
        """Background task started (called from reader thread)"""
        tool_calls = payload.get("tool_calls", [])
        bg_names = [tc.get("tool_name", "?") for tc in tool_calls]
        self.call_from_thread(self._do_background_started, bg_names)

    def _on_background_completed(self, payload):
        """Background task completed, results injected (called from reader thread)"""
        count = payload.get("count", 0)
        task_ids = payload.get("task_ids", [])
        self.call_from_thread(self._do_background_completed, count, task_ids)

    def _on_background_status(self, payload):
        """Background task status update (called from reader thread)"""
        pending = payload.get("pending_results", 0)
        if pending > 0:
            self._status_queue.put((f"BG: {pending} pending", "working", None))

    def on_exit(self):
        """Textual app shut down lifecycle hook. 记录退出原因。"""
        _trace("ON_EXIT called")
        _tui_log.info("Textual app.on_exit() called — app shutting down")
        # 记录当前 chat_log 行数
        try:
            chat_panel = self.query_one(ChatPanel)
            _tui_log.info("Chat lines on exit: %s", len(chat_panel.chat_log.lines))
        except Exception:
            _tui_log.info("Could not get chat lines on exit")

    def _on_agent_interrupted(self, payload):
        """Agent was interrupted by user (called from reader thread)"""
        message = payload.get("message", "请求已被中断")
        self.call_from_thread(self._do_agent_interrupted, message)

    # ── Main-thread UI helpers (called via call_from_thread from reader thread) ──

    def _do_start_assistant_message(self):
        chat_panel = self.query_one(ChatPanel)
        chat_panel.start_assistant_message()

    def _do_agent_answer(self, answer: str):
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_assistant_message(answer)
        self._answer_rendered = True

    def _do_gateway_ready(self):
        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Gateway ready - You can start chatting!", "success")
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message("Gateway is ready! You can now send messages.")
        import threading
        def load_history_async():
            try:
                result = self.gateway.call("agent.get_history", {})
                messages = result.get("messages", [])
                if messages:
                    self.call_from_thread(chat_panel.replay_messages, messages)
            except Exception as e:
                self.call_from_thread(chat_panel.add_system_message, f"(history load failed: {e})")
        thread = threading.Thread(target=load_history_async, daemon=True)
        thread.start()

    def _do_gateway_init_error(self, error_msg: str):
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"FATAL: {error_msg}", "error")
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message("=" * 50)
        chat_panel.add_error_message(f"Gateway failed to start: {error_msg}")
        chat_panel.add_system_message("Please check config.yaml and restart.")
        chat_panel.add_system_message("=" * 50)

    def _do_agent_ready(self, tools_len: int):
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"Ready - {tools_len} tools", "success")
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"Agent ready with {tools_len} tools")

    def _do_message_complete(self, answer: str):
        if answer:
            chat_panel = self.query_one(ChatPanel)
            chat_panel.add_assistant_message(answer)
        status_bar = self.query_one(StatusBar)
        status_bar.set_status("Ready", "success")

    def _do_chat_system_message(self, msg: str):
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(msg)

    def _do_agent_error(self, error_msg: str):
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_error_message(error_msg)
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"Error: {error_msg[:50]}", "error")
        # Auto-restart gateway if it crashed
        try:
            _tui_log.info("Agent error — restarting gateway...")
            _trace(f"Agent error, restarting gateway: {error_msg[:100]}")
            self.gateway.restart()
            _tui_log.info("Gateway restarted after error")
            _trace("Gateway restarted after error")
        except Exception as restart_err:
            _tui_log.critical("Gateway restart after error failed: %s", restart_err)
            _trace(f"Gateway restart after error FAILED: {restart_err}")

    def _do_background_started(self, bg_names: list):
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"BG task: {', '.join(bg_names)}", "working")

    def _do_background_completed(self, count: int, task_ids: list):
        status_bar = self.query_one(StatusBar)
        status_bar.set_status(f"BG tasks completed: {count}", "success")

    def _do_agent_interrupted(self, message: str):
        chat_panel = self.query_one(ChatPanel)
        chat_panel.add_system_message(f"! {message}")
        tool_panel = self.query_one(ToolPanel)
        tool_panel.clear()
        status_bar = self.query_one(StatusBar)
        status_bar.set_status("What should I do?", "thinking")
        self.query_one(ChatPanel).focus_input()


def run_tui():
    """Run the TUI application"""
    app = TableHelperTUI()
    app.run()


if __name__ == "__main__":
    run_tui()
