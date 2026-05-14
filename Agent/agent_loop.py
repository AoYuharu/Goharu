"""
Agent 执行循环 — 供 Gateway 和 answer_review 路径共享。
"""

import asyncio
import json

from Agent.ActorAgent import ActorAgent
from Agent.ContextCompactor import ContextCompactor
from Agent.ReflectionAgent import ReflectionAgent
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
        guard_logs = action.get("guard_logs")

        if logger:
            logger.log_tool_call(step_number, tool_name, action.get("arguments", {}), result_preview)

        if RICH_AVAILABLE and console:
            console.print(Panel(
                _rich_escape(result_preview),
                title="[bold green]✓ Tool Result[/bold green]",
                border_style="green",
                padding=(1, 2)
            ))
            if config.get("ui.verbose", False) and guard_logs:
                console.print(f"  [dim italic]Guard: {'; '.join(guard_logs)}[/dim italic]")
        else:
            print(f"Result:\n{result_preview}\n")
            if config.get("ui.verbose", False) and guard_logs:
                print(f"Guard: {'; '.join(guard_logs)}\n")
        return

    # continue 类型 nothing to render
    if action_type == "continue":
        pass


# ── 核心 Agent 循环 ──────────────────────────────────
async def run_agent(
    actor: ActorAgent,
    reflector: ReflectionAgent,
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

    show_reflections = config.get("ui.show_reflections", False)
    reflection_mode = config.get("agent.reflection_mode", "adaptive")
    reflections = []
    last_answer = None
    max_depth = int(config.get("agent.maxDepth", 8) or 8)
    max_reflection_steps = 5
    token_usage = empty_token_usage()

    consecutive_rejections = 0
    last_tool_call_step = -1
    reflection_count = 0

    def should_reflect(step_num, action_type):
        if reflection_count >= max_reflection_steps:
            return False
        if reflection_mode == "always":
            return True
        if reflection_mode == "never":
            return False
        if action_type == "answer":
            return True
        if step_num >= max_depth - 1:
            return True
        if step_num > 0 and step_num % 3 == 0:
            return True
        return False

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

    # ── 上下文自动压缩 ──────────────────────────────
    compact_enabled = bool(config.get("agent.context_compact.enabled", False))
    compact_threshold = int(config.get("agent.context_compact.threshold_tokens", 80000))
    compact_before_step = bool(config.get("agent.context_compact.trigger_before_step", True))
    _compactor = None
    _compacted_this_turn = False

    def maybe_compact_context(step_num, force=False):
        nonlocal _compactor, _compacted_this_turn
        if not compact_enabled:
            return False
        if _compacted_this_turn and not force:
            return False
        if not compact_before_step and not force:
            return False

        messages = memory_manager.get_context()
        if _compactor is None:
            _compactor = ContextCompactor()
        estimated = _compactor.estimate_tokens(messages)

        if estimated < compact_threshold and not force:
            return False

        if logger:
            logger.log_memory_event(
                f"Context auto-compact triggered at step {step_num + 1} "
                f"(estimated {estimated} tokens, threshold {compact_threshold})"
            )
        if RICH_AVAILABLE and console:
            console.print(
                f"[yellow]⚡ Context tokens ({estimated}) exceed threshold ({compact_threshold}), "
                f"auto-compacting...[/yellow]"
            )
        else:
            print(f"[*] Context tokens ({estimated}) exceed threshold ({compact_threshold}), compacting...")

        try:
            summary = _compactor.compact(messages)
            if not summary or len(summary) < 20:
                raise ValueError("Compaction returned empty or too-short summary")
        except Exception as e:
            if logger:
                logger.log_error(f"Context compaction failed: {e}, falling back to truncation")
            if RICH_AVAILABLE and console:
                console.print(f"[red]Compaction failed: {e}, using hard truncation[/red]")
            else:
                print(f"[-] Compaction failed: {e}, using hard truncation")
            # 回退：硬截断，保留最近 max_context_messages 条
            max_msgs = int(config.get("agent.max_context_messages", 20) or 20)
            recent = messages[-max_msgs:]
            memory_manager.clear_context()
            for msg in recent:
                memory_manager.append(msg)
            return True

        # 成功压缩：清空上下文并注入摘要
        memory_manager.clear_context()
        compact_message = {
            "role": "user",
            "content": f"[上下文自动压缩 - 对话过长已自动摘要]\n\n{summary}\n\n---\n请根据以上摘要继续执行任务。",
        }
        memory_manager.append(compact_message)
        _compacted_this_turn = True

        if logger:
            logger.log_memory_event(
                f"Context compacted: {len(messages)} messages → "
                f"{len(summary)} chars summary"
            )
        if RICH_AVAILABLE and console:
            console.print(
                f"[green]✓ Context compacted: {len(messages)} messages → "
                f"{len(summary)} chars summary[/green]"
            )
        else:
            print(f"[+] Context compacted: {len(messages)} messages → {len(summary)} chars summary")
        return True

    if RICH_AVAILABLE and console:
        for step in range(max_depth):
            if check_interrupt():
                console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
                return {
                    "final_answer": "执行已被用户中断。",
                    "reflections": reflections,
                    "interrupted": True,
                    "token_usage": dict(token_usage),
                }

            maybe_compact_context(step)
            notify_before_api_call()
            action = await actor.act(on_tool_call_start=on_tool_call_start)
            add_usage(token_usage, action.get("usage"))
            render_step_result(step + 1, action, console=console, logger=logger)

            if action.get("type") in ["tool", "tool_batch"]:
                last_tool_call_step = step
                consecutive_rejections = 0

            if action.get("type") == "answer":
                last_answer = action.get("answer")

            if should_reflect(step, action.get("type")):
                reflection = reflector.reflect(
                    question=question,
                    history=memory_manager.get_context(),
                    memory_markdown=memory_manager.get_memory_markdown(),
                    soul_markdown=memory_manager.get_soul_markdown(),
                )
                reflections.append(reflection)
                reflection_count += 1
                if logger:
                    logger.log_reflection(step + 1, reflection)

                console.print(Panel(
                    _rich_escape(reflection),
                    title=f"[bold magenta]Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})[/bold magenta]",
                    border_style="magenta",
                    padding=(1, 2)
                ))

                if "可以给出最终回答" in reflection:
                    consecutive_rejections = 0
                    if action.get("type") == "tool":
                        memory_manager.append({
                            "role": "user",
                            "content": f"[Reflection] {reflection}\n\n✓ 信息已充分，现在请基于以上所有信息给出完整的最终回答。禁止继续调用工具。",
                        })
                        continue
                    break
                elif "需要继续调用工具" in reflection:
                    consecutive_rejections += 1
                    if consecutive_rejections >= 10:
                        console.print("[bold red]⚠ Reflection 连续 10 次拒绝，可能陷入死循环[/bold red]")
                        console.print("[yellow]问题分析：[/yellow]")
                        console.print(f"  - 最后一次工具调用在 step {last_tool_call_step + 1}")
                        console.print(f"  - 当前 step {step + 1}")
                        console.print(f"  - Actor 可能没有响应 Reflection 的要求")
                        console.print("\n[yellow]建议：[/yellow]")
                        console.print("  1. 检查 Actor prompt 是否足够强制")
                        console.print("  2. 检查模型是否理解工具调用格式")
                        console.print("  3. 尝试更明确的用户指令\n")
                        return {
                            "final_answer": f"执行失败：Reflection 连续 {consecutive_rejections} 次拒绝，可能 Actor 没有正确调用工具。\n\n最后的 Reflection：\n{reflection}",
                            "reflections": reflections,
                            "deadlock": True,
                            "token_usage": dict(token_usage),
                        }
                    feedback = f"[Reflection] {reflection}\n\n⚠ 警告：你必须调用工具来验证。不要编造结果。请立即调用必要的工具。"
                    memory_manager.append({"role": "user", "content": feedback})
                    continue
                elif action.get("type") == "answer":
                    consecutive_rejections += 1
                    memory_manager.append({
                        "role": "user",
                        "content": f"[Reflection] {reflection}\n\n你的回答需要更多验证，请继续执行必要的工具调用。",
                    })
                    continue
            elif action.get("type") == "answer":
                if reflection_mode == "never":
                    break
                if reflection_count < max_reflection_steps:
                    reflection = reflector.reflect(
                        question=question,
                        history=memory_manager.get_context(),
                        memory_markdown=memory_manager.get_memory_markdown(),
                        soul_markdown=memory_manager.get_soul_markdown(),
                    )
                    reflections.append(reflection)
                    reflection_count += 1
                    if logger:
                        logger.log_reflection(step + 1, reflection)

                    console.print(Panel(
                        _rich_escape(reflection),
                        title=f"[bold magenta]Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})[/bold magenta]",
                        border_style="magenta",
                        padding=(1, 2)
                    ))

                    if "可以给出最终回答" in reflection:
                        break
                    else:
                        consecutive_rejections += 1
                        memory_manager.append({
                            "role": "user",
                            "content": f"[Reflection] {reflection}\n\n请继续执行必要的操作。",
                        })
                        continue
                else:
                    break
    else:
        for step in range(max_depth):
            if check_interrupt():
                print("[Execution interrupted by user]")
                return {
                    "final_answer": "执行已被用户中断。",
                    "reflections": reflections,
                    "interrupted": True,
                    "token_usage": dict(token_usage),
                }

            maybe_compact_context(step)
            notify_before_api_call()
            action = await actor.act(on_tool_call_start=on_tool_call_start)
            add_usage(token_usage, action.get("usage"))
            render_step_result(step + 1, action, console=console, logger=logger)

            if action.get("type") in ["tool", "tool_batch"]:
                last_tool_call_step = step
                consecutive_rejections = 0

            if action.get("type") == "answer":
                last_answer = action.get("answer")

            if should_reflect(step, action.get("type")):
                reflection = reflector.reflect(
                    question=question,
                    history=memory_manager.get_context(),
                    memory_markdown=memory_manager.get_memory_markdown(),
                    soul_markdown=memory_manager.get_soul_markdown(),
                )
                reflections.append(reflection)
                reflection_count += 1
                if logger:
                    logger.log_reflection(step + 1, reflection)

                print(format_block(f"Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})", reflection))

                if "可以给出最终回答" in reflection:
                    consecutive_rejections = 0
                    if action.get("type") == "tool":
                        memory_manager.append({
                            "role": "user",
                            "content": f"[Reflection] {reflection}\n\n✓ 信息已充分，现在请基于以上所有信息给出完整的最终回答。禁止继续调用工具。",
                        })
                        continue
                    break
                elif "需要继续调用工具" in reflection:
                    consecutive_rejections += 1
                    if consecutive_rejections >= 10:
                        print("⚠ Reflection 连续 10 次拒绝，可能陷入死循环")
                        print(f"最后一次工具调用在 step {last_tool_call_step + 1}")
                        print(f"当前 step {step + 1}")
                        print("Actor 可能没有响应 Reflection 的要求\n")
                        return {
                            "final_answer": f"执行失败：Reflection 连续 {consecutive_rejections} 次拒绝。\n\n最后的 Reflection：\n{reflection}",
                            "reflections": reflections,
                            "deadlock": True,
                            "token_usage": dict(token_usage),
                        }
                    feedback = f"[Reflection] {reflection}\n\n⚠ 警告：你必须调用工具来验证。不要编造结果。请立即调用必要的工具。"
                    memory_manager.append({"role": "user", "content": feedback})
                    continue
                elif action.get("type") == "answer":
                    consecutive_rejections += 1
                    memory_manager.append({
                        "role": "user",
                        "content": f"[Reflection] {reflection}\n\n你的回答需要更多验证，请继续执行必要的工具调用。",
                    })
                    continue
            elif action.get("type") == "answer":
                if reflection_mode == "never":
                    break
                if reflection_count < max_reflection_steps:
                    reflection = reflector.reflect(
                        question=question,
                        history=memory_manager.get_context(),
                        memory_markdown=memory_manager.get_memory_markdown(),
                        soul_markdown=memory_manager.get_soul_markdown(),
                    )
                    reflections.append(reflection)
                    reflection_count += 1
                    if logger:
                        logger.log_reflection(step + 1, reflection)

                    print(format_block(f"Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})", reflection))

                    if "可以给出最终回答" in reflection:
                        break
                    else:
                        consecutive_rejections += 1
                        memory_manager.append({
                            "role": "user",
                            "content": f"[Reflection] {reflection}\n\n请继续执行必要的操作。",
                        })
                        continue
                else:
                    break

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
        "reflections": reflections,
        "review_events": review_events,
        "housekeeping_events": housekeeping_events,
        "token_usage": dict(token_usage),
    }
