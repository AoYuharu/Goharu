import asyncio
import sys

from Agent.ActorAgent import ActorAgent
from Agent.ReflectionAgent import ReflectionAgent
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from configurationLoader import config
from Logger import ConversationLogger

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.markdown import Markdown
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

APP_NAME = "TableHelper"
FINAL_ANSWER_PROMPT = "请基于以上所有信息，给出最终、完整、准确的回答。此阶段禁止调用工具，不要输出 JSON，只能直接输出给用户的自然语言答复。"
HELP_TEXT = """Commands:
  /help      Show this help
  /multi     Enter multiline input mode
  /context   Show the current context that will be sent to the API
  /sysprompt Show the system prompt sections
  /clear     Clear the current session context
  /interrupt Interrupt the current agent execution
  /exit      Exit the app
  /quit      Exit the app
  exit       Exit the app

Multiline mode:
  /send    Submit drafted text
  /cancel  Discard drafted text
"""

console = Console() if RICH_AVAILABLE else None
logger = None
interrupt_requested = False  # 全局中断标志


def request_interrupt():
    """请求中断当前执行"""
    global interrupt_requested
    interrupt_requested = True


def check_interrupt():
    """检查是否有中断请求"""
    global interrupt_requested
    if interrupt_requested:
        interrupt_requested = False
        return True
    return False


def print_message(text, style=""):
    if RICH_AVAILABLE and console:
        console.print(text, style=style)
    else:
        print(text)


def format_block(label, content):
    body = str(content).strip()
    if not body:
        body = "(empty)"
    if RICH_AVAILABLE and console:
        return Panel(body, title=label, border_style="dim")
    return f"[{label}]\n{body}\n"


def preview_text(text, max_length=160):
    compact = " ".join(str(text).split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3] + "..."


def _extract_tool_name(tool):
    if hasattr(tool, "name"):
        return str(getattr(tool, "name"))
    if isinstance(tool, dict) and tool.get("name"):
        return str(tool["name"])
    return preview_text(tool)


def list_tool_names(tools_result):
    tools = getattr(tools_result, "tools", tools_result)
    if not tools:
        return []
    return [_extract_tool_name(tool) for tool in tools]


def get_model_display_name():
    llm_config = config.get("model.large-language-model", {}) or {}
    provider = llm_config.get("provider", "unknown")
    model_name = llm_config.get("model")
    if provider == "local_hf":
        model_name = (llm_config.get("local_hf", {}) or {}).get("name") or model_name
    if model_name:
        return f"{provider} / {model_name}"
    return provider


def print_startup_banner(memory_manager, runtime_status, tool_names):
    if RICH_AVAILABLE and console:
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan", width=12)
        table.add_column(style="white")
        table.add_row("Model", get_model_display_name())
        table.add_row("Tools", runtime_status)
        table.add_row("Available", ', '.join(tool_names) if tool_names else '(none)')
        console.print(Panel(table, title=f"[bold cyan]{APP_NAME}[/bold cyan]", border_style="cyan"))
        console.print("[dim]Type /help for commands[/dim]\n")
    else:
        print(f"=== {APP_NAME} ===")
        print(f"model      : {get_model_display_name()}")
        print(f"tools mode : {runtime_status}")
        print(f"tools      : {', '.join(tool_names) if tool_names else '(none)'}")
        print(f"memory     : {memory_manager.long_term.index_path}")
        print(f"user       : {memory_manager.user_profile.path}")
        print(f"soul       : {memory_manager.long_term.soul_path}")
        if memory_manager.long_term.soul_created_this_run:
            print("soul status: created default SOUL.md")
        print("commands   : /help /multi /exit")
        print()


def print_help():
    if RICH_AVAILABLE and console:
        console.print(Panel(HELP_TEXT, title="Help", border_style="cyan"))
    else:
        print(format_block("help", HELP_TEXT))


def print_context(actor):
    """显示即将发送给 API 的上下文结构"""
    messages = actor.build_messages()

    if RICH_AVAILABLE and console:
        from rich.syntax import Syntax
        import json

        # 格式化消息
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            content_preview = content[:200] + "..." if len(content) > 200 else content
            formatted.append({
                "role": role,
                "content_length": len(content),
                "content_preview": content_preview
            })

        json_str = json.dumps(formatted, ensure_ascii=False, indent=2)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title="[bold cyan]Context Structure[/bold cyan]", border_style="cyan"))
        console.print(f"[dim]Total messages: {len(messages)}, Total characters: {sum(len(m.get('content', '')) for m in messages)}[/dim]\n")
    else:
        print("=== Context Structure ===")
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            print(f"[{i+1}] {role}: {len(content)} chars")
            print(f"    {content[:100]}...")
        print(f"\nTotal: {len(messages)} messages\n")


