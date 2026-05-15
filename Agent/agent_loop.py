"""
Agent 执行循环 — 供 Gateway 路径共享。
"""

import asyncio
import json

from Agent.ActorAgent import ActorAgent
from Agent.BackgroundTaskManager import BackgroundTaskManager
from Agent.ContextCompactor import ContextCompactor
from Agent.MicroCompactor import MicroCompactor
from Memory.MemoryManager import MemoryManager
from configurationLoader import config

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markup import escape as _rich_escape
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    _rich_escape = lambda x: x

FINAL_ANSWER_PROMPT = (
    "请基于以上所有信息，给出最终、完整、准确的回答。"
    "此阶段禁止调用工具，不要输出 JSON，只能直接输出给用户的自然语言答复。"
)

# ── 全局中断 ─────────────────────────────────────────
interrupt_requested = False


def request_interrupt():
    global interrupt_requested
    interrupt_requested = True


def check_interrupt():
    global interrupt_requested
    if interrupt_requested:
        interrupt_requested = False
        return True
    return False


# ── 工具函数 ─────────────────────────────────────────
def preview_text(text, max_length=160):
    compact = " ".join(str(text).split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3] + "..."


def format_block(label, content):
    body = str(content).strip()
    if not body:
        body = "(empty)"
    if RICH_AVAILABLE:
        return Panel(body, title=label, border_style="dim")
    return f"[{label}]\n{body}\n"


def normalize_usage(raw_usage):
    if not raw_usage:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    return {
        "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
        "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(raw_usage.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": int(raw_usage.get("cache_read_input_tokens", 0) or 0),
    }


def empty_token_usage():
    usage = normalize_usage({})
    usage["total_tokens"] = 0
    return usage


def add_usage(total_usage, step_usage):
    normalized = normalize_usage(step_usage)
    for key, value in normalized.items():
        total_usage[key] = total_usage.get(key, 0) + value
    total_usage["total_tokens"] = total_usage.get("input_tokens", 0) + total_usage.get("output_tokens", 0)
    return normalized


# ── 结果渲染 ─────────────────────────────────────────
def render_step_result(step_number, action, console=None, logger=None):
    action_type = action.get("type")

    if action_type == "error":
        error_msg = action.get("error", "未知错误")
        if logger:
            logger.log_error(f"Step {step_number}: {error_msg}")
        if RICH_AVAILABLE and console:
            console.print(f"[bold red]✗[/bold red] [red]{_rich_escape(error_msg)}[/red]")
        else:
            print(f"[step {step_number}] ERROR: {error_msg}")
        return

    if action_type == "tool_batch":
        tool_calls = action.get("tool_calls", [])
        if logger:
            for tc in tool_calls:
                logger.log_tool_call(step_number, tc["tool_name"], tc.get("arguments", {}), tc["result_preview"])
        if RICH_AVAILABLE and console:
            for tc in tool_calls:
                console.print(Panel(
                    _rich_escape(tc["result_preview"]),
                    title=f"[bold green]✓ Tool Result: {_rich_escape(tc['tool_name'])}[/bold green]",
                    border_style="green",
                    padding=(1, 2)
                ))
        else:
            for tc in tool_calls:
                print(f"Result ({tc['tool_name']}):\n{tc['result_preview']}\n")
        return

    if action_type == "tool":
        tool_name = action.get("tool_name", "unknown_tool")
        result_preview = action.get("result_preview") or "(no preview)"
        if logger:
            logger.log_tool_call(step_number, tool_name, action.get("arguments", {}), result_preview)
        if RICH_AVAILABLE and console:
            console.print(Panel(
                _rich_escape(result_preview),
                title="[bold green]✓ Tool Result[/bold green]",
                border_style="green",
                padding=(1, 2)
            ))
        else:
            print(f"Result:\n{result_preview}\n")
        return

    if action_type == "continue":
        pass


