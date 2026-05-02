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
    from Memory.FileStateManager import FileStateManager
    from configurationLoader import config

    if logger:
        logger.log_user_input(question)

    turn_start_index = memory_manager.get_context_size()
    memory_manager.append({"role": "user", "content": question})

    max_depth = int(config.get("mcp.maxDepth", 8) or 8)
    max_review_cycles = int(config.get("mcp.max_review_cycles", 3) or 3)

    # 文件状态管理器
    file_state = FileStateManager()

    # 审核循环计数
    review_cycle = 0
    final_answer = None
    review_history = []

    # 全局中断标志（从 main 模块导入会有循环依赖，所以直接访问）
    import main as main_module

    # 导入 render_step 函数
    from main import render_step

    if console:
        console.print(f"[dim]答案审核模式：最多 {max_review_cycles} 次审核循环[/dim]\n")

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

        # Actor 执行一步
        action = await actor.act()

        # 显示 Actor 的输出
        from main import render_step
        render_step(step + 1, action)

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

        # 如果 Actor 输出了答案，触发审核
        elif action_type == "answer":
            answer = action.get("answer", "")

            if console:
                console.print("\n" + "=" * 60)
                console.print("[bold cyan]Actor 认为可以回答，启动 Reflection 审核...[/bold cyan]")
                console.print("=" * 60 + "\n")

            # 获取 FileStateManager 的上下文
            file_state_context = file_state.get_reflection_context()

            # Reflection 审核答案
            review_result = reflector.review_answer(
                question=question,
                answer=answer,
                file_state_context=file_state_context,
                memory_markdown=memory_manager.get_memory_markdown(),
                soul_markdown=memory_manager.get_soul_markdown(),
            )

            if logger:
                logger.log_reflection(step + 1, review_result)

            # 显示审核结果
            if console:
                from rich.panel import Panel
                console.print(Panel(
                    review_result,
                    title=f"[bold magenta]Reflection 审核 (第 {review_cycle + 1}/{max_review_cycles} 次)[/bold magenta]",
                    border_style="magenta",
                    padding=(1, 2)
                ))

            review_history.append({
                "cycle": review_cycle + 1,
                "answer": answer,
                "review": review_result,
            })

            # 判断审核结果
            if "答案可以接受" in review_result:
                # 审核通过，使用这个答案
                if console:
                    console.print("[bold green]✓ 审核通过，答案可以展示[/bold green]\n")

                final_answer = answer
                break

            elif "答案需要改进" in review_result:
                # 审核不通过
                review_cycle += 1

                if review_cycle >= max_review_cycles:
                    # 达到最大审核次数，强制使用当前答案
                    if console:
                        console.print(f"[bold yellow]⚠ 已达到最大审核次数 ({max_review_cycles})，强制使用当前答案[/bold yellow]\n")

                    final_answer = answer
                    break
                else:
                    # 反馈给 Actor，让其改进
                    if console:
                        console.print(f"[yellow]✗ 审核未通过，反馈给 Actor 改进（剩余 {max_review_cycles - review_cycle} 次机会）[/yellow]\n")

                    feedback = f"""[Reflection 审核反馈]

{review_result}

请根据以上反馈改进你的答案。你可以：
1. 补充更多信息（如果需要，可以继续调用工具）
2. 修正错误或不准确的部分
3. 使答案更加清晰和完整

请给出改进后的答案。"""

                    memory_manager.append({
                        "role": "user",
                        "content": feedback,
                    })

                    # 继续循环，让 Actor 重新回答
                    continue
            else:
                # 审核结果不明确，视为通过
                if console:
                    console.print("[yellow]⚠ 审核结果不明确，默认接受答案[/yellow]\n")

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

        FINAL_ANSWER_PROMPT = "请基于以上所有信息，给出最终、完整、准确的回答。此阶段禁止调用工具，不要输出 JSON，只能直接输出给用户的自然语言答复。"
        final_prompt = actor.build_messages(FINAL_ANSWER_PROMPT)

        try:
            final_answer = actor.query(final_prompt)
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

    return {
        "final_answer": final_answer,
        "review_history": review_history,
        "review_cycles": review_cycle,
        "file_state_stats": file_state.get_stats(),
        "review_events": review_events,
        "housekeeping_events": housekeeping_events,
    }
