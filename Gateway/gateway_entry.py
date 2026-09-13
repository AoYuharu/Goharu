"""
Gateway Entry Point — agent backend subprocess.

JSON-RPC server over stdio for TUI communication.
Supports message batching: pending user messages are merged before each actor step.

Protocol contract (methods/events/payloads): docs/tui-protocol.md
— consumed by the tui-ts/ (TypeScript Ink) frontend. Keep both in sync
when changing the protocol.
"""

import asyncio
import faulthandler
import http.server
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
from Agent.MicroCompactor import MicroCompactor
from Core.Message import CoreMessage, MessageSource
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from Tools.tool_process_tracker import tool_process_tracker
from configurationLoader import config

# Logging setup — centralized, per-module file output
from Core.LogManager import init_logging, get_logger
from Core.LifecycleLog import lifecycle
from Core.timing import timeit
init_logging()
logger = get_logger(__name__)
lifecycle("════════ 新会话 ════════", f"Gateway 进程启动 pid={os.getpid()}", level=1)

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


def emit_voice_event(ev_type: str, payload: Dict[str, Any]):
    """推送语音相关事件给 TUI（下载进度 / 转写结果 / 状态）。"""
    write_json({
        "jsonrpc": "2.0", "method": "event",
        "params": {"type": ev_type, "payload": payload},
    })


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


def _set_dotenv_value(key: str, value: str):
    """写入/更新 .env 文件中的 key=value（保留其他行与注释）。"""
    env_path = project_root / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    quoted = value.replace('"', '\\"')
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f'{key}="{quoted}"'
            found = True
            break
    if not found:
        lines.append(f'{key}="{quoted}"')
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _classify_switch_failure(err: str):
    """把模型切换失败的原始异常串归类成一句用户可读的短消息。

    返回 (error_kind, message)。auto 探测的原始错误里混着 OpenAI 协议探测
    的预期失败（如对 anthropic 路由的 404），不能原样抛给用户。
    """
    low = (err or "").lower()
    if "authenticationerror" in low or "401" in low or "login fail" in low:
        return "auth", "API Key 认证失败（401）：key 缺失或无效，请重新输入正确的 key 后重试"
    if "apitimeouterror" in low or "timeout" in low or "connection error" in low:
        return "network", "无法连接目标端点（超时或网络错误），请检查 API URL 与网络后重试"
    if "notfounderror" in low or "404" in low:
        return "route", "端点路由不存在（404）：请检查 API URL 是否正确"
    return "unknown", f"切换失败：{(err or '')[:160]}"


