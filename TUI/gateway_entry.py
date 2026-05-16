"""
TUI Gateway Entry Point

JSON-RPC server over stdio for TUI communication.
Supports message batching: pending user messages are merged before each actor step.
"""

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to path BEFORE importing project modules
project_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(project_root))

# Set UTF-8 encoding for stdout/stderr (platform-aware, no-op on Linux)
from Tools.platform_utils import setup_stdio_encoding
setup_stdio_encoding()

# Real stdout for JSON-RPC (AFTER encoding setup)
_real_stdout = sys.stdout
_stdout_lock = threading.Lock()

from Agent.ActorAgent import ActorAgent
from Agent.BackgroundTaskManager import BackgroundTaskManager
from Agent.MemoryOrchestrator import MemoryOrchestrator
from Agent.MicroCompactor import MicroCompactor
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from Tools.tool_process_tracker import tool_process_tracker
from configurationLoader import config
from Gateway.session import SessionStore

# Logging setup — centralized, per-module file output
from Core.LogManager import init_logging, get_logger
init_logging()
logger = get_logger(__name__)

# Crash log path (独立 crash 文件，同时 LogManager 也会捕获 ERROR+ 到 logs/gateway/crash.log)
CRASH_LOG = project_root / "runtime_memory" / "logs" / "gateway" / "crash.log"


def write_json(obj: Dict[str, Any]) -> bool:
    """
    Write a JSON-RPC message to stdout.

    Returns:
        bool: True on success, False if pipe is broken
    """
    try:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with _stdout_lock:
            _real_stdout.write(line)
            _real_stdout.flush()
        return True
    except (BrokenPipeError, OSError, ValueError) as e:
        # Pipe broken during shutdown — expected, not an error
        if isinstance(e, ValueError) and "closed file" not in str(e):
            raise
        return False


def log_crash(reason: str, exc_info=None):
    """Log crash information to crash log and stderr.

    LogManager 的 root logger 已附加 crash handler 和 stderr handler，
    直接使用 logger.error() 会同时写入 crash.log 和 stderr。
    同时保留原始 crash 格式写入独立文件以便快速 grep。
    """
    logger.error("CRASH: %s", reason, exc_info=exc_info if exc_info else None)
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== Gateway crash · {datetime.now().isoformat()} ===\n")
            f.write(f"Reason: {reason}\n")
            if exc_info:
                traceback.print_exception(*exc_info, file=f)
    except Exception:
        pass


