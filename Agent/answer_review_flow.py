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