# ── 核心 Agent 循环 ──────────────────────────────────
async def run_agent(
    actor: ActorAgent,
    question: str,
    memory_manager: MemoryManager,
    logger=None,
    console=None,
    before_api_call=None,
):
    if logger:
        logger.log_user_input(question)

    turn_start_index = memory_manager.get_context_size()
    memory_manager.append({"role": "user", "content": question})

    max_depth = int(config.get("agent.maxDepth", 8) or 8)
    token_usage = empty_token_usage()
    last_answer = None

    def on_tool_call_start(tool_name, args, raw_reply, guard_result):
        nonlocal step
        if RICH_AVAILABLE and console:
            if raw_reply and config.get("ui.show_actor_output", True):
                console.print(Panel(
                    _rich_escape(raw_reply),
                    title=f"[bold blue]Actor (step {step + 1})[/bold blue]",
                    border_style="blue",
                    padding=(1, 2)
                ))
            arguments_summary = ActorAgent._summarize_arguments(args)
            console.print(
                f"\n[bold yellow]→ Tool Call[/bold yellow] "
                f"[cyan]{_rich_escape(tool_name)}[/cyan] {_rich_escape(arguments_summary)}"
            )
            console.print("[dim]⏳ Executing...[/dim]")
        else:
            if raw_reply and config.get("ui.show_actor_output", True):
                print(f"[Actor (step {step + 1})]")
                print(raw_reply)
                print()
            print(f"\n[step {step + 1}] Tool: {tool_name}")
            arguments_summary = ActorAgent._summarize_arguments(args)
            print(f"Args: {arguments_summary}")
            print("⏳ Executing...")

    def notify_before_api_call(extra_system_prompt=None):
        if before_api_call is None:
            return
        snapshot = actor.build_messages_with_document(extra_system_prompt)
        before_api_call({
            "step": step + 1,
            "messages": snapshot["messages"],
            "document": snapshot["document"],
            "extra_system_prompt": extra_system_prompt,
            "token_usage": dict(token_usage),
        })

    # ── Snip 提醒 ────────────────────────────────────
    snip_enabled = bool(config.get("agent.snip.enabled", False))
    snip_threshold = int(config.get("agent.snip.reminder_threshold_tokens", 40000))

    def _get_snip_reminder():
        if not snip_enabled:
            return None
        total = token_usage.get("total_tokens", 0)
        if total < snip_threshold:
            return None
        return (
            f"[系统提示] 当前上下文累计 Token 已达到 {total}，超过限制 {snip_threshold}。"
            f"建议使用 Snip 工具裁剪你认为不再有用的历史消息。"
            f"消息的 ID 前缀格式为 [ID:msg_xxxxxxxx]，传入这些 ID 即可删除对应消息。"
        )

    # ── 上下文自动压缩 ──────────────────────────────
    compact_enabled = bool(config.get("agent.context_compact.enabled", False))
    compact_threshold = int(config.get("agent.context_compact.threshold_tokens", 80000))
    _compactor = None
    _compacted_this_turn = False
    _last_api_input_tokens = None

    def maybe_compact_context(step_num, force=False):
        nonlocal _compactor, _compacted_this_turn
        if not compact_enabled:
            return False
        if _compacted_this_turn and not force:
            return False
        messages = memory_manager.get_context()
        if _compactor is None:
            _compactor = ContextCompactor()
        estimated = _compactor.estimate_tokens(messages)
        if _last_api_input_tokens is not None and _last_api_input_tokens > estimated:
            estimated = _last_api_input_tokens
        if estimated < compact_threshold and not force:
            return False
        if logger:
            logger.log_memory_event(
                f"Context auto-compact triggered at step {step_num + 1} "
                f"(estimated {estimated} tokens, threshold {compact_threshold})"
            )
        if RICH_AVAILABLE and console:
            console.print(f"[yellow]⚡ Context tokens ({estimated}) exceed threshold ({compact_threshold}), compacting...[/yellow]")
        else:
            print(f"[*] Context tokens ({estimated}) exceed threshold ({compact_threshold}), compacting...")
        try:
            summary = _compactor.compact(messages)
            if not summary or len(summary) < 20:
                raise ValueError("Compaction returned empty or too-short summary")
        except Exception as e:
            if logger:
                logger.log_error(f"Context compaction failed: {e}, falling back to truncation")
            max_msgs = int(config.get("agent.max_context_messages", 20) or 20)
            recent = messages[-max_msgs:]
            memory_manager.clear_context()
            for msg in recent:
                memory_manager.append(msg)
            return True
        memory_manager.clear_context()
        compact_message = {
            "role": "user",
            "content": f"[上下文自动压缩 - 对话过长已自动摘要]\n\n{summary}\n\n---\n请根据以上摘要继续执行任务。",
        }
        memory_manager.append(compact_message)
        _compacted_this_turn = True
        if logger:
            logger.log_memory_event(f"Context compacted: {len(messages)} messages → {len(summary)} chars summary")
        if RICH_AVAILABLE and console:
            console.print(f"[green]✓ Context compacted: {len(messages)} messages → {len(summary)} chars summary[/green]")
        else:
            print(f"[+] Context compacted: {len(messages)} messages → {len(summary)} chars summary")
        return True

    # ── 微压缩 ──────────────────────────────────────
    micro_compact_enabled = bool(config.get("agent.micro_compact.enabled", True))
    micro_compact_age = float(config.get("agent.micro_compact.age_threshold_hours", 1))
    micro_compact_keep = int(config.get("agent.micro_compact.keep_tool_results", 5))
    _micro_compacted_this_turn = False

    def maybe_micro_compact(step_num):
        nonlocal _micro_compacted_this_turn
        if not micro_compact_enabled:
            return False
        if _micro_compacted_this_turn:
            return False
        messages = memory_manager.get_context()
        compacted = MicroCompactor.compact(
            messages, age_threshold_hours=micro_compact_age, keep_tool_results=micro_compact_keep,
        )
        if compacted is messages:
            return False
        removed_count = len(messages) - len(compacted)
        memory_manager.clear_context()
        for msg in compacted:
            memory_manager.append(msg)
        _micro_compacted_this_turn = True
        if logger:
            logger.log_memory_event(f"Micro-compact at step {step_num + 1}: removed {removed_count} older tool results")
        if RICH_AVAILABLE and console:
            console.print(f"[dim]📦 Micro-compact: {removed_count} older tool results → placeholder[/dim]")
        return True

    def _drain_background():
        bg_results = BackgroundTaskManager().drain_pending()
        if bg_results:
            BackgroundTaskManager().inject_into_memory(memory_manager, bg_results)
            if RICH_AVAILABLE and console:
                console.print(f"[cyan]📥 {len(bg_results)} background task(s) completed, results injected into context[/cyan]")
            elif logger:
                logger.info(f"{len(bg_results)} background task(s) completed and injected")
        return bg_results

    # ── 主循环 ──────────────────────────────────────
    for step in range(max_depth):
        if check_interrupt():
            if RICH_AVAILABLE and console:
                console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
            else:
                print("[Execution interrupted by user]")
            return {
                "final_answer": "执行已被用户中断。",
                "interrupted": True,
                "token_usage": dict(token_usage),
            }

        _drain_background()
        maybe_compact_context(step)
        maybe_micro_compact(step)
        notify_before_api_call()
        action = await actor.act(on_tool_call_start=on_tool_call_start, extra_system_prompt=_get_snip_reminder())
        add_usage(token_usage, action.get("usage"))
        _last_api_input_tokens = (action.get("usage") or {}).get("input_tokens")
        render_step_result(step + 1, action, console=console, logger=logger)

        if action.get("type") == "answer":
            last_answer = action.get("answer")
            if BackgroundTaskManager().has_pending():
                _drain_background()
                continue
            break

    # Final drain: background tasks may have completed during the last step
    _drain_background()

    final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)
    used_fallback_answer = False
    try:
        notify_before_api_call(FINAL_ANSWER_PROMPT)
        final_answer = actor.query(final_prompt)
        add_usage(token_usage, actor.get_last_usage())
        if not str(final_answer or "").strip() and last_answer:
            final_answer = last_answer
            used_fallback_answer = True
    except Exception as e:
        if logger:
            logger.log_error(str(e))
        if not last_answer:
            raise
        final_answer = last_answer
        used_fallback_answer = True

    if not used_fallback_answer:
        memory_manager.append({"role": "assistant", "content": final_answer})

    if logger:
        logger.log_assistant_response(final_answer)

    turn_transcript = memory_manager.get_turn_messages_since(turn_start_index)
    review_events = memory_manager.post_turn_review(turn_transcript)
    housekeeping_events = memory_manager.detectOverflow()

    if logger:
        for event in list(review_events or []) + list(housekeeping_events or []):
            logger.log_memory_event(event)

    return {
        "final_answer": final_answer,
        "review_events": review_events,
        "housekeeping_events": housekeeping_events,
        "token_usage": dict(token_usage),
    }