class GatewaySession:
    """Gateway session managing agent components"""

    def __init__(self):
        self.memory_manager: Optional[MemoryManager] = None
        self.actor: Optional[ActorAgent] = None
        self.runtime = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        self._prompt_loader = None  # Lazily initialized
        # Unified queue: messages + commands processed in FIFO order
        # Each item is a dict with "type" key: "message" or "command"
        #   message: {"type": "message", "data": str, "req_id": str}
        #   command: {"type": "command", "name": str, "params": dict, "req_id": str}
        self.pending_items: list = []
        self._processing = False  # Whether currently processing
        self._batch_lock = threading.Lock()
        # Interrupt support
        self._interrupt_event = threading.Event()
        # Micro-compact flag (once per session turn)
        self._micro_compacted = False
        # Background task reactivation dedup flag
        self._bg_reactivation_queued: bool = False
        # PipelineManager: TencentDB-aligned memory pipeline
        self.pipeline_manager = None
        # 卡死看门狗：step 超时未返回时自动 dump 全部线程栈到此文件
        self._watchdog_file = None
        # 生命周期日志轮次计数
        self._turn_count = 0

    def queue_message(self, message: str, req_id):
        """Queue a user message for batching"""
        with self._batch_lock:
            self.pending_items.append({"type": "message", "data": message, "req_id": req_id})
            logger.info(f"Queued message (total pending: {len(self.pending_items)})")

    def queue_command(self, command_name: str, params: dict, req_id):
        """Queue a command to be executed between message batches"""
        with self._batch_lock:
            self.pending_items.append(
                {"type": "command", "name": command_name, "params": params, "req_id": req_id}
            )
            logger.info(
                "Queued command '%s' (total pending: %d)",
                command_name, len(self.pending_items),
            )

    def get_pending_messages(self):
        """Get and clear only pending messages, leaving commands in queue.

        Used by mid-step message merge inside process_message().
        Commands must NOT be executed mid-step — they wait for the
        next iteration of the outer processing loop.
        """
        with self._batch_lock:
            msgs = []
            reqs = []
            remaining = []
            for item in self.pending_items:
                if item["type"] == "message":
                    msgs.append(item["data"])
                    reqs.append(item["req_id"])
                else:
                    remaining.append(item)
            self.pending_items = remaining
            return msgs, reqs

    def drain_pending_items(self):
        """Get and clear ALL pending items (messages and commands).

        Used by the main processing loop to get everything queued so far.
        """
        with self._batch_lock:
            items = list(self.pending_items)
            self.pending_items.clear()
            return items

    def has_pending(self) -> bool:
        """Check if there are pending items (messages or commands)"""
        with self._batch_lock:
            return len(self.pending_items) > 0

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
            lifecycle("启动: MemoryManager", "初始化完成")

            # Initialize PipelineManager (TencentDB-aligned L1/L2/L3 pipeline)
            from Agent.PipelineManager import PipelineManager
            self.pipeline_manager = PipelineManager(memory_manager=self.memory_manager)
            logger.info("PipelineManager initialized")
            lifecycle("启动: PipelineManager", "初始化完成")

            # 一次性迁移：SQLite L2 → scene_blocks/*.md, USER.md → persona.md
            try:
                migrated_l2 = self.pipeline_manager.migrate_legacy_l2_to_scene_blocks()
                if migrated_l2 > 0:
                    logger.info("Migration: %d L2 scenes migrated to scene_blocks", migrated_l2)
                self.pipeline_manager.migrate_legacy_l3_to_persona()
            except Exception as e:
                logger.warning("Migration failed (non-critical): %s", e)

            # Initialize tool runtime
            self.runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))
            await self.runtime.initialize()
            lifecycle("启动: 工具运行时", "初始化完成")

            # Start MCP client manager: connect configured servers, bridge their
            # tools into the registry (mcp__<server>__<tool>), then refresh the
            # tool table so the actor sees them immediately
            from Tools.mcp.manager import mcp_manager
            mcp_manager.attach_runtime(self.runtime)
            try:
                await mcp_manager.start()
            except Exception as e:
                logger.error("MCP manager startup failed (non-critical): %s", e)

            await self.runtime.list_tools()  # populate last_tool_definitions
            logger.info("Tool runtime initialized")
            lifecycle("启动: 工具加载", f"{len(self.runtime.last_tool_definitions)} 个工具")

            # Inject WorkingMemory into snip tool (module loaded during initialize)
            import Tools.builtin.snip_tool as snip_tool
            snip_tool.set_working_memory(self.memory_manager.working)

            # Initialize agents
            self.actor = ActorAgent(self.runtime, self.memory_manager)
            logger.info("Agents initialized")
            lifecycle("启动: ActorAgent", "初始化完成")

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
                # 1b. Per-task completion event for the TUI background panel
                from Core.ToolCall import ToolCall
                failed = bool(bg_result.error) or ToolCall.classify_is_error(
                    bg_result.tool_name, str(bg_result.result)
                )
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "task.background.update",
                        "payload": {
                            "task_id": task_id,
                            "tool": bg_result.tool_name,
                            "status": "error" if failed else "success",
                        },
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
                self.memory_manager.append(CoreMessage.context_only(
                    MessageSource.REACTIVATION, synthetic_msg,
                ).to_dict())
                with self._batch_lock:
                    self.pending_items.append({
                        "type": "message",
                        "data": synthetic_msg,
                        "req_id": "bg_reactivation",
                    })
                _start_processing("default")

            BackgroundTaskManager().register_callback(_on_bg_complete)
            logger.info("Background task completion callback registered")

            # Warm up the embedding model in the background so the first user
            # message doesn't block ~60s on SentenceTransformer load during
            # retrieve_prompt_memory (perceived as "stuck at step 1").
            def _warmup_embedding():
                try:
                    from Memory.retrieval.EmbeddingService import EmbeddingService
                    EmbeddingService().encode("warmup")
                    logger.info("Embedding model warmup completed")
                except Exception as e:
                    logger.warning("Embedding warmup failed: %s", e)

            threading.Thread(target=_warmup_embedding, daemon=True, name="embedding-warmup").start()
            lifecycle("启动: embedding 预热", "后台线程")

            # Per-task start events for the TUI background panel
            def _on_bg_start(task_id, meta):
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "task.background.update",
                        "payload": {
                            "task_id": task_id,
                            "tool": meta.get("tool_name", "?"),
                            "args": meta.get("args", ""),
                            "status": "running",
                            "started_at": meta.get("submitted_at", 0),
                        },
                    }
                })

            BackgroundTaskManager().register_start_callback(_on_bg_start)
            logger.info("Background task start callback registered")

            # Wire subagent (AgentDelegate) lifecycle/activity events to TUI
            from Tools.builtin import agent_delegate as _agent_delegate

            def _on_subagent_event(payload: dict):
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {"type": "subagent.update", "payload": payload},
                })

            _agent_delegate._manager.set_output_callback(_on_subagent_event)
            logger.info("Subagent output callback registered")

            # Forward live tool (run_cmd) stdout/stderr to TUI as tool.output events
            from Tools.tool_output_stream import tool_output_bus

            def _on_tool_output(line: str, stream: str, source: str):
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "tool.output",
                        "payload": {"line": line, "stream": stream, "cmd": source},
                    },
                })

            tool_output_bus.subscribe(_on_tool_output)
            logger.info("Tool output stream subscribed")

            # Dangerous-command confirmation: run_cmd blocks until user answers
            from Tools.confirmation_manager import confirmation_manager

            def _on_confirm_request(confirm_id: str, cmd: str, description: str):
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "tool.confirm_request",
                        "payload": {
                            "confirm_id": confirm_id,
                            "cmd": cmd,
                            "description": description,
                        },
                    },
                })

            confirmation_manager.set_notifier(_on_confirm_request)
            logger.info("Confirmation manager notifier registered")

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

        # 重置本轮的工具调用历史板与触及文件跟踪（供 Verify 上下文收敛用）
        try:
            from Tools.touched_files import reset as reset_touched_files
            reset_touched_files()
            from Tools.tool_call_board import reset_main_calls
            reset_main_calls()
        except Exception:
            pass

        logger.debug(
            "process_message start: session=%s msg_len=%d",
            session_id, len(message),
        )

        # Emit thinking event IMMEDIATELY (before any blocking operations)
        # so TUI shows spinner feedback right away
        write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "agent.thinking",
                "payload": {}
            }
        })

        # PipelineManager: notify activity & check triggers
        if self.pipeline_manager:
            try:
                with timeit("gateway.process.notify_activity"):
                    self.pipeline_manager.notify_activity()
            except Exception as e:
                logger.warning(f"PipelineManager.notify_activity failed: {e}")

        # Periodic memory maintenance: trigger pipeline turn start (L1 check)
        # Pass before_l1_callback to emit agent.l1_extracting event when L1 runs
        if self.pipeline_manager:
            try:
                def _on_l1_start():
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.l1_extracting",
                            "payload": {}
                        }
                    })
                with timeit("gateway.process.on_turn_start"):
                    self.pipeline_manager.on_turn_start(before_l1_callback=_on_l1_start)
            except Exception as e:
                logger.warning(f"PipelineManager.on_turn_start failed: {e}")

        try:
            # 清除上一次可能残留的中断标记
            self._interrupt_event.clear()

            # Check for pending messages that should be merged BEFORE first step
            pending_msgs, pending_reqs = self.get_pending_messages()
            # If bg_reactivation was queued, clear the dedup flag for this turn
            if "bg_reactivation" in pending_reqs:
                self._bg_reactivation_queued = False
            all_messages = [message] + pending_msgs
            combined = "\n".join(all_messages)

            # Add ALL user messages to memory
            self.memory_manager.append({"role": "user", "content": combined})
            self._turn_count += 1
            # turn 边界：清空记忆检索缓存，本轮首次构建时重新检索一次
            self.actor.reset_retrieval_cache()
            lifecycle(
                "══ 第 {} 轮 ══".format(self._turn_count),
                f'用户消息: "{combined[:80]}"',
                level=1,
            )

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

            # Process with actor
            step = 0
            max_steps = config.get("agent.maxDepth", 8)

            def emit_token_stats_from_build(build_result):
                """复用 actor 主构建的结果发 token 统计（不再单独组装一次），
                面板数值与实际发给 LLM 的 messages 完全一致。"""
                token_stats = self.calculate_token_stats_from_messages(
                    build_result["messages"]
                )
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
                lifecycle(f"第 {step} 步", f"max_steps={max_steps}", level=2)

                # ── 中断检查：用户按下了 ESC ──
                if self._interrupt_event.is_set():
                    self._interrupt_event.clear()
                    tool_process_tracker.kill_all()
                    self.memory_manager.append(CoreMessage.context_only(
                        MessageSource.INTERRUPT,
                        "Request interrupted by user",
                    ).to_dict())
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.interrupted",
                            "payload": {"message": "请求已被用户中断"}
                        }
                    })
                    lifecycle("轮结束", "被用户中断", level=2)
                    return "请求已被用户中断。"

                # Before each step: check for pending messages and merge
                pending_msgs, pending_reqs = self.get_pending_messages()
                # Clear dedup flag if bg_reactivation is being consumed
                if "bg_reactivation" in pending_reqs:
                    self._bg_reactivation_queued = False
                if pending_msgs:
                    pending_text = "\n".join(pending_msgs)
                    # Add to memory so actor sees them
                    # bg_reactivation already injected directly as context_only above
                    if "bg_reactivation" not in pending_reqs:
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

                logger.info("step %d: bg drained, entering micro-compact check", step)
                self._arm_step_watchdog(step)  # 覆盖整个 step（含 token stats），不只 act()

                # Micro-compact: collapse older tool results (once per turn)
                if (
                    not self._micro_compacted
                    and config.get("agent.micro_compact.enabled", True)
                ):
                    with timeit("gateway.step.micro_compact"):
                        messages = self.memory_manager.get_context()
                        age_h = float(config.get("agent.micro_compact.age_threshold_hours", 1))
                        keep_n = int(config.get("agent.micro_compact.keep_tool_results", 5))
                        min_chars = int(config.get("agent.micro_compact.min_content_chars", 120))
                        compacted, folded = MicroCompactor.compact(
                            messages,
                            age_threshold_hours=age_h,
                            keep_tool_results=keep_n,
                            min_content_chars=min_chars,
                        )
                        if compacted is not messages:
                            self.memory_manager.clear_context()
                            for msg in compacted:
                                self.memory_manager.append(msg)
                            logger.info(
                                "Micro-compact: folded %d older tool results (threshold=%sh, keep=%d, min_chars=%d)",
                                folded, age_h, keep_n, min_chars,
                            )
                            write_json({
                                "jsonrpc": "2.0",
                                "method": "event",
                                "params": {
                                    "type": "context.micro_compact",
                                    "payload": {"removed_results": folded}
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

                # Emit LLM requesting event (before actor.act() LLM call)
                write_json({
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "agent.llm_requesting",
                        "payload": {}
                    }
                })

                # Get actor response with on_tool_call_start callback
                # This callback fires BEFORE each tool executes, allowing us
                # to emit tool.call events in real-time (not after completion)
                current_step = step  # capture for closure

                # 读取 see 配置，控制哪些内容实时展示
                see_config = config.get("ui.see", {})
                show_thinking = see_config.get("thinking", True)
                show_text = see_config.get("text", True)

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
                    if not show_thinking:
                        return
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

                def _on_text(text_content):
                    if not show_text:
                        return
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.text_content",
                            "payload": {
                                "step": current_step,
                                "text": str(text_content).strip()
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

                def _on_retry(attempt, max_retries, backoff, phase="backoff"):
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.retry",
                            "payload": {
                                "attempt": attempt,
                                "max_retries": max_retries,
                                "backoff": backoff,
                                "phase": phase,
                                "step": current_step
                            }
                        }
                    })

                logger.info("Actor step %d/%d starting LLM call...", step, max_steps)
                try:
                    with timeit(f"gateway.step{step}.actor_act"):
                        actor_response = await self.actor.act(
                            on_tool_call_start=_on_tool_call,
                            on_thinking=_on_thinking,
                            on_tool_result=_on_tool_result,
                            on_text=_on_text,
                            on_retry=_on_retry,
                            _interrupt_check=lambda: self._interrupt_event.is_set(),
                            on_built=emit_token_stats_from_build
                        )
                finally:
                    faulthandler.cancel_dump_traceback_later()
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
                    lifecycle("轮结束", f"answer {len(answer)} 字", level=2)

                    # Send complete answer as one event
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.answer",
                            "payload": {"answer": answer}
                        }
                    })

                    # PipelineManager: on_turn_end (L3 check)
                    if self.pipeline_manager:
                        ctx = self.memory_manager.get_context()
                        turn_slice = ctx[-24:] if len(ctx) > 24 else ctx
                        try:
                            with timeit("gateway.process.on_turn_end"):
                                self.pipeline_manager.on_turn_end(turn_slice)
                        except Exception as e:
                            logger.warning(f"PipelineManager.on_turn_end failed: {e}")

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
                    self.memory_manager.append(CoreMessage.context_only(
                        MessageSource.INTERRUPT,
                        "Request interrupted by user",
                    ).to_dict())
                    write_json({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "agent.interrupted",
                            "payload": {"message": "请求已被用户中断"}
                        }
                    })
                    lifecycle("轮结束", "被用户中断（工具执行期间）", level=2)
                    return "请求已被用户中断。"

                elif response_type == "error":
                    # Error occurred
                    error_msg = actor_response.get("error", "Unknown error")
                    logger.error(f"Actor error: {error_msg}")
                    lifecycle("轮结束", f"错误: {error_msg}", level=2)
                    return f"Error: {error_msg}"

                else:
                    # Unknown response type
                    logger.warning(f"Unknown actor response type: {response_type}")
                    logger.warning(f"Response: {actor_response}")
                    break

            # Max steps reached
            logger.warning("Max steps (%d) reached for session %s", max_steps, session_id)
            lifecycle("轮结束", f"达到最大步数 {max_steps}", level=2)
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
        if self.pipeline_manager:
            self.pipeline_manager.reset_warmup()

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
            return {"current_tokens": 0}

        core = getattr(self.actor, "core", None)
        provider = getattr(core, "provider", None)
        prepare = getattr(provider, "_prepare_messages", None)

        if core is not None and callable(prepare):
            system_blocks, remote_messages = prepare(messages)

            parts = []
            for block in system_blocks:
                text = self._stringify_token_content(block.get("text", "")).strip()
                if not text:
                    continue
                parts.append(f"system: {text}")

            for message in remote_messages:
                role = message.get("role", "user")
                content = self._stringify_token_content(message.get("content", "")).strip()
                if content:
                    parts.append(f"{role}: {content}")

            current_tokens = self._estimate_tokens("\n".join(parts)) if parts else 0
            return {"current_tokens": current_tokens}

        current_tokens = self._estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str))
        return {"current_tokens": current_tokens}

    def calculate_token_stats(self, extra_system_prompt=None):
        """Calculate compact token stats for the next assembled request.

        缓存命中率来自最近一次 API 响应的真实 usage（cache_read 占总输入的
        比例），而不是本地 cache_control 标记估算——MiniMax 走自动前缀缓存，
        本地标记与实际命中无对应关系。
        """
        if not self.actor:
            return {
                "current_tokens": 0,
                "prompt_cache_ratio": 0.0,
            }

        logger.debug("token stats: building messages snapshot...")
        snapshot = self.actor.build_messages_with_document(extra_system_prompt)
        logger.debug("token stats: snapshot built (%d msgs), estimating...",
                     len(snapshot["messages"]))
        return self.calculate_token_stats_from_messages(snapshot["messages"])

    def calculate_token_stats_from_messages(self, messages):
        """基于已组装好的 messages 估算 token（不重新构建提示词）。"""
        stats = self._estimate_rendered_messages(messages)
        current_tokens = stats["current_tokens"]

        usage = getattr(getattr(self.actor, "core", None), "_last_usage", None) or {}
        uncached_input = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        total_input = uncached_input + cache_read + cache_write
        prompt_cache_ratio = (cache_read / total_input) if total_input > 0 else 0.0
        return {
            "current_tokens": current_tokens,
            "prompt_cache_ratio": prompt_cache_ratio,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "uncached_input_tokens": uncached_input,
        }

    def _arm_step_watchdog(self, step: int):
        """卡死看门狗：actor step 超过阈值未返回时，自动 dump 全部线程栈。

        输出到 logs/gateway/watchdog.log。正常返回时由调用方 cancel。
        """
        timeout = float(config.get("agent.step_watchdog_seconds", 150) or 150)
        if timeout <= 0:
            return
        try:
            if self._watchdog_file is None:
                log_dir = Path(config.get("logging.base_dir", "./runtime_memory/logs")) / "gateway"
                log_dir.mkdir(parents=True, exist_ok=True)
                self._watchdog_file = open(log_dir / "watchdog.log", "a", encoding="utf-8")
            self._watchdog_file.write(
                f"\n===== step {step} watchdog armed at {datetime.now().isoformat()} "
                f"(timeout={timeout}s) =====\n"
            )
            self._watchdog_file.flush()
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(timeout, file=self._watchdog_file)
        except Exception as e:
            logger.warning("step watchdog arm failed: %s", e)

    async def shutdown(self):
        """Shutdown the gateway"""
        if self.pipeline_manager:
            self.pipeline_manager.shutdown()
        try:
            from Tools.mcp.manager import mcp_manager
            await mcp_manager.close()
        except Exception as e:
            logger.warning("MCP manager close failed: %s", e)
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

            import sys as _sys2
            _sys2.stderr.write(f"[TRACE] dispatch: agent.send_message msg_len={len(message)} session={session_id}\n")
            _sys2.stderr.flush()

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

            # Queue command for execution in the processing loop.
            # No lock contention — commands are processed sequentially
            # between message batches by _process_queued_messages().
            _session.queue_command("clear_session", {"session_id": session_id}, req_id)
            _start_processing(session_id)

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True, "command": "clear_session"},
                "id": req_id,
            }

        elif method == "agent.compact":
            session_id = params.get("session_id", "default")

            # Queue command for execution in the processing loop.
            # No lock contention — commands are processed sequentially
            # between message batches by _process_queued_messages().
            _session.queue_command("compact", {"session_id": session_id}, req_id)
            _start_processing(session_id)

            return {
                "jsonrpc": "2.0",
                "result": {"queued": True, "command": "compact"},
                "id": req_id,
            }

        elif method == "agent.get_history":
            """获取聊天历史记录（结构化回放条目：text/thinking/tool）。

            tool_use 与其后的 tool_result 按 tool_use_id 配对回填，
            使 TUI 能以与实时一致的形态（折叠工具行、thinking 块）重建界面。
            存储的 assistant 文本可能带有 LLM 模仿的 [ID:msg_xxx] 前缀，
            回放前统一剥离（与 ActorAgent 实时输出同一规则）。
            """
            from Agent.ActorAgent import _strip_id_prefixes

            history = _session.memory_manager.get_context()
            serialized = []
            pending_tools = {}  # tool_use_id → 已追加的 tool 条目（等待结果回填）

            for msg in history:
                if not isinstance(msg, dict):
                    continue
                # Skip context-only messages (internal system messages)
                if CoreMessage.is_context_only(msg):
                    continue
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                ts = msg.get("timestamp", "")

                if isinstance(content, str):
                    if content.strip():
                        serialized.append({
                            "role": role, "kind": "text",
                            "text": _strip_id_prefixes(content), "timestamp": ts,
                        })
                    continue
                if not isinstance(content, list):
                    continue

                for item in content:
                    if not isinstance(item, dict):
                        continue
                    block_type = item.get("type")
                    if block_type == "text":
                        text = item.get("text", "")
                        if text.strip():
                            serialized.append({
                                "role": role, "kind": "text",
                                "text": _strip_id_prefixes(text), "timestamp": ts,
                            })
                    elif block_type == "thinking":
                        thinking = item.get("thinking", "")
                        if thinking.strip():
                            serialized.append({
                                "role": role, "kind": "thinking",
                                "text": _strip_id_prefixes(thinking)[:4000], "timestamp": ts,
                            })
                    elif block_type == "tool_use":
                        tool_input = item.get("input", {})
                        # 极简显示：只列参数值（逗号分隔），不带参数名
                        if isinstance(tool_input, dict):
                            args_brief = ", ".join(
                                (str(v)[:24] + "…") if len(str(v)) > 24 else str(v)
                                for v in tool_input.values()
                            )
                        else:
                            args_brief = str(tool_input)[:40]
                        entry = {
                            "role": "assistant", "kind": "tool",
                            "tool": item.get("name", "?"),
                            "args": args_brief,
                            "args_full": json.dumps(tool_input, ensure_ascii=False, indent=2)[:2000],
                            "result": None,
                            "is_error": False,
                            "timestamp": ts,
                        }
                        serialized.append(entry)
                        pending_tools[item.get("id", "")] = entry
                    elif block_type == "tool_result":
                        result_content = item.get("content", "")
                        if isinstance(result_content, str):
                            result_text = result_content
                        elif isinstance(result_content, list):
                            result_text = "\n".join(
                                rc.get("text", "")
                                for rc in result_content
                                if isinstance(rc, dict) and rc.get("type") == "text"
                            )
                        else:
                            result_text = str(result_content)
                        entry = pending_tools.pop(item.get("tool_use_id", ""), None)
                        if entry is not None:
                            entry["result"] = result_text[:4000]
                            entry["is_error"] = bool(item.get("is_error"))
                        else:
                            serialized.append({
                                "role": role, "kind": "tool",
                                "tool": "?", "args": "",
                                "result": result_text[:4000],
                                "is_error": bool(item.get("is_error")),
                                "timestamp": ts,
                            })

            # 会话中断残留的 tool_use（无结果）
            for entry in pending_tools.values():
                entry["result"] = "(no result — session interrupted)"

            # 只发送今天的内容（TUI 也只回放今天）；今天没有则回退最近 20 条。
            # 避免把 7 天保留窗内的全部历史（可能数 MB 的工具结果）传给前端。
            today_prefix = datetime.now().strftime("%Y-%m-%d")
            today_entries = [e for e in serialized if str(e.get("timestamp", "")).startswith(today_prefix)]
            replay = today_entries if today_entries else serialized[-20:]

            return {
                "jsonrpc": "2.0",
                "result": {"messages": replay},
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

        elif method == "agent.cancel_background_task":
            task_id = params.get("task_id")
            if task_id is None:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Missing 'task_id' parameter"},
                    "id": req_id,
                }
            try:
                cancelled = BackgroundTaskManager().cancel_task(int(task_id))
                return {
                    "jsonrpc": "2.0",
                    "result": {"cancelled": cancelled, "task_id": task_id},
                    "id": req_id,
                }
            except Exception as e:
                logger.error("Cancel background task failed: %s", e, exc_info=True)
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                    "id": req_id,
                }

        elif method == "agent.interrupt":
            # 仅在 agent 正在处理消息或队列里有待处理消息时才标记中断
            # （has_pending 覆盖"消息已入队但处理线程还没进入 process_message"的窗口，
            # 此时不设标志的话，用户想取消的消息会照常跑起来）
            if _session._processing or _session.has_pending():
                _session._interrupt_event.set()
                logger.info("Interrupt requested by user")
                interrupted = True
            else:
                interrupted = False
            return {
                "jsonrpc": "2.0",
                "result": {"interrupted": interrupted},
                "id": req_id
            }

        elif method == "agent.confirm_response":
            # 用户对危险命令确认的回复——立即放行/拒绝等待中的工具线程
            from Tools.confirmation_manager import confirmation_manager
            confirm_id = str(params.get("confirm_id", ""))
            approved = bool(params.get("approved", False))
            found = confirmation_manager.resolve(confirm_id, approved)
            logger.info("Confirmation %s resolved: approved=%s found=%s", confirm_id, approved, found)
            return {
                "jsonrpc": "2.0",
                "result": {"ok": found, "approved": approved},
                "id": req_id
            }

        elif method == "voice.get_status":
            from Gateway import voice_service
            return {
                "jsonrpc": "2.0",
                "result": voice_service.get_status(),
                "id": req_id,
            }

        elif method == "voice.set_config":
            from Gateway import voice_service
            p = {}
            for k in ("mic_consent", "asr_enabled", "wakeup_enabled"):
                if k in params:
                    p[k] = bool(params[k])
            status = voice_service.set_config(**p)
            # 根据开关启停唤醒监听（仅唤醒模式需要长驻监听器）
            if status.get("wakeup_enabled"):
                ok, msg = voice_service.start_listener(emit_voice_event)
                status["listener"] = {"ok": ok, "message": msg}
            else:
                voice_service.stop_listener()
                status["listener"] = {"ok": False, "message": "已停止"}
            return {
                "jsonrpc": "2.0",
                "result": status,
                "id": req_id,
            }

        elif method == "voice.download_model":
            from Gateway import voice_service
            started, msg = voice_service.download_model(emit_voice_event)
            return {
                "jsonrpc": "2.0",
                "result": {"started": started, "message": msg},
                "id": req_id,
            }

        elif method == "model.get_status":
            llm_cfg = config.get("model.large-language-model", {}) or {}
            api_key_env = llm_cfg.get("api_key_env", "")
            api_key = os.getenv(api_key_env) if api_key_env else ""
            masked = ""
            if api_key:
                masked = api_key[:6] + "***" + api_key[-4:] if len(api_key) > 12 else "***"
            return {
                "jsonrpc": "2.0",
                "result": {
                    "model": llm_cfg.get("model", ""),
                    "base_url": llm_cfg.get("base_url", ""),
                    "provider": llm_cfg.get("provider", "auto"),
                    "api_key_env": api_key_env,
                    "api_key_set": bool(api_key),
                    "api_key_masked": masked,
                    "history": config.get("model.model_history", []) or [],
                },
                "id": req_id,
            }

        elif method == "model.set_config":
            from Agent.LLMCore import LLMCore
            model = params.get("model")
            base_url = params.get("base_url")
            api_key = params.get("api_key")
            new_api_key_env = params.get("api_key_env")
            changed = []
            # 事务性：先记住旧值，热重载失败时整体回滚，避免 config.yaml
            # 停留在"已切换但实际没生效"的半套状态
            old_llm_cfg = dict(config.get("model.large-language-model", {}) or {})
            if model:
                config.set("model.large-language-model.model", model)
                changed.append("model")
            if base_url:
                config.set("model.large-language-model.base_url", base_url)
                changed.append("base_url")
            if new_api_key_env:
                config.set("model.large-language-model.api_key_env", new_api_key_env)
                changed.append("api_key_env")
            api_key_env = config.get("model.large-language-model.api_key_env", "ANTHROPIC_API_KEY")
            if api_key and not new_api_key_env and (model or base_url):
                # 手动切换且未显式指定目标 env 时，按目标匹配的历史条目决定
                # key 写进哪个变量——避免把新厂商的 key 写进当前厂商的变量
                # 名下，覆盖掉原厂商的密钥
                for h in (config.get("model.model_history", []) or []):
                    if not isinstance(h, dict) or not h.get("api_key_env"):
                        continue
                    if (h.get("model") == model) and (not base_url or h.get("base_url") == base_url):
                        api_key_env = h["api_key_env"]
                        config.set("model.large-language-model.api_key_env", api_key_env)
                        changed.append("api_key_env")
                        break
            if api_key:
                _set_dotenv_value(api_key_env, api_key)
                os.environ[api_key_env] = api_key
                changed.append("api_key")
            ok, err = LLMCore().reload()
            if not ok:
                # 回滚 config（.env 与运行中的 os.environ 不回滚：写掉的 key
                # 仍是有效密钥，留在新变量名下无害）；LLMCore 保持旧客户端，
                # 对话不中断——失败即回退到原模型继续工作
                for key, val in old_llm_cfg.items():
                    config.set(f"model.large-language-model.{key}", val)
                config.save()
                kind, msg = _classify_switch_failure(err)
                fallback = (old_llm_cfg.get("model") or "")
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "success": False,
                        "error_kind": kind,
                        "message": msg,
                        "fallback_model": fallback,
                    },
                    "id": req_id,
                }
            # 重载成功后才记入最近使用列表（同 model+base_url 去重置顶，
            # 最多 8 条），并随条目记录 api_key_env——不同厂商的 key 各存
            # 各的环境变量，回切时自动换用，互不覆盖
            if model or base_url:
                llm_cfg = config.get("model.large-language-model", {}) or {}
                entry = {
                    "model": llm_cfg.get("model", ""),
                    "base_url": llm_cfg.get("base_url", ""),
                    "api_key_env": llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"),
                }
                history = [
                    h for h in (config.get("model.model_history", []) or [])
                    if isinstance(h, dict)
                    and (h.get("model"), h.get("base_url")) != (entry["model"], entry["base_url"])
                ]
                history.insert(0, entry)
                config.set("model.model_history", history[:8])
                config.save()
            logger.info("Model config updated: %s", ", ".join(changed) if changed else "(none)")
            return {
                "jsonrpc": "2.0",
                "result": {"success": True, "changed": changed},
                "id": req_id,
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
    Background processing: drains the unified queue and processes items
    in FIFO order.  Consecutive messages are batched; commands are executed
    between message batches.

    Queue item format:
        {"type": "message", "data": str, "req_id": str}
        {"type": "command", "name": str, "params": dict, "req_id": str}
    """
    global _session
    global _processing_lock

    import sys as _sys
    _sys.stderr.write("[DIAG] _process_queued_messages: START\n")
    _sys.stderr.flush()

    async def _execute_command(cmd_item: dict):
        """Execute a queued command inside the processing event loop."""
        name = cmd_item["name"]
        params = cmd_item.get("params", {})
        cid = cmd_item.get("req_id", "")
        try:
            if name == "compact":
                await _session.compact_session(params.get("session_id", session_id))
            elif name == "clear_session":
                await _session.clear_session(params.get("session_id", session_id))
            else:
                logger.warning("Unknown command: %s", name)
                write_json({
                    "jsonrpc": "2.0", "method": "event",
                    "params": {
                        "type": "command.complete",
                        "payload": {"command": name, "success": False,
                                     "error": f"Unknown command: {name}"}
                    }
                })
                return

            # Emit updated token stats after command
            token_stats = _session.calculate_token_stats()
            write_json({
                "jsonrpc": "2.0", "method": "event",
                "params": {"type": "token.stats", "payload": token_stats}
            })
            write_json({
                "jsonrpc": "2.0", "method": "event",
                "params": {
                    "type": "command.complete",
                    "payload": {"command": name, "success": True}
                }
            })
            logger.info("Command '%s' executed successfully", name)
        except Exception as exc:
            logger.error("Command '%s' failed: %s", name, exc)
            write_json({
                "jsonrpc": "2.0", "method": "event",
                "params": {
                    "type": "command.complete",
                    "payload": {"command": name, "success": False, "error": str(exc)}
                }
            })

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while _session.has_pending():
            # ── Drain ALL pending items (messages + commands) ──
            items = _session.drain_pending_items()
            if not items:
                break

            last_answer = None
            i = 0
            while i < len(items):
                item = items[i]

                if item["type"] == "command":
                    # Execute command inline between message batches
                    loop.run_until_complete(_execute_command(item))
                    i += 1

                else:
                    # Collect consecutive message items into a batch
                    msg_items = []
                    while i < len(items) and items[i]["type"] == "message":
                        msg_items.append(items[i])
                        i += 1

                    combined = "\n".join(m["data"] for m in msg_items)

                    # Notify TUI of batching
                    if len(msg_items) > 1:
                        write_json({
                            "jsonrpc": "2.0", "method": "event",
                            "params": {
                                "type": "user.batch",
                                "payload": {"count": len(msg_items),
                                            "combined": combined[:100]}
                            }
                        })

                    # Process as one combined message
                    last_answer = loop.run_until_complete(
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
                            "payload": {"answer": last_answer}
                        }
                    })

            # ── Post-batch background watcher ─────────────────
            # Only runs when ALL items from this drain are processed
            # AND no new items arrived during processing.
            # If _session.has_pending() is True, the outer while loop
            # catches it and processes new items instead.
            if _session.has_pending():
                continue  # New items arrived, skip watcher

            if last_answer is None:
                continue  # No message was processed, skip watcher

            from configurationLoader import config
            watch_window = int(config.get("agent.background_watch_window", 1800))
            max_reactivations = int(config.get("agent.background_max_reactivations", 3))
            task_mgr = BackgroundTaskManager()

            if watch_window > 0 and task_mgr._reactivation_count < max_reactivations:
                start_watch = time.time()

                async def _bg_watcher():
                    while (time.time() - start_watch) < watch_window:
                        # 用户发了新消息或命令，立即退出 watcher
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
                                task_mgr.inject_into_memory(
                                    _session.memory_manager, bg_results
                                )
                            # Only queue synthetic message if callback didn't already
                            if not _session._bg_reactivation_queued:
                                synthetic_msg = (
                                    f"[System: background results arrived after previous answer] "
                                    f"{len(bg_results)} background task(s) completed. "
                                    f"Please review and respond if needed."
                                )
                                _session.memory_manager.append(CoreMessage.context_only(
                                    MessageSource.REACTIVATION, synthetic_msg,
                                ).to_dict())
                                with _session._batch_lock:
                                    _session.pending_items.append({
                                        "type": "message",
                                        "data": synthetic_msg,
                                        "req_id": "bg_reactivation",
                                    })
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
        # Re-check: items might have arrived during the lock release race window
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


def inject_voice_message(transcript: str) -> bool:
    """把语音识别结果作为带标签的 user 消息注入会话，并触发处理。"""
    global _session
    transcript = (transcript or "").strip()
    if not transcript:
        logger.warning("voice: empty transcript, skip inject")
        return False
    from Core.Message import CoreMessage, MessageSource
    annotation = (
        "[voice-recognize] This message originates from the user's voice "
        "recognition input. It may contain Chinese homophone misrecognitions "
        "or ASR errors — infer the intended meaning before acting."
    )
    _session.memory_manager.append(
        CoreMessage.context_only(MessageSource.VOICE, annotation).to_dict()
    )
    _session.queue_message(f"[voice-recognize] {transcript}", "voice")
    _start_processing("default")
    logger.info("voice: injected transcript into agent (%d chars)", len(transcript))
    return True


class _VoiceHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/message":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body or b"{}")
            transcript = str(data.get("message", "") or "")
        except Exception as exc:
            logger.warning("voice: bad HTTP payload: %s", exc)
            transcript = ""
        ok = inject_voice_message(transcript)
        resp = json.dumps({"ok": ok}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


def start_voice_http_server(port: int = 8999):
    """本地 HTTP 输入端点：POST /message 接收语音转写。"""
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _VoiceHTTPHandler)
    except OSError as exc:
        logger.warning("voice: HTTP endpoint failed to bind %s: %s", port, exc)
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="voice-http").start()
    logger.info("voice: HTTP endpoint listening on 127.0.0.1:%d", port)
    return server


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

        # 启动本地 HTTP 输入端点（语音转写由此注入 agent）
        try:
            voice_port = int(config.get("voice.http_port", 8999))
            start_voice_http_server(voice_port)
        except Exception as exc:
            logger.warning("voice: HTTP endpoint start failed: %s", exc)

        # 若上次会话开启了语音唤醒，启动时自动恢复长驻监听
        try:
            from Gateway import voice_service
            if config.get("voice.wakeup_enabled", False):
                ok, msg = voice_service.start_listener(emit_voice_event)
                logger.info("voice: auto-start listener → ok=%s %s", ok, msg)
        except Exception as exc:
            logger.warning("voice: auto-start failed: %s", exc)

        # Send ready event
        if not write_json({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": {}}
        }):
            log_crash("Failed to send gateway.ready event")
            sys.exit(1)

        # Start background stdin reader —— 必须先于初始 token 统计：
        # 首次记忆检索要等 embedding 模型冷加载（实测可达 56s），若先算 token
        # 统计，TUI 刚发出来的 agent.get_history 等请求会在管道里晾到超时。
        reader_thread = threading.Thread(target=_stdin_reader, daemon=True, name="stdin-reader")
        reader_thread.start()

        # Emit initial token stats —— 放后台线程，不阻塞请求主循环
        def _emit_initial_token_stats():
            try:
                stats = _session.calculate_token_stats()
            except Exception as exc:
                logger.warning("initial token stats failed: %s", exc)
                return
            write_json({
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "token.stats",
                    "payload": stats
                }
            })
            logger.info("Gateway ready, token stats: current=%s, cache_ratio=%.2f%%",
                        stats["current_tokens"], stats["prompt_cache_ratio"] * 100)

        threading.Thread(
            target=_emit_initial_token_stats, daemon=True, name="initial-token-stats"
        ).start()

        # Process requests from queue
        while _bg_reader_running:
            try:
                request = _request_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Dispatch request
            method = request.get("method", "?")
            sys.stderr.write(f"[TRACE] main loop dispatching: method={method}\n")
            sys.stderr.flush()
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