def print_system_prompt(actor):
    """显示系统提示词的完整内容"""
    messages = actor.build_messages()

    # 提取所有 system 角色的消息
    system_messages = [msg for msg in messages if msg.get("role") == "system"]

    if not system_messages:
        if RICH_AVAILABLE and console:
            console.print("[yellow]No system messages found[/yellow]\n")
        else:
            print("No system messages found\n")
        return

    if RICH_AVAILABLE and console:
        from rich.markdown import Markdown

        for i, msg in enumerate(system_messages, 1):
            content = msg.get("content", "")
            # 尝试将内容渲染为 Markdown
            try:
                md = Markdown(content)
                console.print(Panel(
                    md,
                    title=f"[bold cyan]System Prompt Section {i}/{len(system_messages)}[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2)
                ))
            except Exception:
                # 如果 Markdown 渲染失败，直接显示文本
                console.print(Panel(
                    content,
                    title=f"[bold cyan]System Prompt Section {i}/{len(system_messages)}[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2)
                ))

        total_chars = sum(len(msg.get("content", "")) for msg in system_messages)
        console.print(f"[dim]Total system sections: {len(system_messages)}, Total characters: {total_chars}[/dim]\n")
    else:
        print("=== System Prompt ===")
        for i, msg in enumerate(system_messages, 1):
            content = msg.get("content", "")
            print(f"\n--- Section {i}/{len(system_messages)} ---")
            print(content)
            print(f"--- End of Section {i} ({len(content)} chars) ---\n")

        total_chars = sum(len(msg.get("content", "")) for msg in system_messages)
        print(f"Total: {len(system_messages)} sections, {total_chars} characters\n")


def should_exit(user_input):
    normalized = str(user_input).strip().lower()
    return normalized in {"exit", "/exit", "/quit"}


def read_multiline_input():
    print_message("[dim]Multiline mode. Use /send to submit, /cancel to discard.[/dim]\n" if RICH_AVAILABLE else "[input] Multiline mode. Use /send to submit, /cancel to discard.\n")
    lines = []
    while True:
        try:
            line = input("... > ")
        except EOFError:
            return None
        command = line.strip()
        if command == "/send":
            return "\n".join(lines).strip()
        if command == "/cancel":
            print_message("[dim]Multiline draft discarded.[/dim]\n" if RICH_AVAILABLE else "[input] Multiline draft discarded.\n")
            return None
        lines.append(line)


def read_user_message(actor=None, memory_manager=None):
    """
    读取用户消息，支持特殊命令

    Args:
        actor: ActorAgent 实例，用于 /context 和 /sysprompt 命令
        memory_manager: MemoryManager 实例，用于 /clear 命令
    """
    while True:
        try:
            if RICH_AVAILABLE and console:
                user_input = console.input("[bold cyan]You[/bold cyan] > ")
            else:
                user_input = input("You > ")
        except EOFError:
            return "/exit"
        command = user_input.strip()

        if command == "/help":
            print_help()
            continue
        if command == "/multi":
            drafted = read_multiline_input()
            if drafted:
                return drafted
            continue
        if command == "/context":
            if actor:
                print_context(actor)
            else:
                print_message("[yellow]Context not available yet[/yellow]")
            continue
        if command == "/sysprompt":
            if actor:
                print_system_prompt(actor)
            else:
                print_message("[yellow]System prompt not available yet[/yellow]")
            continue
        if command == "/clear":
            if memory_manager:
                memory_manager.clear_context()
                if RICH_AVAILABLE and console:
                    console.print("[green]✓ Current session context cleared[/green]")
                else:
                    print("✓ Current session context cleared")
            else:
                print_message("[yellow]Memory manager not available yet[/yellow]")
            continue
        if command == "/interrupt":
            request_interrupt()
            print_message("[yellow]Interrupt requested. Will stop at next step.[/yellow]")
            continue
        if should_exit(command):
            return command
        if not command:
            continue
        return user_input


