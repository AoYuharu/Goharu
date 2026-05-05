from Agent.LargeLanguageModel import LargeLanguageModel
import json

from Memory.ToolCall import ToolCall
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer
from Tools.guard import ToolCallGuard
from Tools.task_guide import TaskGuide
from configurationLoader import config


class ActorAgent(LargeLanguageModel):
    def __init__(self, tool_runtime, working):
        super().__init__()
        self.tool_runtime = tool_runtime
        self.working = working
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()
        self.guard = None  # 延迟初始化，等待工具列表加载
        self.task_guide = TaskGuide()  # 任务引导器

        # 检测是否使用 Anthropic 原生工具调用
        llm_config = config.get("model.large-language-model", {}) or {}
        self.provider = llm_config.get("provider", "local_hf")
        self.use_native_tools = llm_config.get("use_native_tools", True)  # 默认启用原生工具调用

    @staticmethod
    def is_tool_call(text: str) -> bool:
        return ToolCall.try_from_text(text) is not None

    @staticmethod
    def parse_tool_call(text: str):
        call = ToolCall.try_from_text(text)
        if call is None:
            raise ValueError("Invalid tool call payload")
        return call.to_payload()

    @staticmethod
    def _stringify_tool_result(result):
        content = getattr(result, "content", result)
        if isinstance(content, (dict, list)):
            try:
                return json.dumps(content, ensure_ascii=False)
            except TypeError:
                return str(content)
        return str(content)

    @staticmethod
    def _preview_text(text, max_length=160):
        compact = " ".join(str(text).split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3] + "..."

    @staticmethod
    def _summarize_arguments(args, max_length=120):
        try:
            rendered = json.dumps(args, ensure_ascii=False)
        except TypeError:
            rendered = str(args)
        return ActorAgent._preview_text(rendered, max_length=max_length)

    def build_messages(self, extra_system_prompt=None):
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

        # 初始化防护器（首次调用时）
        if self.guard is None and tool_definitions:
            self.guard = ToolCallGuard(tool_definitions)

        document = self.prompt_assembler.build_actor_document(
            history=self.working.get_context(),
            soul_markdown=self.working.get_soul_markdown(),
            user_profile_markdown=self.working.get_user_profile_markdown(),
            memory_markdown=self.working.get_memory_markdown(),
            extra_system_prompt=extra_system_prompt,
            tool_definitions=tool_definitions,
        )
        return self.prompt_renderer.render_document(document)

    def _convert_tools_to_anthropic_format(self, tool_definitions):
        """
        将工具定义转换为 Anthropic 原生格式

        Args:
            tool_definitions: MCP 工具定义列表

        Returns:
            List[dict]: Anthropic 格式的工具定义
        """
        if not tool_definitions:
            return []

        anthropic_tools = []
        for tool in tool_definitions:
            anthropic_tool = {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
            }

            # 转换 inputSchema 为 Anthropic 的 input_schema
            input_schema = tool.get("inputSchema", {})
            if input_schema:
                anthropic_tool["input_schema"] = input_schema

            anthropic_tools.append(anthropic_tool)

        return anthropic_tools

    def _should_use_native_tools(self):
        """判断是否应该使用 Anthropic 原生工具调用"""
        # 获取工具定义
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

        return (
            self.provider == "anthropic_compatible"
            and self.use_native_tools
            and tool_definitions is not None
            and bool(tool_definitions)
        )

    async def act(self, max_retries=3, on_tool_call_start=None):
        """
        执行一次推理，支持工具调用重试和并发执行多个工具

        Args:
            max_retries: 工具调用失败时的最大重试次数（防线5）
            on_tool_call_start: 工具调用开始时的回调函数，接收(tool_name, args)
        """
        # 先构建消息（这会触发工具定义的加载）
        messages = self.build_messages()

        # 判断是否使用 Anthropic 原生工具调用
        use_native = self._should_use_native_tools()

        if use_native:
            return await self._act_with_native_tools(max_retries, on_tool_call_start, messages)
        else:
            return await self._act_with_text_tools(max_retries, on_tool_call_start, messages)

    async def _act_with_native_tools(self, max_retries=3, on_tool_call_start=None, messages=None):
        """
        使用 Anthropic 原生工具调用格式

        支持：
        - 一次响应多个 tool_use 块
        - tool_choice 参数引导
        - 结构化 JSON 验证
        - 失败重试机制
        """
        if messages is None:
            messages = self.build_messages()

        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)
        anthropic_tools = self._convert_tools_to_anthropic_format(tool_definitions)

        # 构建生成参数
        gen_kwargs = {"tools": anthropic_tools}

        # 混合策略：首次尝试使用 tool_choice 引导（如果只有一个工具）
        if max_retries == 3 and len(anthropic_tools) == 1:
            gen_kwargs["tool_choice"] = {"type": "tool", "name": anthropic_tools[0]["name"]}

        # 调用 LLM
        response = self.query(messages, **gen_kwargs)

        # 解析响应
        if not hasattr(response, "content"):
            # 降级到文本模式
            return await self._act_with_text_tools(max_retries, on_tool_call_start)

        # 提取所有 tool_use 块
        tool_use_blocks = []
        text_content = []

        for block in response.content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            if block_dict.get("type") == "tool_use":
                tool_use_blocks.append(block_dict)
            elif block_dict.get("type") == "text":
                text_content.append(block_dict.get("text", ""))

        # 如果没有工具调用，返回文本答案
        if not tool_use_blocks:
            answer = "\n".join(text_content).strip()
            if answer:
                self.working.append({"role": "assistant", "content": answer})
                return {
                    "type": "answer",
                    "answer": answer,
                    "answer_preview": self._preview_text(answer),
                    "raw_reply": answer,
                }
            # 空响应，重试
            if max_retries > 0:
                return await self._act_with_native_tools(max_retries - 1, on_tool_call_start)
            raise ValueError("模型返回空响应")

        # 并发执行所有工具调用
        import asyncio

        async def execute_single_tool(tool_use_block):
            """执行单个工具调用"""
            tool_call = ToolCall.from_anthropic_tool_use(tool_use_block)
            if tool_call is None:
                return {
                    "error": f"无法解析工具调用: {tool_use_block}",
                    "tool_use_id": tool_use_block.get("id"),
                }

            raw_tool_name = tool_call.tool_name
            raw_args = tool_call.arguments

            # 防线 1-4：使用防护器修复工具调用
            guard_result = None
            if self.guard:
                guard_result = self.guard.guard(raw_tool_name, raw_args)

                if not guard_result["success"]:
                    error_msg = guard_result.get("error", "未知错误")
                    return {
                        "error": error_msg,
                        "tool_use_id": tool_use_block.get("id"),
                        "guard_result": guard_result,
                    }

            # 使用修复后的工具名和参数
            tool_name = guard_result["tool_name"] if guard_result else raw_tool_name
            args = guard_result["arguments"] if guard_result else raw_args

            # 通知工具调用开始
            if on_tool_call_start:
                on_tool_call_start(tool_name, args, str(tool_use_block), guard_result)

            # 执行工具调用
            result = await self.tool_runtime.call_tool(tool_name, args)
            result_text = self._stringify_tool_result(result)

            return {
                "tool_use_id": tool_use_block.get("id"),
                "tool_name": tool_name,
                "arguments": args,
                "result": result,
                "result_text": result_text,
                "guard_result": guard_result,
            }

        # 并发执行所有工具调用
        results = await asyncio.gather(*[execute_single_tool(block) for block in tool_use_blocks])

        # 检查是否有错误
        errors = [r for r in results if "error" in r]
        if errors and max_retries > 0:
            # 构建错误反馈消息
            error_msgs = [r["error"] for r in errors]

            # 将原始响应和错误反馈添加到上下文
            self.working.append({
                "role": "assistant",
                "content": [block for block in response.content],
            })

            # 为每个失败的工具调用添加 tool_result（错误）
            tool_results = []
            for r in results:
                if "error" in r:
                    tool_results.append(
                        ToolCall.create_anthropic_tool_result(
                            r["tool_use_id"],
                            r["error"],
                            is_error=True,
                        )
                    )

            self.working.append({
                "role": "user",
                "content": tool_results + [{
                    "type": "text",
                    "text": f"部分工具调用失败: {'; '.join(error_msgs)}\n请重新生成正确的工具调用。",
                }],
            })

            return await self._act_with_native_tools(max_retries - 1, on_tool_call_start)

        # 记录所有成功的工具调用到 working memory
        assistant_content = []
        user_content = []

        for i, result in enumerate(results):
            if "error" not in result:
                # 添加 tool_use 块
                assistant_content.append(tool_use_blocks[i])

                # 添加 tool_result 块
                user_content.append(
                    ToolCall.create_anthropic_tool_result(
                        result["tool_use_id"],
                        result["result_text"],
                        is_error=False,
                    )
                )

        # 记录到 working memory
        if assistant_content:
            self.working.append({
                "role": "assistant",
                "content": assistant_content,
            })

        if user_content:
            self.working.append({
                "role": "user",
                "content": user_content,
            })

        # 返回多个工具调用的结果
        return {
            "type": "tool_batch",
            "tool_calls": [
                {
                    "tool_name": r["tool_name"],
                    "arguments": r["arguments"],
                    "arguments_summary": self._summarize_arguments(r["arguments"]),
                    "result_preview": self._preview_text(r["result_text"]),
                    "guard_logs": r["guard_result"].get("logs") if r.get("guard_result") else None,
                }
                for r in results if "error" not in r
            ],
            "raw_reply": str(response),
        }

    async def _act_with_text_tools(self, max_retries=3, on_tool_call_start=None, messages=None):
        """
        使用文本 JSON 格式的工具调用（向后兼容）

        这是原有的实现逻辑
        """
        if messages is None:
            messages = self.build_messages()

        reply = self.query(messages)

        # 尝试解析多个工具调用
        tool_calls = ToolCall.try_all_from_text(reply)

        if tool_calls is not None and len(tool_calls) > 0:
            # 有多个工具调用 - 并发执行
            import asyncio

            async def execute_single_tool(tool_call_obj):
                """执行单个工具调用"""
                raw_tool_name = tool_call_obj.tool_name
                raw_args = tool_call_obj.arguments

                # 防线 1-4：使用防护器修复工具调用
                guard_result = None
                if self.guard:
                    guard_result = self.guard.guard(raw_tool_name, raw_args)

                    if not guard_result["success"]:
                        error_msg = guard_result.get("error", "未知错误")
                        return {
                            "tool_call": tool_call_obj,
                            "error": error_msg,
                            "guard_result": guard_result
                        }

                # 使用修复后的工具名和参数
                tool_name = guard_result["tool_name"] if guard_result else raw_tool_name
                args = guard_result["arguments"] if guard_result else raw_args

                # 通知工具调用开始（在执行之前）
                if on_tool_call_start:
                    on_tool_call_start(tool_name, args, reply, guard_result)

                # 执行工具调用
                result = await self.tool_runtime.call_tool(tool_name, args)
                result_text = self._stringify_tool_result(result)

                return {
                    "tool_call": tool_call_obj,
                    "tool_name": tool_name,
                    "arguments": args,
                    "result": result,
                    "result_text": result_text,
                    "guard_result": guard_result
                }

            # 并发执行所有工具调用
            results = await asyncio.gather(*[execute_single_tool(tc) for tc in tool_calls])

            # 检查是否有错误
            errors = [r for r in results if "error" in r]
            if errors and max_retries > 0:
                # 有错误，尝试重试
                error_msgs = [r["error"] for r in errors]
                self.working.append({
                    "role": "assistant",
                    "content": reply,
                })
                self.working.append({
                    "role": "user",
                    "content": f"部分工具调用失败: {'; '.join(error_msgs)}\n请重新生成正确的工具调用。",
                })
                return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start)

            # 记录所有成功的工具调用到 working memory
            for result in results:
                if "error" not in result:
                    self.working.append(result["tool_call"])
                    self.working.append({
                        "role": "tool",
                        "name": result["tool_name"],
                        "content": result["result_text"],
                    })

            # 返回多个工具调用的结果
            return {
                "type": "tool_batch",
                "tool_calls": [
                    {
                        "tool_name": r["tool_name"],
                        "arguments": r["arguments"],
                        "arguments_summary": self._summarize_arguments(r["arguments"]),
                        "result_preview": self._preview_text(r["result_text"]),
                        "guard_logs": r["guard_result"].get("logs") if r.get("guard_result") else None,
                    }
                    for r in results if "error" not in r
                ],
                "raw_reply": reply,
            }

        # 尝试解析单个工具调用（向后兼容）
        tool_call = ToolCall.try_from_text(reply)
        if tool_call is not None:
            raw_tool_name = tool_call.tool_name
            raw_args = tool_call.arguments

            # 防线 1-4：使用防护器修复工具调用
            guard_result = None
            if self.guard:
                guard_result = self.guard.guard(raw_tool_name, raw_args)

                if not guard_result["success"]:
                    # 防护失败，记录错误并尝试重试
                    error_msg = guard_result.get("error", "未知错误")

                    # 防线 5：重试机制
                    if max_retries > 0:
                        # 将错误反馈给模型，让它重新生成
                        self.working.append({
                            "role": "assistant",
                            "content": reply,
                        })
                        self.working.append({
                            "role": "user",
                            "content": f"工具调用失败: {error_msg}\n请重新生成正确的工具调用。",
                        })
                        return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start)
                    else:
                        # 重试次数耗尽，返回错误
                        error_response = f"工具调用失败（已重试 3 次）: {error_msg}"
                        self.working.append({"role": "assistant", "content": error_response})
                        return {
                            "type": "error",
                            "error": error_response,
                            "raw_reply": reply,
                        }

            # 使用修复后的工具名和参数
            tool_name = guard_result["tool_name"] if guard_result else raw_tool_name
            args = guard_result["arguments"] if guard_result else raw_args

            # 通知工具调用开始（在执行之前）
            if on_tool_call_start:
                on_tool_call_start(tool_name, args, reply, guard_result)

            # 执行工具调用
            result = await self.tool_runtime.call_tool(tool_name, args)
            result_text = self._stringify_tool_result(result)

            # 检查工具执行结果是否为错误
            if isinstance(result.content, str) and result.content.startswith('{"error":'):
                # 工具执行失败，尝试重试
                if max_retries > 0:
                    self.working.append(tool_call)
                    self.working.append({
                        "role": "tool",
                        "name": tool_name,
                        "content": result_text,
                    })
                    self.working.append({
                        "role": "user",
                        "content": f"工具执行失败: {result_text}\n请检查参数并重新调用。",
                    })
                    return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start)

            # 成功执行，记录到 working memory
            self.working.append(tool_call)
            self.working.append({
                "role": "tool",
                "name": tool_name,
                "content": result_text,
            })

            # 任务引导：检测重复行为
            if self.task_guide:
                warning = self.task_guide.record_tool_call(tool_name, args)
                if warning:
                    self.working.append({
                        "role": "user",
                        "content": warning,
                    })

            return {
                "type": "tool",
                "tool_name": tool_name,
                "arguments": args,
                "arguments_summary": self._summarize_arguments(args),
                "result_preview": self._preview_text(result_text),
                "raw_reply": reply,
                "guard_logs": guard_result.get("logs") if guard_result else None,
            }

        self.working.append({"role": "assistant", "content": reply})
        return {
            "type": "answer",
            "answer": reply,
            "answer_preview": self._preview_text(reply),
            "raw_reply": reply,
        }