class GatewaySession:
    """Gateway session managing agent components"""

    def __init__(self):
        self.memory_manager: Optional[MemoryManager] = None
        self.memory_orchestrator: Optional[MemoryOrchestrator] = None
        self.actor: Optional[ActorAgent] = None
        self.runtime = None
        self.session_store: Optional[SessionStore] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        self._prompt_loader = None  # Lazily initialized
        # Message batching support
        self.pending_messages: list = []  # Pending user messages
        self.pending_requests: list = []  # Corresponding request IDs
        self._processing = False  # Whether currently processing
        self._batch_lock = threading.Lock()
        # Interrupt support
        self._interrupt_event = threading.Event()
        # Micro-compact flag (once per session turn)
        self._micro_compacted = False
        # Background task reactivation dedup flag
        self._bg_reactivation_queued: bool = False

    def queue_message(self, message: str, req_id):
        """Queue a user message for batching"""
        with self._batch_lock:
            self.pending_messages.append(message)
            self.pending_requests.append(req_id)
            logger.info(f"Queued message (total pending: {len(self.pending_messages)})")

    def get_pending_messages(self):
        """Get and clear all pending messages"""
        with self._batch_lock:
            msgs = list(self.pending_messages)
            reqs = list(self.pending_requests)
            self.pending_messages.clear()
            self.pending_requests.clear()
            return msgs, reqs

    def has_pending(self) -> bool:
        """Check if there are pending messages"""
        with self._batch_lock:
            return len(self.pending_messages) > 0

    def _drain_background_results(self):
        """Thread-safe: drain background results → inject into memory → emit event.

        Returns:
            list: The drained BackgroundResult objects (empty list if none).
        """
        bg_results = BackgroundTaskManager().drain_pending()
        if not bg_results:
            return []
        BackgroundTaskManager().inject_into_memory(self.memory_manager, bg_results)
        write_json({
            "jsonrpc": "2.0", "method": "event",
            "params": {
                "type": "task.background.completed",
                "payload": {
                    "count": len(bg_results),
                    "task_ids": [r.task_id for r in bg_results],
                }
            }
        })
        logger.info(
            "Drained %d background task result(s) into memory",
            len(bg_results),
        )
        return bg_results

    def _run_post_turn_review(self, turn_context):
        """Fire-and-forget: run post-turn user profile review in background thread."""
        try:
            events = self.memory_orchestrator.post_turn_review(turn_context)
            for evt in events:
                logger.info(f"[MemoryOrch] {evt}")
        except Exception as e:
            logger.warning(f"post_turn_review failed: {e}")

    async def initialize(self):
        """Initialize all components"""
        if self._initialized:
            return

        try:
            logger.info("Initializing gateway session...")

            # Create event loop if needed
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)

            # Initialize memory manager
            self.memory_manager = MemoryManager()
            logger.info("MemoryManager initialized")

            # Initialize memory orchestrator (overflow detection, daily summarization, etc.)
            self.memory_orchestrator = MemoryOrchestrator(self.memory_manager)
            logger.info("MemoryOrchestrator initialized")

            # Initialize tool runtime
            self.runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))
            await self.runtime.initialize()
            await self.runtime.list_tools()  # populate last_tool_definitions
            logger.info("Tool runtime initialized")

            # Inject WorkingMemory into snip tool (module loaded during initialize)
            import Tools.builtin.snip_tool as snip_tool
            snip_tool.set_working_memory(self.memory_manager.working)

            # Initialize agents
            self.actor = ActorAgent(self.runtime, self.memory_manager)
            logger.info("Agents initialized")

            # Initialize session store
            session_config = config.get("gateway.session", {})
            self.session_store = SessionStore(
                storage_path=session_config.get("storage_path", "./runtime_memory/gateway/sessions.json"),
                group_sessions_per_user=session_config.get("group_sessions_per_user", True),
                thread_sessions_per_user=session_config.get("thread_sessions_per_user", False),
                reset_mode=session_config.get("reset_mode", "idle"),
                reset_at_hour=session_config.get("reset_at_hour", 4),
                reset_idle_minutes=session_config.get("reset_idle_minutes", 1440),
            )
            logger.info("SessionStore initialized")

            # Register background task completion callback for real-time TUI updates
            def _on_bg_complete(task_id, bg_result):
                # 1. Immediately notify TUI of status change
                status = BackgroundTaskManager().get_status_summary()
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "task.background.status",
                        "payload": status,
                    }
                })
                # 2. If agent is processing, step boundary will drain naturally
                if self._processing:
                    return
                # 3. Idle: queue a synthetic message and wake up processing
                if self._bg_reactivation_queued:
                    return
                self._bg_reactivation_queued = True
                synthetic_msg = (
                    f"[System: background results arrived after previous answer] "
                    f"Background task(s) completed. Please review and respond if needed."
                )
                with self._batch_lock:
                    self.pending_messages.append(synthetic_msg)
                    self.pending_requests.append("bg_reactivation")
                _start_processing("default")

            BackgroundTaskManager().register_callback(_on_bg_complete)
            logger.info("Background task completion callback registered")

            self._initialized = True
            logger.info("Gateway session initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize gateway: {e}", exc_info=True)
            raise

    async def process_message(self, message: str, session_id: str = "default") -> str:
        """
        Process a user message and return the response.
        Supports message batching: pending messages are merged before each actor step.

        Args:
            message: User message
            session_id: Session identifier

        Returns:
            str: Agent response
        """
        if not self._initialized:
            await self.initialize()

        self._processing = True
        self._micro_compacted = False  # reset per turn

        logger.debug(
            "process_message start: session=%s msg_len=%d",
            session_id, len(message),
        )

        # Periodic memory maintenance: check for expired daily files to summarize
        if self.memory_orchestrator:
            try:
                events = self.memory_orchestrator.detectOverflow()
                for evt in events:
                    logger.info(f"[MemoryOrch] {evt}")
            except Exception as e:
                logger.warning(f"detectOverflow failed: {e}")

        try:
            # 清除上一次可能残留的中断标记
            self._interrupt_event.clear()

            # Get or create session
            from Gateway.platforms.base import SessionSource, Platform
            source = SessionSource(
                platform=Platform.LOCAL,
                user_id="tui_user",
                user_name="TUI User",
                chat_id=session_id,
                chat_type="private"
            )

            session = self.session_store.get_or_create_session(source)

            # Check for pending messages that should be merged BEFORE first step
            pending_msgs, pending_reqs = self.get_pending_messages()
            # If bg_reactivation was queued, clear the dedup flag for this turn
            if "bg_reactivation" in pending_reqs:
                self._bg_reactivation_queued = False
            all_messages = [message] + pending_msgs
            combined = "\n".join(all_messages)

            # Add ALL user messages to memory
            self.memory_manager.append({"role": "user", "content": combined})

            # If messages were batched, notify TUI
            if len(all_messages) > 1:
                write_json({
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "user.batch",
                        "payload": {
                            "count": len(all_messages),
                            "combined": combined[:100]
                        }
                    }
                })

            # Emit thinking event
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "agent.thinking",
                    "payload": {}
                }
            })

            # Process with actor
            step = 0
            max_steps = config.get("agent.maxDepth", 8)

            def emit_token_stats(extra_system_prompt=None):
                token_stats = self.calculate_token_stats(extra_system_prompt)
                write_json({
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "token.stats",
                        "payload": token_stats
                    }
                })

            while step < max_steps:
                step += 1

                # ── 中断检查：用户按下了 ESC ──
                if self._interrupt_event.is_set():
                    self._interrupt_event.clear()
                    tool_process_tracker.kill_all()
                    self.memory_manager.append({
                        "role": "user",
                        "content": "Request interrupted by user"
                    })
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.interrupted",
                            "payload": {"message": "请求已被用户中断"}
                        }
                    })
                    return "请求已被用户中断。"

                # Before each step: check for pending messages and merge
                pending_msgs, pending_reqs = self.get_pending_messages()
                # Clear dedup flag if bg_reactivation is being consumed
                if "bg_reactivation" in pending_reqs:
                    self._bg_reactivation_queued = False
                if pending_msgs:
                    pending_text = "\n".join(pending_msgs)
                    # Add to memory so actor sees them
                    self.memory_manager.append({"role": "user", "content": f"[新消息] {pending_text}"})
                    # Merge into the combined answer
                    combined += f"\n{pending_text}"
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "user.merge",
                            "payload": {
                                "count": len(pending_msgs),
                                "merged": pending_text[:100]
                            }
                        }
                    })
                    # Re-emit thinking event for new context
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.thinking",
                            "payload": {}
                        }
                    })

                # Drain completed background tasks before each step
                self._drain_background_results()

                # Micro-compact: collapse older tool results (once per turn)
                if (
                    not self._micro_compacted
                    and config.get("agent.micro_compact.enabled", True)
                ):
                    messages = self.memory_manager.get_context()
                    age_h = float(config.get("agent.micro_compact.age_threshold_hours", 1))
                    keep_n = int(config.get("agent.micro_compact.keep_tool_results", 5))
                    compacted = MicroCompactor.compact(
                        messages, age_threshold_hours=age_h, keep_tool_results=keep_n
                    )
                    if compacted is not messages:
                        removed = len(messages) - len(compacted)
                        self.memory_manager.clear_context()
                        for msg in compacted:
                            self.memory_manager.append(msg)
                        logger.info(
                            "Micro-compact: removed %d older tool results (threshold=%sh, keep=%d)",
                            removed, age_h, keep_n,
                        )
                        write_json({
                            "jsonrpc": "2.0",
                            "method": "event",
                            "params": {
                                "type": "context.micro_compact",
                                "payload": {"removed_results": removed}
                            }
                        })
                    self._micro_compacted = True

                # Emit step event
                write_json({
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "agent.step",
                        "payload": {"step": step}
                    }
                })

                emit_token_stats()

                # Get actor response with on_tool_call_start callback
                # This callback fires BEFORE each tool executes, allowing us
                # to emit tool.call events in real-time (not after completion)
                current_step = step  # capture for closure

                def _on_tool_call(tool_name, tool_args, call_id="", *args):
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "tool.call",
                            "payload": {
                                "tool": tool_name,
                                "arguments": tool_args,
                                "call_id": str(call_id),
                                "step": current_step
                            }
                        }
                    })

                def _on_thinking(thinking_text):
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.thinking_content",
                            "payload": {
                                "step": current_step,
                                "thinking": str(thinking_text).strip()
                            }
                        }
                    })

                def _on_tool_result(tool_name, call_id, result_text):
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "tool.result",
                            "payload": {
                                "tool": tool_name,
                                "call_id": str(call_id),
                                "result": str(result_text)
                            }
                        }
                    })

                logger.debug("Actor step %d/%d starting...", step, max_steps)
                actor_response = await self.actor.act(
                    on_tool_call_start=_on_tool_call,
                    on_thinking=_on_thinking,
                    on_tool_result=_on_tool_result,
                    _interrupt_check=lambda: self._interrupt_event.is_set()
                )
                logger.debug(
                    "Actor step %d response: type=%s",
                    step, actor_response.get("type"),
                )

                # Drain background results that completed during actor.act()
                # before checking the response type. Without this, a background
                # task that finishes mid-act() is invisible until the next step's
                # drain (one full step of latency).
                self._drain_background_results()

                # Check response type
                response_type = actor_response.get("type")

                # Note: thinking and tool results are already emitted in real-time
                # via on_thinking / on_tool_result callbacks during act() execution.
                # Only emit events that are NOT covered by callbacks.

                if response_type == "answer":
                    # Don't return immediately if background tasks are pending
                    if BackgroundTaskManager().has_pending():
                        bg_results = self._drain_background_results()
                        if bg_results:
                            logger.info(
                                "Background tasks completed during answer, injecting %d result(s) and continuing",
                                len(bg_results),
                            )
                            continue

                    # Got final answer - return directly without streaming
                    answer = actor_response.get("answer", "")

                    # Send complete answer as one event
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.answer",
                            "payload": {"answer": answer}
                        }
                    })

                    # Fire post-turn user profile review in background (non-blocking)
                    if self.memory_orchestrator:
                        ctx = self.memory_manager.get_context()
                        turn_slice = ctx[-24:] if len(ctx) > 24 else ctx
                        threading.Thread(
                            target=self._run_post_turn_review,
                            args=(turn_slice,),
                            daemon=True
                        ).start()

                    return answer

                elif response_type == "tool":
                    # Single tool — result already emitted by on_tool_result callback
                    pass

                elif response_type == "tool_batch":
                    # tool.call and tool.result already emitted by callbacks — nothing more to emit
                    pass

                elif response_type == "tool_backgrounded":
                    # Tool(s) moved to background — notify TUI and continue
                    has_bg = actor_response.get("has_backgrounded", False)
                    if has_bg:
                        write_json({
                            "jsonrpc": "2.0",
                            "method": "event",
                            "params": {
                                "type": "task.background.started",
                                "payload": {
                                    "tool_calls": actor_response.get("tool_calls", []),
                                }
                            }
                        })

                elif response_type == "continue":
                    # Thinking already emitted by on_thinking callback — just keep looping
                    pass

                elif response_type == "interrupted":
                    # User pressed ESC during tool execution — actor skipped tools
                    self._interrupt_event.clear()
                    tool_process_tracker.kill_all()
                    self.memory_manager.append({
                        "role": "user",
                        "content": "Request interrupted by user"
                    })
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.interrupted",
                            "payload": {"message": "请求已被用户中断"}
                        }
                    })
                    return "请求已被用户中断。"

                elif response_type == "error":
                    # Error occurred
                    error_msg = actor_response.get("error", "Unknown error")
                    logger.error(f"Actor error: {error_msg}")
                    return f"Error: {error_msg}"

                else:
                    # Unknown response type
                    logger.warning(f"Unknown actor response type: {response_type}")
                    logger.warning(f"Response: {actor_response}")
                    break

            # Max steps reached
            logger.warning("Max steps (%d) reached for session %s", max_steps, session_id)
            return "I apologize, but I've reached the maximum number of steps. Please try rephrasing your question."

        except Exception as e:
            import traceback
            err_type = type(e).__name__
            err_msg = str(e) or "(no message)"
            logger.error(f"Error processing message: {err_type}: {err_msg}", exc_info=True)
            # Emit error event to TUI before re-raise
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "agent.error",
                    "payload": {"message": f"Agent异常 [{err_type}]: {err_msg}"}
                }
            })
            raise
        finally:
            self._processing = False

    async def clear_session(self, session_id: str):
        """Clear a session's memory"""
        if self.memory_manager:
            self.memory_manager.clear_context()

    async def compact_session(self, session_id: str):
        """Summarize current conversation via LLM and replace with summary"""
        if not self.memory_manager:
            return

        messages = self.memory_manager.get_context()
        if not messages:
            return

        # Serialize messages for the LLM prompt
        history_lines = []
        for msg in messages:
            role = msg.get("role", "unknown") if isinstance(msg, dict) else "unknown"
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        t = item.get("type", "")
                        if t == "text":
                            parts.append(item.get("text", ""))
                        elif t == "tool_use":
                            parts.append(f"[调用工具: {item.get('name', '?')}]")
                        elif t == "tool_result":
                            rc = item.get("content", "")
                            parts.append(f"[工具结果: {str(rc)[:200]}]")
                    else:
                        parts.append(str(item))
                content = " ".join(parts)
            content = str(content)[:2000]
            history_lines.append(f"[{role}] {content}")

        history_text = "\n".join(history_lines)

        # Build compact prompt
        compact_prompt = (
            "你是一个对话摘要助手。请用中文对以下对话进行精炼摘要，保留关键信息：\n"
            "1. 用户的主要问题和需求\n"
            "2. 助手给出的关键回答和结论\n"
            "3. 任何重要的决策、代码修改或文件操作\n"
            "4. 未解决的问题或待办事项\n\n"
            "请用200字以内完成摘要，直接输出摘要文本，不要加任何前缀或标记。\n\n"
            f"=== 对话记录 ===\n{history_text}\n=== 结束 ==="
        )

        try:
            from Agent.LargeLanguageModel import LargeLanguageModel
            llm = LargeLanguageModel()
            summary = llm.query([{"role": "user", "content": compact_prompt}])
            if not summary or not summary.strip():
                summary = "(摘要生成失败)"
        except Exception as e:
            logger.warning(f"Compact summary generation failed: {e}")
            summary = "(摘要生成失败)"

        # Clear context and insert summary
        self.memory_manager.clear_context()
        self.memory_manager.append({
            "role": "system",
            "content": f"[对话摘要 - {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{summary.strip()}"
        })
        logger.info(f"Session compacted: {len(messages)} messages → summary ({len(summary)} chars)")

    def _get_prompt_loader(self):
        """Get or create PromptLoader"""
        if self._prompt_loader is None:
            from Prompting.PromptLoader import PromptLoader
            self._prompt_loader = PromptLoader()
        return self._prompt_loader

    def _read_file_safe(self, path):
        """Read a file safely, return empty string on error"""
        try:
            p = Path(path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    def _estimate_tokens(self, text):
        """Estimate token count for text (uses TokenEstimator)"""
        from Agent.TokenEstimator import TokenEstimator
        return TokenEstimator().estimate(text)

    @staticmethod
    def _stringify_token_content(content):
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except TypeError:
            return str(content)

    def _estimate_rendered_messages(self, messages):
        if not self.actor:
            return {"current_tokens": 0, "cached_tokens": 0}

        core = getattr(self.actor, "core", None)
        provider = getattr(core, "provider", None)

        if provider == "anthropic_compatible" and core is not None:
            system_blocks, remote_messages = core._prepare_anthropic_messages(messages)

            parts = []
            cached_tokens = 0
            for block in system_blocks:
                text = self._stringify_token_content(block.get("text", "")).strip()
                if not text:
                    continue
                parts.append(f"system: {text}")
                if block.get("cache_control"):
                    cached_tokens += self._estimate_tokens(text)

            for message in remote_messages:
                role = message.get("role", "user")
                content = self._stringify_token_content(message.get("content", "")).strip()
                if content:
                    parts.append(f"{role}: {content}")

            current_tokens = self._estimate_tokens("\n".join(parts)) if parts else 0
            return {
                "current_tokens": current_tokens,
                "cached_tokens": min(cached_tokens, current_tokens),
            }

        prepared_messages = core._prepare_local_messages(messages) if core is not None else messages
        parts = []
        for message in prepared_messages or []:
            role = message.get("role", "user") if isinstance(message, dict) else "user"
            content = self._stringify_token_content(message.get("content", "") if isinstance(message, dict) else message).strip()
            if content:
                parts.append(f"{role}: {content}")

        current_tokens = self._estimate_tokens("\n".join(parts)) if parts else 0
        return {"current_tokens": current_tokens, "cached_tokens": 0}

    def calculate_token_stats(self, extra_system_prompt=None):
        """Calculate compact token stats for the next assembled request."""
        if not self.actor:
            return {
                "current_tokens": 0,
                "prompt_cache_ratio": 0.0,
            }

        snapshot = self.actor.build_messages_with_document(extra_system_prompt)
        stats = self._estimate_rendered_messages(snapshot["messages"])
        current_tokens = stats["current_tokens"]
        cached_tokens = stats["cached_tokens"]
        prompt_cache_ratio = (cached_tokens / current_tokens) if current_tokens > 0 else 0.0
        return {
            "current_tokens": current_tokens,
            "prompt_cache_ratio": prompt_cache_ratio,
        }

    async def shutdown(self):
        """Shutdown the gateway"""
        if self.runtime:
            await self.runtime.close()


# Global session
_session: Optional[GatewaySession] = None
_request_queue: queue.Queue = queue.Queue()  # Incoming request queue
_bg_reader_running = False
_processing_lock = threading.Lock()  # Prevent concurrent background processing
_SHUTTING_DOWN = False  # Set to True during process shutdown to stop new processing


def dispatch(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Dispatch a JSON-RPC request.

    For agent.send_message: if session is already processing, the message
    is queued for batching and the method returns immediately.

    Args:
        request: JSON-RPC request object

    Returns:
        JSON-RPC response or None for notifications
    """
    global _session

    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    try:
        if method == "agent.send_message":
            message = params.get("message", "")
            session_id = params.get("session_id", "default")

            # Always queue the message and return immediately (non-blocking)
            _session.queue_message(message, req_id)

            # Start background processing if not already running
            _start_processing(session_id)

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True},
                "id": req_id
            }

        elif method == "agent.clear_session":
            session_id = params.get("session_id", "default")

            # 等待后台消息处理完成（避免竞态条件）
            # 如果后台正在处理消息，等待其完成
            acquired = _processing_lock.acquire(blocking=True, timeout=5.0)
            if not acquired:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "无法获取处理锁，请稍后重试"},
                    "id": req_id
                }

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_session.clear_session(session_id))
                    # Emit updated token stats after clear
                    token_stats = _session.calculate_token_stats()
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "token.stats",
                            "payload": token_stats
                        }
                    })
                    return {
                        "jsonrpc": "2.0",
                        "result": {"success": True},
                        "id": req_id
                    }
                finally:
                    loop.close()
            finally:
                _processing_lock.release()

        elif method == "agent.compact":
            session_id = params.get("session_id", "default")

            acquired = _processing_lock.acquire(blocking=True, timeout=5.0)
            if not acquired:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "无法获取处理锁，请稍后重试"},
                    "id": req_id
                }

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_session.compact_session(session_id))
                    # Emit updated token stats after compact
                    token_stats = _session.calculate_token_stats()
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "token.stats",
                            "payload": token_stats
                        }
                    })
                    return {
                        "jsonrpc": "2.0",
                        "result": {"success": True},
                        "id": req_id
                    }
                finally:
                    loop.close()
            finally:
                _processing_lock.release()

        elif method == "agent.get_history":
            """获取聊天历史记录"""
            history = _session.memory_manager.get_context()
            # 序列化为可传输的格式（只保留 role 和 content）
            serialized = []
            for msg in history:
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    msg_type = "text"  # 标记消息类型，TUI 据此渲染

                    if isinstance(content, list):
                        # Anthropic 原生 content list 格式
                        parts = []
                        has_tool_use = False
                        has_tool_result = False
                        for item in content:
                            if not isinstance(item, dict):
                                continue
                            block_type = item.get("type")
                            if block_type == "text":
                                parts.append(item.get("text", ""))
                            elif block_type == "thinking":
                                parts.append(f"[思考] {item.get('thinking', '')}")
                            elif block_type == "tool_use":
                                tool_name = item.get("name", "?")
                                tool_input = item.get("input", {})
                                # 精简参数显示
                                args_brief = ", ".join(
                                    f"{k}={str(v)[:80]}"
                                    for k, v in (tool_input.items() if isinstance(tool_input, dict) else {})
                                )
                                parts.append(f"🔧 调用工具: {tool_name}({args_brief})")
                                has_tool_use = True
                            elif block_type == "tool_result":
                                result_content = item.get("content", "")
                                if isinstance(result_content, str):
                                    parts.append(result_content[:500])
                                elif isinstance(result_content, list):
                                    for rc in result_content:
                                        if isinstance(rc, dict) and rc.get("type") == "text":
                                            parts.append(rc.get("text", "")[:500])
                                has_tool_result = True
                        content = "\n".join(p for p in parts if p)
                        if has_tool_use:
                            msg_type = "tool_call"
                        elif has_tool_result:
                            msg_type = "tool_result"
                    serialized.append({
                        "role": role,
                        "content": str(content)[:1000],
                        "type": msg_type,
                    })
            return {
                "jsonrpc": "2.0",
                "result": {"messages": serialized},
                "id": req_id
            }

        elif method == "agent.update_config":
            """更新配置参数并保存到文件"""
            key = params.get("key", "")
            value = params.get("value")
            if not key:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Missing 'key' parameter"},
                    "id": req_id
                }
            try:
                # 尝试转换数值类型
                if isinstance(value, str):
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    else:
                        try:
                            if "." in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except (ValueError, TypeError):
                            pass  # 保持字符串
                old_value = config.set(key, value)
                config.save()
                logger.info(f"Config updated: {key} = {value} (was: {old_value})")
                return {
                    "jsonrpc": "2.0",
                    "result": {"success": True, "key": key, "old_value": old_value, "new_value": value},
                    "id": req_id
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                    "id": req_id
                }

        elif method == "agent.interrupt":
            # 仅在 agent 正在处理消息时才标记中断
            if _session._processing:
                _session._interrupt_event.set()
                logger.info("Interrupt requested by user")
            return {
                "jsonrpc": "2.0",
                "result": {"interrupted": _session._processing},
                "id": req_id
            }

        else:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                },
                "id": req_id
            }

    except Exception as e:
        logger.error(f"Error dispatching {method}: {e}", exc_info=True)

        # Emit error event
        write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "agent.error",
                "payload": {"message": str(e)}
            }
        })

        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": str(e)
            },
            "id": req_id
        }


def _stdin_reader():
    """Background thread: read JSON-RPC requests from stdin"""
    global _bg_reader_running
    _bg_reader_running = True

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            _request_queue.put(request)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            write_json({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            })

    _bg_reader_running = False
    logger.info("stdin EOF")


def _process_queued_messages(session_id: str):
    """
    Background processing: collects all pending messages, processes them,
    and before each actor step checks for newly queued messages.
    """
    global _session
    global _processing_lock

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while _session.has_pending():
            # Collect ALL pending messages
            pending_msgs, pending_reqs = _session.get_pending_messages()
            if not pending_msgs:
                break

            # Combine all messages into one context
            combined = "\n".join(pending_msgs)

            # Notify TUI of batching
            if len(pending_msgs) > 1:
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "user.batch",
                        "payload": {"count": len(pending_msgs), "combined": combined[:100]}
                    }
                })

            # Process as one combined message
            answer = loop.run_until_complete(
                _session.process_message(combined, session_id)
            )

            # Emit token stats
            token_stats = _session.calculate_token_stats()
            write_json({
                "jsonrpc": "2.0", "method": "event",
                "params": {"type": "token.stats", "payload": token_stats}
            })

            # Emit completion event (for all batched requests)
            write_json({
                "jsonrpc": "2.0", "method": "event",
                "params": {
                    "type": "message.complete",
                    "payload": {"answer": answer}
                }
            })

            # ── Post-answer background watcher ─────────────────
            # After answering, watch for late background task completions.
            # If new results arrive, inject them as synthetic messages
            # and re-trigger processing so the agent can respond.
            #
            # IMPORTANT: Uses asyncio.sleep (not time.sleep) so the event
            # loop stays alive.  BackgroundTaskManager.track_task() relies on
            # task.add_done_callback() to populate _pending_results, and those
            # callbacks only fire while the event loop is running.
            from configurationLoader import config
            watch_window = int(config.get("agent.background_watch_window", 1800))
            max_reactivations = int(config.get("agent.background_max_reactivations", 3))
            task_mgr = BackgroundTaskManager()

            if watch_window > 0 and task_mgr._reactivation_count < max_reactivations:
                start_watch = time.time()

                async def _bg_watcher():
                    while (time.time() - start_watch) < watch_window:
                        # 用户发了新消息，立即退出 watcher 处理消息
                        if _session.has_pending():
                            return True
                        if task_mgr.has_pending():
                            bg_results = task_mgr.drain_pending()
                            task_mgr.increment_reactivation()
                            logger.info(
                                "[bg-task] Post-answer reactivation #%d: %d results arrived",
                                task_mgr._reactivation_count, len(bg_results),
                            )
                            # Inject results into memory so agent sees them
                            if _session.memory_manager:
                                BackgroundTaskManager().inject_into_memory(
                                    _session.memory_manager, bg_results
                                )
                            # Only queue synthetic message if callback didn't already
                            if not _session._bg_reactivation_queued:
                                synthetic_msg = (
                                    f"[System: background results arrived after previous answer] "
                                    f"{len(bg_results)} background task(s) completed. "
                                    f"Please review and respond if needed."
                                )
                                with _session._batch_lock:
                                    _session.pending_messages.append(synthetic_msg)
                                    _session.pending_requests.append("bg_reactivation")
                            return True
                        await asyncio.sleep(1.0)
                    return False

                loop.run_until_complete(_bg_watcher())

    except Exception as e:
        err_type = type(e).__name__
        err_msg = str(e) or "(no message)"

        # "cannot schedule new futures after shutdown" 是 executor 生命周期问题
        # 不视为严重 crash，记录后尝试从新线程恢复
        if "cannot schedule new futures" in err_msg:
            logger.warning(
                "LLM executor shutdown detected, restarting processor thread: %s",
                err_msg,
            )
        else:
            logger.error(f"Error in message processor: {e}", exc_info=True)
            log_crash(f"Message processor error: {e}", sys.exc_info())

        # Emit error event to TUI
        write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "agent.error",
                "payload": {"message": f"处理消息异常 [{err_type}]: {err_msg}"}
            }
        })
    finally:
        loop.close()
        # CRITICAL: Release processing lock so new messages can start processing
        try:
            _processing_lock.release()
        except RuntimeError:
            pass  # Lock may not have been acquired (shouldn't happen normally)
        # Re-check: messages might have arrived during the lock release race window
        if _session.has_pending():
            _start_processing(session_id)


def _start_processing(session_id: str):
    """
    Start a background processing thread if one isn't already running.
    Thread-safe, can be called from any thread.
    """
    global _SHUTTING_DOWN
    if _SHUTTING_DOWN:
        return
    if _processing_lock.acquire(blocking=False):
        thread = threading.Thread(
            target=_process_queued_messages,
            args=(session_id,),
            daemon=True
        )
        thread.start()


def main():
    """Main entry point"""
    global _session

    try:
        # Initialize session immediately
        _session = GatewaySession()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_session.initialize())
        except Exception as init_err:
            error_msg = str(init_err)
            log_crash(f"Gateway initialization failed: {error_msg}", sys.exc_info())
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "gateway.init_error",
                    "payload": {"message": error_msg}
                }
            })
            sys.exit(1)
        finally:
            loop.close()

        logger.info("Gateway session initialized")

        # Send ready event
        if not write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": {}}
        }):
            log_crash("Failed to send gateway.ready event")
            sys.exit(1)

        # Emit initial token stats
        token_stats = _session.calculate_token_stats()
        write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "token.stats",
                "payload": token_stats
            }
        })

        logger.info(f"Gateway ready, token stats: current={token_stats['current_tokens']}, "
                     f"cache_ratio={token_stats['prompt_cache_ratio']:.2%}")

        # Start background stdin reader
        reader_thread = threading.Thread(target=_stdin_reader, daemon=True)
        reader_thread.start()

        # Process requests from queue
        while _bg_reader_running:
            try:
                request = _request_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Dispatch request
            response = dispatch(request)
            if response is not None:
                if not write_json(response):
                    log_crash("Failed to write response (broken pipe)")
                    sys.exit(0)

    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down...")
    except Exception as e:
        log_crash(f"Unhandled exception: {e}", sys.exc_info())
        sys.exit(1)
    finally:
        global _SHUTTING_DOWN
        # Mark processing as stopped so pending daemon threads don't
        # keep submitting LLM queries during executor shutdown
        _SHUTTING_DOWN = True

        # Cleanup gateway
        if _session:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_session.shutdown())
            finally:
                loop.close()

        # Shutdown the persistent LLM executor last
        try:
            from Agent.ActorAgent import shutdown_llm_executor
            shutdown_llm_executor(wait=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