def render_step(step_number, action):
    action_type = action.get("type")
    raw_reply = action.get("raw_reply", "")

    # 显示 Actor 的原始输出（思考过程）
    if raw_reply and config.get("ui.show_actor_output", True):
        if RICH_AVAILABLE and console:
            console.print(Panel(
                raw_reply,
                title=f"[bold blue]Actor (step {step_number})[/bold blue]",
                border_style="blue",
                padding=(1, 2)
            ))
        else:
            print(f"[Actor (step {step_number})]")
            print(raw_reply)
            print()

    # 处理错误类型
    if action_type == "error":
        error_msg = action.get("error", "未知错误")
        if logger:
            logger.log_error(f"Step {step_number}: {error_msg}")
        if RICH_AVAILABLE and console:
            console.print(f"[bold red]✗[/bold red] [red]{error_msg}[/red]")
        else:
            print(f"[step {step_number}] ERROR: {error_msg}")
        return

    # 渲染工具调用
    if action_type != "tool":
        return

    tool_name = action.get("tool_name", "unknown_tool")
    arguments_summary = action.get("arguments_summary", "{}")
    result_preview = action.get("result_preview") or "(no preview)"
    guard_logs = action.get("guard_logs")

    if logger:
        logger.log_tool_call(step_number, tool_name, action.get("arguments", {}), result_preview)

    if RICH_AVAILABLE and console:
        # 显示工具调用
        console.print(f"\n[bold yellow]→ Tool Call[/bold yellow] [cyan]{tool_name}[/cyan] {arguments_summary}")

        # 始终显示工具返回结果（不再依赖 verbose）
        console.print(Panel(
            result_preview,
            title="[bold green]Tool Result[/bold green]",
            border_style="green",
            padding=(1, 2)
        ))

        # 显示防护日志（verbose 模式）
        if config.get("ui.verbose", False) and guard_logs:
            console.print(f"  [dim italic]Guard: {'; '.join(guard_logs)}[/dim italic]")
    else:
        label = f"step {step_number}"
        print(f"\n[{label}] Tool: {tool_name}")
        print(f"Args: {arguments_summary}")
        print(f"Result:\n{result_preview}\n")
        if config.get("ui.verbose", False) and guard_logs:
            print(f"Guard: {'; '.join(guard_logs)}\n")


async def run_agent(
    actor: ActorAgent,
    reflector: ReflectionAgent,
    question: str,
    memory_manager: MemoryManager,
):
    if logger:
        logger.log_user_input(question)

    turn_start_index = memory_manager.get_context_size()
    memory_manager.append({"role": "user", "content": question})

    show_reflections = config.get("ui.show_reflections", False)
    reflection_mode = config.get("mcp.reflection_mode", "adaptive")
    reflections = []
    last_answer = None
    max_depth = int(config.get("mcp.maxDepth", 8) or 8)
    max_reflection_steps = 5  # Reflection 最大步数

    # 死循环检测
    consecutive_rejections = 0
    last_tool_call_step = -1
    reflection_count = 0  # Reflection 计数器

    def should_reflect(step_num, action_type):
        # 如果已经达到 Reflection 最大次数，不再 reflect
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

    if RICH_AVAILABLE and console:
        # 不使用 Progress，直接显示输出
        for step in range(max_depth):
            # 检查中断
            if check_interrupt():
                console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
                return {
                    "final_answer": "执行已被用户中断。",
                    "reflections": reflections,
                    "interrupted": True,
                }

            action = await actor.act()
            render_step(step + 1, action)

            # 记录工具调用
            if action.get("type") == "tool":
                last_tool_call_step = step
                consecutive_rejections = 0  # 重置拒绝计数

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
                reflection_count += 1  # 增加 Reflection 计数
                if logger:
                    logger.log_reflection(step + 1, reflection)

                # 始终显示 Reflection（用户需要看到思考过程）
                console.print(Panel(
                    reflection,
                    title=f"[bold magenta]Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})[/bold magenta]",
                    border_style="magenta",
                    padding=(1, 2)
                ))

                # Reflection Agent 拥有最终决策权
                if "可以给出最终回答" in reflection:
                    consecutive_rejections = 0
                    break
                elif "需要继续调用工具" in reflection:
                    consecutive_rejections += 1

                    # 死循环检测：连续 10 次拒绝
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
                        }

                    # Reflection 要求继续，将反思内容作为指令反馈给 Actor
                    feedback = f"[Reflection] {reflection}\n\n⚠ 警告：你必须调用工具来验证。不要编造结果。请立即调用必要的工具。"
                    memory_manager.append({
                        "role": "user",
                        "content": feedback,
                    })
                    continue
                elif action.get("type") == "answer":
                    consecutive_rejections += 1
                    # Actor 给出了答案但 Reflection 没有明确同意，拒绝结束
                    memory_manager.append({
                        "role": "user",
                        "content": f"[Reflection] {reflection}\n\n你的回答需要更多验证，请继续执行必要的工具调用。",
                    })
                    continue
            elif action.get("type") == "answer":
                # 没有 reflection 但 Actor 给出答案，强制进行一次 reflection
                if reflection_count < max_reflection_steps:
                    reflection = reflector.reflect(
                        question=question,
                        history=memory_manager.get_context(),
                        memory_markdown=memory_manager.get_memory_markdown(),
                        soul_markdown=memory_manager.get_soul_markdown(),
                    )
                    reflections.append(reflection)
                    reflection_count += 1  # 增加 Reflection 计数
                    if logger:
                        logger.log_reflection(step + 1, reflection)

                    # 始终显示 Reflection
                    console.print(Panel(
                        reflection,
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
                    # 达到 Reflection 最大次数，直接结束
                    break
    else:
        for step in range(max_depth):
            # 检查中断
            if check_interrupt():
                print("[Execution interrupted by user]")
                return {
                    "final_answer": "执行已被用户中断。",
                    "reflections": reflections,
                    "interrupted": True,
                }

            action = await actor.act()
            render_step(step + 1, action)

            # 记录工具调用
            if action.get("type") == "tool":
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
                reflection_count += 1  # 增加 Reflection 计数
                if logger:
                    logger.log_reflection(step + 1, reflection)

                # 始终显示 Reflection
                print(format_block(f"Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})", reflection))

                # Reflection Agent 拥有最终决策权
                if "可以给出最终回答" in reflection:
                    consecutive_rejections = 0
                    break
                elif "需要继续调用工具" in reflection:
                    consecutive_rejections += 1

                    # 死循环检测
                    if consecutive_rejections >= 10:
                        print("⚠ Reflection 连续 10 次拒绝，可能陷入死循环")
                        print(f"最后一次工具调用在 step {last_tool_call_step + 1}")
                        print(f"当前 step {step + 1}")
                        print("Actor 可能没有响应 Reflection 的要求\n")

                        return {
                            "final_answer": f"执行失败：Reflection 连续 {consecutive_rejections} 次拒绝。\n\n最后的 Reflection：\n{reflection}",
                            "reflections": reflections,
                            "deadlock": True,
                        }

                    feedback = f"[Reflection] {reflection}\n\n⚠ 警告：你必须调用工具来验证。不要编造结果。请立即调用必要的工具。"
                    memory_manager.append({
                        "role": "user",
                        "content": feedback,
                    })
                    continue
                elif action.get("type") == "answer":
                    consecutive_rejections += 1
                    memory_manager.append({
                        "role": "user",
                        "content": f"[Reflection] {reflection}\n\n你的回答需要更多验证，请继续执行必要的工具调用。",
                    })
                    continue
            elif action.get("type") == "answer":
                if reflection_count < max_reflection_steps:
                    reflection = reflector.reflect(
                        question=question,
                        history=memory_manager.get_context(),
                        memory_markdown=memory_manager.get_memory_markdown(),
                        soul_markdown=memory_manager.get_soul_markdown(),
                    )
                    reflections.append(reflection)
                    reflection_count += 1  # 增加 Reflection 计数
                    if logger:
                        logger.log_reflection(step + 1, reflection)

                    # 始终显示 Reflection
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
                    # 达到 Reflection 最大次数，直接结束
                    break

    final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)
    used_fallback_answer = False
    try:
        final_answer = actor.query(final_prompt)
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
    }


