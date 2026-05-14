"""
新的 Agent 执行流程实现

核心变更：
1. Reflection 仅在 Actor 输出 answer 时触发
2. FileStateManager 记录所有文件读取和工具调用
3. Reflection 审核 Actor 的答案，最多 N 次循环
4. 只有审核通过的答案才展示给用户
"""

async def run_agent_with_answer_review(
    actor,
    reflector,
    question,
    memory_manager,
    logger=None,
    console=None,
):
    """
    新的 Agent 执行流程：答案审核模式

    流程：
    1. Actor 调用工具收集信息
    2. Actor 认为可以回答时输出 answer
    3. Reflection 审核答案
    4. 如果审核通过，展示答案
    5. 如果审核不通过，反馈给 Actor 重新回答（最多 N 次）
    """
    from Agent.ContextCompactor import ContextCompactor
    from Memory.FileStateManager import FileStateManager
    from configurationLoader import config

    if logger:
        logger.log_user_input(question)

    turn_start_index = memory_manager.get_context_size()
    memory_manager.append({"role": "user", "content": question})

    max_depth = int(config.get("agent.maxDepth", 8) or 8)
    max_review_cycles = int(config.get("agent.max_review_cycles", 3) or 3)

    # 上下文窗口管理配置
    max_context_messages = int(config.get("agent.max_context_messages", 20) or 20)

    # 文件状态管理器
    file_state = FileStateManager()

    # Token统计
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0

    # Token显示函数
    def display_token_stats(label=""):
        """显示当前token统计"""
        if console:
            from Agent.TokenEstimator import TokenEstimator
            total_tokens = total_input_tokens + total_output_tokens
            cache_rate = (total_cache_read_tokens / total_input_tokens * 100) if total_input_tokens > 0 else 0
            console.print(
                f"[dim]{label}Token: 输入 {TokenEstimator.format(total_input_tokens)} "
                f"| 输出 {TokenEstimator.format(total_output_tokens)} "
                f"| 缓存 {TokenEstimator.format(total_cache_read_tokens)} ({cache_rate:.1f}%) "
                f"| 总计 {TokenEstimator.format(total_tokens)}[/dim]"
            )

    # 审核循环计数
    review_cycle = 0
    final_answer = None
    review_history = []

    # 全局中断标志
    from Agent import agent_loop as main_module

    # 导入 render_step_result 函数
    from Agent.agent_loop import render_step_result

    # 用于显示最新thinking的变量
    latest_thinking = {"content": None}

    # 重复工具调用检测
    tool_call_history = []  # 记录最近的工具调用
    MAX_SAME_TOOL_CALLS = 3  # 最多允许连续调用同一工具3次

    def check_repeated_tool_call(tool_name, args):
        """检查是否重复调用同一工具"""
        # 生成工具调用的签名（工具名+参数）
        import json
        try:
            args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except:
            args_str = str(args)

        call_signature = f"{tool_name}:{args_str}"

        # 添加到历史记录
        tool_call_history.append(call_signature)

        # 只保留最近10次调用
        if len(tool_call_history) > 10:
            tool_call_history.pop(0)

        # 检查最近3次是否都是同一个调用
        if len(tool_call_history) >= MAX_SAME_TOOL_CALLS:
            recent_calls = tool_call_history[-MAX_SAME_TOOL_CALLS:]
            if len(set(recent_calls)) == 1:  # 所有调用都相同
                return True

        return False

    # 工具调用开始的回调函数
    def on_tool_call_start(tool_name, args, raw_reply, guard_result):
        """工具调用开始时立即显示"""
        # 检查重复调用
        is_repeated = check_repeated_tool_call(tool_name, args)

        if console:
            from rich.markup import escape
            from Agent.ActorAgent import ActorAgent

            # 立即显示工具调用
            arguments_summary = ActorAgent._summarize_arguments(args)
            console.print(f"\n[bold yellow]→ Tool Call[/bold yellow] [cyan]{escape(tool_name)}[/cyan] {escape(arguments_summary)}")

            # 如果是重复调用，显示警告
            if is_repeated:
                console.print(f"[bold red]⚠️ 警告：你已经连续{MAX_SAME_TOOL_CALLS}次调用相同的工具和参数！[/bold red]")
                # 添加系统提示
                memory_manager.append({
                    "role": "user",
                    "content": f"⚠️ 系统警告：你重复调用了{MAX_SAME_TOOL_CALLS}次同一个工具 '{tool_name}' 且参数相同。如果没有特殊情况，不要这样使用。请检查是否陷入循环，或者尝试其他方法。"
                })

            # 显示执行中的状态
            console.print("[dim]⏳ Executing...[/dim]")
        else:
            from Agent.ActorAgent import ActorAgent
            print(f"\n[step {step + 1}] Tool: {tool_name}")
            arguments_summary = ActorAgent._summarize_arguments(args)
            print(f"Args: {arguments_summary}")

            if is_repeated:
                print(f"⚠️ 警告：你已经连续{MAX_SAME_TOOL_CALLS}次调用相同的工具和参数！")
                memory_manager.append({
                    "role": "user",
                    "content": f"⚠️ 系统警告：你重复调用了{MAX_SAME_TOOL_CALLS}次同一个工具 '{tool_name}' 且参数相同。如果没有特殊情况，不要这样使用。请检查是否陷入循环，或者尝试其他方法。"
                })

            print("⏳ Executing...")

    if console:
        console.print(f"[dim]答案审核模式：最多 {max_review_cycles} 次审核循环[/dim]\n")
        # 显示初始token统计（用户提问后）
        display_token_stats("📊 初始 ")

    # ── 上下文自动压缩 ──────────────────────────────
    compact_enabled = bool(config.get("agent.context_compact.enabled", False))
    compact_threshold = int(config.get("agent.context_compact.threshold_tokens", 80000))
    _compactor = None
    _compacted_this_turn = False

    def maybe_compact_context():
        nonlocal _compactor, _compacted_this_turn
        if not compact_enabled or _compacted_this_turn:
            return
        messages = memory_manager.get_context()
        if _compactor is None:
            _compactor = ContextCompactor()
        estimated = _compactor.estimate_tokens(messages)
        if estimated < compact_threshold:
            return

        if logger:
            logger.log_memory_event(
                f"Context auto-compact triggered (review flow, est {estimated} tokens)"
            )
        if console:
            console.print(f"[yellow]⚡ Context ({estimated} tokens) exceeds threshold, compacting...[/yellow]")

        try:
            summary = _compactor.compact(messages)
            if not summary or len(summary) < 20:
                raise ValueError("empty summary")
        except Exception as e:
            if logger:
                logger.log_error(f"Compaction failed in review flow: {e}")
            if console:
                console.print(f"[red]Compaction failed: {e}, using hard truncation[/red]")
            max_msgs = int(config.get("agent.max_context_messages", 20) or 20)
            recent = messages[-max_msgs:]
            memory_manager.clear_context()
            for msg in recent:
                memory_manager.append(msg)
            _compacted_this_turn = True
            return

        memory_manager.clear_context()
        memory_manager.append({
            "role": "user",
            "content": f"[上下文自动压缩]\n\n{summary}\n\n---\n请根据以上摘要继续执行任务。",
        })
        _compacted_this_turn = True

    for step in range(max_depth):
        # 检查中断
        if main_module.check_interrupt():
            if console:
                console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
            return {
                "final_answer": "执行已被用户中断。",
                "review_history": review_history,
                "interrupted": True,
            }

        # Actor 执行一步（传入回调函数）
        maybe_compact_context()
        action = await actor.act(on_tool_call_start=on_tool_call_start)

        # 收集token统计（如果有）
        if action.get("usage"):
            usage = action["usage"]
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)
            total_cache_read_tokens += usage.get("cache_read_input_tokens", 0)

            # LLM回复后立即显示token统计
            display_token_stats(f"📊 Step {step + 1} ")

        # 显示thinking内容（单独的框，只显示最新的）
        thinking = action.get("thinking")

        # 调试：检查thinking是否存在
        print(f"[answer_review_flow] 🔍 检查 thinking: action_type={action.get('type')}, has_thinking={thinking is not None}, thinking_length={len(thinking) if thinking else 0}")
        if config.get("ui.verbose", False) and console:
            console.print(f"[dim]Debug: action_type={action.get('type')}, has_thinking={thinking is not None}, thinking_length={len(thinking) if thinking else 0}[/dim]")

        if thinking and config.get("ui.show_thinking", True):
            latest_thinking["content"] = thinking
            if console:
                from rich.markup import escape
                from rich.panel import Panel
                console.print(Panel(
                    escape(thinking),
                    title="[bold cyan]💭 Latest Thinking[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2)
                ))
            else:
                print(f"[Thinking]")
                print(thinking)
                print()
        elif config.get("ui.show_thinking", True) and not thinking:
            # 如果配置要显示thinking但没有thinking内容，显示提示
            if console and config.get("ui.verbose", False):
                console.print("[dim]⚠️ No thinking content in this step[/dim]")

        # 显示 Actor 的输出（只显示结果，不显示thinking）
        render_step_result(step + 1, action)

        action_type = action.get("type")

        # 记录工具调用到 FileStateManager
        if action_type == "tool":
            tool_name = action.get("tool_name")
            arguments = action.get("arguments", {})
            result_preview = action.get("result_preview", "")

            # 从 memory 中获取完整的工具结果
            context = memory_manager.get_context()
            if context and len(context) > 0:
                last_message = context[-1]
                if last_message.get("role") == "tool":
                    result = last_message.get("content", "")
                    file_state.record_tool_call(tool_name, arguments, result, result_preview)

            # 答案引导机制：在特定工具调用后，引导 Actor 输出答案
            # 如果调用了子Agent工具，并且已经执行了多步，提示生成答案
            if tool_name == "AgentDelegate" and step >= 2:
                guidance_message = "工具调用已完成。请基于以上工具返回的结果，生成一个完整的、用户友好的答案。不要再调用工具，直接输出自然语言答复。"
                memory_manager.append({"role": "user", "content": guidance_message})

                if console:
                    console.print(f"[dim]→ 引导 Actor 生成答案[/dim]\n")

        # 上下文窗口管理：保持最近的N条消息
        context = memory_manager.get_context()
        if len(context) > max_context_messages:
            # 保留系统消息和最近的对话
            system_messages = [m for m in context if m.get("role") == "system"]
            recent_messages = [m for m in context if m.get("role") != "system"][-max_context_messages:]

            # 清空并重建上下文
            memory_manager.clear_context()
            for msg in system_messages + recent_messages:
                memory_manager.append(msg)

            if console and config.get("ui.verbose", False):
                console.print(f"[dim]→ 上下文窗口管理：保留最近 {len(recent_messages)} 条消息[/dim]\n")

        # 处理批量工具调用
        elif action_type == "tool_batch":
            tool_calls = action.get("tool_calls", [])

            # 记录所有工具调用
            for tc in tool_calls:
                tool_name = tc.get("tool_name")
                arguments = tc.get("arguments", {})
                result_preview = tc.get("result_preview", "")
                file_state.record_tool_call(tool_name, arguments, "", result_preview)

            # 批量工具调用后也添加引导
            if step >= 2:
                guidance_message = "所有工具调用已完成。请基于以上工具返回的结果，生成一个完整的、用户友好的答案。不要再调用工具，直接输出自然语言答复。"
                memory_manager.append({"role": "user", "content": guidance_message})

                if console:
                    console.print(f"[dim]→ 引导 Actor 生成答案[/dim]\n")

        # 如果 Actor 输出中间推理（仅 thinking 块，无 text/tool_use），继续循环
        elif action_type == "continue":
            if console:
                preview = action.get("content_preview", "")
                if preview:
                    console.print(f"[dim]→ 中间推理: {preview}[/dim]")
            continue

        # 如果 Actor 输出了答案，直接使用（跳过 Reflection 审核）
        elif action_type == "answer":
            answer = action.get("answer", "")

            if console:
                console.print("\n[dim]Actor 输出答案，跳过 Reflection 审核[/dim]\n")

            # 直接接受答案，不经过 Reflection 审核
            final_answer = answer
            break

        # 如果是错误，记录并继续
        elif action_type == "error":
            if console:
                console.print(f"[red]✗ 错误: {action.get('error')}[/red]")
            continue

    # 如果循环结束还没有答案，生成最终答案
    if final_answer is None:
        if console:
            console.print("\n[yellow]⚠ 未获得明确答案，生成最终回答...[/yellow]\n")
            # API上传前显示token统计
            display_token_stats("📊 上传前 ")

        FINAL_ANSWER_PROMPT = "请基于以上所有信息，给出最终、完整、准确的回答。此阶段禁止调用工具，不要输出 JSON，只能直接输出给用户的自然语言答复。"
        final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)

        try:
            response = actor.query(final_prompt)
            final_answer = response if isinstance(response, str) else str(response)

            # 收集最终答案的token统计
            if hasattr(response, "usage"):
                total_input_tokens += getattr(response.usage, "input_tokens", 0)
                total_output_tokens += getattr(response.usage, "output_tokens", 0)
                total_cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0)

            # 最终答案生成后显示token统计
            if console:
                display_token_stats("📊 最终 ")
        except Exception as e:
            if logger:
                logger.log_error(str(e))
            final_answer = "抱歉，生成最终答案时出现错误。"

    # 记录最终答案
    memory_manager.append({"role": "assistant", "content": final_answer})

    if logger:
        logger.log_assistant_response(final_answer)

    # 后续处理
    turn_transcript = memory_manager.get_turn_messages_since(turn_start_index)
    review_events = memory_manager.post_turn_review(turn_transcript)
    housekeeping_events = memory_manager.detectOverflow()

    if logger:
        for event in list(review_events or []) + list(housekeeping_events or []):
            logger.log_memory_event(event)

    # 返回前显示最终token统计
    if console:
        display_token_stats("📊 总计 ")

    return {
        "final_answer": final_answer,
        "review_history": review_history,
        "review_cycles": review_cycle,
        "file_state_stats": file_state.get_stats(),
        "review_events": review_events,
        "housekeeping_events": housekeeping_events,
        "token_usage": {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cache_read_tokens": total_cache_read_tokens,
            "total_tokens": total_input_tokens + total_output_tokens
        }
    }