async def main():
    global logger
    logger = ConversationLogger()

    memory_manager = MemoryManager()
    runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))

    try:
        await runtime.initialize()
        tools = await runtime.list_tools()
        tool_names = list_tool_names(tools)

        actor = ActorAgent(runtime, memory_manager)
        reflector = ReflectionAgent()
        print_startup_banner(memory_manager, runtime.status_label, tool_names)

        if RICH_AVAILABLE and console:
            console.print(f"[dim]Logging to: {logger.get_log_path()}[/dim]\n")
        else:
            print(f"Logging to: {logger.get_log_path()}\n")

        while True:
            question = read_user_message(actor, memory_manager)
            if should_exit(question):
                break

            result = await run_agent(actor, reflector, question, memory_manager)

            # 检查是否被中断或死锁
            if result.get("interrupted"):
                if RICH_AVAILABLE and console:
                    console.print(Panel(result["final_answer"], title="[bold yellow]Interrupted[/bold yellow]", border_style="yellow"))
                else:
                    print(format_block("interrupted", result["final_answer"]))
                continue

            if result.get("deadlock"):
                if RICH_AVAILABLE and console:
                    console.print(Panel(result["final_answer"], title="[bold red]Deadlock Detected[/bold red]", border_style="red"))
                else:
                    print(format_block("deadlock", result["final_answer"]))
                continue

            if RICH_AVAILABLE and console:
                console.print(Panel(result["final_answer"], title="[bold green]Assistant[/bold green]", border_style="green"))
            else:
                print(format_block("assistant", result["final_answer"]))

            show_memory = config.get("ui.show_memory_events", False)
            if show_memory:
                memory_events = list(result.get("review_events") or []) + list(result.get("housekeeping_events") or [])
                if memory_events:
                    if RICH_AVAILABLE and console:
                        console.print("[dim]" + " | ".join(memory_events) + "[/dim]")
                    else:
                        print(format_block("memory", "\n".join(memory_events)))
    finally:
        await runtime.close()


asyncio.run(main())
