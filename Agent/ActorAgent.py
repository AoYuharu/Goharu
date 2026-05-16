from Agent.LargeLanguageModel import LargeLanguageModel
import asyncio
import atexit
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from Core.ToolCall import ToolCall
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer
from Tools.guard import ToolCallGuard
from Tools.task_guide import TaskGuide
import re

from configurationLoader import config

logger = logging.getLogger(__name__)

# PromptRenderer 会为每条历史消息注入 [ID:msg_xxxxxxxx] 前缀（供 snip 工具使用），
# LLM 可能模仿这个格式在输出开头写入同样的 ID 标记。这里用正则剥离这些前缀。
_ID_PREFIX_PATTERN = re.compile(r'^(\[ID:msg_[a-f0-9]+\]\s*)+', re.MULTILINE)


def _strip_id_prefixes(text):
    """移除 LLM 输出中从 Prompt 上下文模仿的 [ID:xxx] 前缀。"""
    return _ID_PREFIX_PATTERN.sub('', text)

# 专用 executor，不受事件循环生命周期影响
# Python 3.9+ 的 ThreadPoolExecutor 会在 __init__ 注册 atexit handler，
# 导致进程关闭时 executor 被自动 shutdown。这里主动 unregister 该 handler，
# 只在手动调用 shutdown_llm_executor() 时才关闭。
_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-query-")
_LLM_EXECUTOR_LOCK = threading.Lock()
_LLM_EXECUTOR_SHUTDOWN = False

# Unregister the atexit handler that ThreadPoolExecutor registers in __init__
# (Python 3.9+). Without this, the executor gets shut down while daemon threads
# are still trying to submit LLM queries during process exit.
try:
    atexit.unregister(_LLM_EXECUTOR._python_exit)
except (AttributeError, Exception):
    pass  # _python_exit may not exist in all Python versions


def shutdown_llm_executor(wait=True):
    """手动关闭 LLM executor（在 main() finally 中调用）。"""
    global _LLM_EXECUTOR, _LLM_EXECUTOR_SHUTDOWN
    with _LLM_EXECUTOR_LOCK:
        _LLM_EXECUTOR_SHUTDOWN = True
        _LLM_EXECUTOR.shutdown(wait=wait, cancel_futures=True)
    logger.info("LLM executor shut down")


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
    def _apply_tool_result_budget(results):
        """Apply token budget to tool results. Saves oversized results to cache dir.

        Called after tool execution, before recording to working memory.
        """
        from Agent.ToolResultBudget import ToolResultBudget

        max_single = config.get("tools.result_budget.max_single_tokens", 8000)
        max_batch = config.get("tools.result_budget.max_batch_tokens", 24000)
        cache_dir = config.get(
            "tools.result_budget.cache_dir", "./runtime_memory/tool_cache"
        )
        return ToolResultBudget.apply(results, cache_dir, max_single, max_batch)

    @staticmethod
    def _is_backgrounded(result_text):
        """Check if a tool result indicates the task was moved to background."""
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict):
                return parsed.get("backgrounded", False)
            return False
        except (json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def _summarize_arguments(args, max_length=120):
        try:
            rendered = json.dumps(args, ensure_ascii=False)
        except TypeError:
            rendered = str(args)
        return ActorAgent._preview_text(rendered, max_length=max_length)

    def build_messages(self, extra_system_prompt=None):
        return self.build_messages_with_document(extra_system_prompt)["messages"]

    def build_messages_with_document(self, extra_system_prompt=None):
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

        # 初始化防护器（首次调用时）
        if self.guard is None and tool_definitions:
            self.guard = ToolCallGuard(tool_definitions)

        import configurationLoader
        retrieval_enabled = bool(configurationLoader.config.get("memory.retrieval.enabled", False))
        use_legacy_memory = bool(configurationLoader.config.get("memory.prompt.use_legacy_memory_markdown", True))

        retrieval_pack = None
        if retrieval_enabled and hasattr(self.working, "retrieve_prompt_memory"):
            try:
                retrieval_pack = self.working.retrieve_prompt_memory()
            except Exception:
                retrieval_pack = None

        document = self.prompt_assembler.build_actor_document(
            history=self.working.get_context(),
            soul_markdown=self.working.get_soul_markdown(),
            user_profile_markdown=self.working.get_user_profile_markdown(),
            memory_markdown=self.working.get_memory_markdown(),
            extra_system_prompt=extra_system_prompt,
            tool_definitions=tool_definitions,
            retrieval_pack=retrieval_pack,
            use_legacy_memory=use_legacy_memory,
        )
        messages = self.prompt_renderer.render_document(document)
        return {
            "document": document,
            "messages": messages,
            "tool_definitions": tool_definitions,
        }

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

    @staticmethod
    def _extract_usage(response, fallback_usage=None):
        if fallback_usage is not None:
            return fallback_usage
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        }

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

    @staticmethod
    async def _run_llm_with_interrupt(query_fn, interrupt_check, poll_interval=0.3):
        """在后台线程运行阻塞的 LLM 查询，定期检查中断标志。

        使用专用的 _LLM_EXECUTOR。如果 executor 已被 shutdown（进程关闭），
        返回 None 让调用方当作中断处理，而非抛出异常导致 crash。
        """
        global _LLM_EXECUTOR, _LLM_EXECUTOR_SHUTDOWN

        if not interrupt_check:
            try:
                return query_fn()
            except RuntimeError as e:
                if "shutdown" in str(e).lower():
                    logger.warning("LLM query skipped (executor shut down)")
                    return None
                raise

        with _LLM_EXECUTOR_LOCK:
            if _LLM_EXECUTOR_SHUTDOWN:
                logger.warning("LLM query skipped (executor marked shut down)")
                return None
            try:
                future = asyncio.get_running_loop().run_in_executor(
                    _LLM_EXECUTOR, query_fn
                )
            except RuntimeError as e:
                if "shutdown" in str(e).lower():
                    logger.warning("LLM query skipped: executor shut down")
                    return None
                raise

        while True:
            if interrupt_check():
                return None  # 用户中断，放弃等待
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass  # 超时后再次检查中断标志

    @staticmethod
    async def _run_tasks_with_interrupt(tasks: list, interrupt_check):
        """执行一组 asyncio.Task，每隔 0.1s 检查中断标志。
        如果中断触发，取消所有未完成任务，返回 None。
        否则等待全部完成，返回有序结果列表。
        """
        remaining = list(tasks)
        completed = {}  # task → result
        try:
            while remaining:
                done, remaining = await asyncio.wait(
                    remaining, timeout=0.1, return_when=asyncio.FIRST_COMPLETED
                )
                if interrupt_check and interrupt_check():
                    for t in remaining:
                        t.cancel()
                    return None  # 中断，不返回结果
                for t in done:
                    try:
                        completed[id(t)] = t.result()
                    except asyncio.CancelledError:
                        completed[id(t)] = {"error": "AbortError: User aborted"}
                    except Exception as exc:
                        completed[id(t)] = {"error": f"Tool execution failed: {type(exc).__name__}: {exc}"}
            # 按原始顺序返回结果
            return [completed[id(t)] for t in tasks]
        except Exception:
            for t in remaining:
                t.cancel()
            raise

    async def act(self, max_retries=3, on_tool_call_start=None, on_thinking=None, on_tool_result=None, _interrupt_check=None, extra_system_prompt=None):
        """
        执行一次推理，支持工具调用重试和并发执行多个工具

        Args:
            max_retries: 工具调用失败时的最大重试次数（防线5）
            on_tool_call_start: 工具调用开始时的回调函数，接收(tool_name, args)
            on_thinking: 收到 thinking 块时立即回调，接收(thinking_text)，在工具执行前触发
            on_tool_result: 单个工具执行完成时立即回调，接收(tool_name, result_text)，不等其他工具
            _interrupt_check: 可选中断检查回调，返回 True 表示用户请求了中断
            extra_system_prompt: 额外的系统提示词（如 Snip 提醒），注入到本步 prompt 中
        """
        # 确保工具定义已加载（防止首次调用来不及加载）
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)
        if not tool_definitions:
            try:
                await self.tool_runtime.list_tools()
                tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)
            except Exception:
                pass

        # 先构建消息（这会触发工具定义的加载）
        build_result = self.build_messages_with_document(extra_system_prompt)
        messages = build_result["messages"]
        tool_definitions = build_result["tool_definitions"]

        # 判断是否使用 Anthropic 原生工具调用
        use_native = self._should_use_native_tools()

        logger.debug(
            "act: use_native=%s tools=%d messages=%d interrupt=%s",
            use_native,
            len(tool_definitions) if tool_definitions else 0,
            len(messages),
            bool(_interrupt_check),
        )

        if use_native:
            anthropic_tools = self._convert_tools_to_anthropic_format(tool_definitions)
            return await self._act_with_native_tools(
                max_retries, on_tool_call_start, messages, anthropic_tools,
                on_thinking=on_thinking, on_tool_result=on_tool_result,
                _interrupt_check=_interrupt_check
            )
        else:
            return await self._act_with_text_tools(
                max_retries, on_tool_call_start, messages,
                on_thinking=on_thinking, on_tool_result=on_tool_result,
                _interrupt_check=_interrupt_check
            )

    async def _act_with_native_tools(self, max_retries=3, on_tool_call_start=None, messages=None, _reuse_tools=None, on_thinking=None, on_tool_result=None, _auto_continue_count=0, _interrupt_check=None):
        """
        使用 Anthropic 原生工具调用格式

        支持：
        - 一次响应多个 tool_use 块
        - tool_choice 参数引导
        - 结构化 JSON 验证
        - 失败重试机制
        - on_thinking / on_tool_result 回调实现实时 UI 更新
        - max_tokens 截断自动续写（最多 2 次）
        """
        if messages is None:
            messages = self.build_messages()

        # 优先使用调用方传入的工具定义（避免 TOCTOU 重复读取 last_tool_definitions）
        if _reuse_tools is not None:
            anthropic_tools = _reuse_tools
        else:
            tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)
            anthropic_tools = self._convert_tools_to_anthropic_format(tool_definitions)

        # 构建生成参数
        gen_kwargs = {"tools": anthropic_tools}

        # 混合策略：首次尝试使用 tool_choice 引导（如果只有一个工具）
        if max_retries == 3 and len(anthropic_tools) == 1:
            gen_kwargs["tool_choice"] = {"type": "tool", "name": anthropic_tools[0]["name"]}

        # ── 中断检查：调用 LLM 之前 ──
        if _interrupt_check and _interrupt_check():
            return {"type": "interrupted"}

        # 调用 LLM（可被 ESC 中断：查询在后台线程运行，主循环轮询中断标志）
        response = await self._run_llm_with_interrupt(
            lambda: self.query(messages, **gen_kwargs),
            _interrupt_check
        )
        if response is None:
            return {"type": "interrupted"}

        # 提取usage信息
        usage = self._extract_usage(response)

        # 解析响应
        if not hasattr(response, "content"):
            # 降级到文本模式
            return await self._act_with_text_tools(max_retries, on_tool_call_start, _interrupt_check=_interrupt_check)

        # 提取所有 tool_use、text、thinking 块
        tool_use_blocks = []
        text_content = []
        thinking_content = []

        for block in response.content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            block_type = block_dict.get("type")
            if block_type == "tool_use":
                tool_use_blocks.append(block_dict)
            elif block_type == "text":
                text_content.append(block_dict.get("text", ""))
            elif block_type == "thinking":
                thinking_content.append(block_dict.get("thinking", ""))

        # 立即通过回调发出 thinking（在工具执行之前）
        if thinking_content and on_thinking:
            thinking_text = _strip_id_prefixes("\n".join(thinking_content).strip())
            if thinking_text:
                on_thinking(thinking_text)

        # ── max_tokens 截断自动续写 ──────────────────────
        MAX_AUTO_CONTINUE = 2
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens" and not tool_use_blocks and _auto_continue_count < MAX_AUTO_CONTINUE:
            # 记录截断的输出（剥离 LLM 可能模仿的 [ID:xxx] 前缀）
            if thinking_content:
                thinking_text = _strip_id_prefixes("\n".join(thinking_content).strip())
                self.working.append({"role": "assistant", "content": thinking_text})
            if text_content:
                partial = _strip_id_prefixes("\n".join(text_content).strip())
                self.working.append({"role": "assistant", "content": partial})
            # 注入续写指令
            self.working.append({
                "role": "user",
                "content": "Continue exactly where you stopped. Do not repeat previous content."
            })
            new_messages = self.build_messages()
            return await self._act_with_native_tools(
                max_retries, on_tool_call_start, new_messages,
                _reuse_tools=_reuse_tools,
                on_thinking=on_thinking, on_tool_result=on_tool_result,
                _auto_continue_count=_auto_continue_count + 1,
                _interrupt_check=_interrupt_check
            )

        # 仅包含 thinking 块 → 模型还在思考，继续循环
        if thinking_content and not text_content and not tool_use_blocks:
            thinking_text = _strip_id_prefixes("\n".join(thinking_content).strip())
            if thinking_text:
                self.working.append({"role": "assistant", "content": thinking_text})
                return {
                    "type": "continue",
                    "content": thinking_text,
                    "content_preview": self._preview_text(thinking_text),
                    "raw_reply": str(response),
                    "usage": usage
                }

        # 如果没有工具调用，返回文本答案（剥离 LLM 可能模仿的 [ID:xxx] 前缀）
        if not tool_use_blocks:
            answer = _strip_id_prefixes("\n".join(text_content).strip())
            if answer:
                self.working.append({"role": "assistant", "content": answer})
                result = {
                    "type": "answer",
                    "answer": answer,
                    "answer_preview": self._preview_text(answer),
                    "raw_reply": answer,
                    "usage": usage
                }
                if thinking_content:
                    result["thinking"] = "\n".join(thinking_content).strip()
                return result
            # 空响应，重试
            if max_retries > 0:
                return await self._act_with_native_tools(max_retries - 1, on_tool_call_start, _reuse_tools=anthropic_tools, on_thinking=on_thinking, on_tool_result=on_tool_result, _interrupt_check=_interrupt_check)
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
            call_id = tool_use_block.get("id", "")
            if on_tool_call_start:
                on_tool_call_start(tool_name, args, call_id, guard_result)

            # 执行工具调用
            try:
                result = await self.tool_runtime.call_tool(tool_name, args)
            except asyncio.CancelledError:
                return {
                    "error": "AbortError: User aborted",
                    "tool_use_id": tool_use_block.get("id"),
                }
            result_text = self._stringify_tool_result(result)

            # 立即通过回调发出单个工具结果（不等其他工具）
            if on_tool_result:
                on_tool_result(tool_name, call_id, self._preview_text(result_text))

            return {
                "tool_use_id": tool_use_block.get("id"),
                "tool_name": tool_name,
                "arguments": args,
                "result": result,
                "result_text": result_text,
                "guard_result": guard_result,
            }

        # ── 中断检查：LLM 已返回工具调用，但用户要求停止 ──
        if _interrupt_check and _interrupt_check():
            return {"type": "interrupted"}

        # 并发执行所有工具调用（可中断）
        tool_tasks = [asyncio.create_task(execute_single_tool(block)) for block in tool_use_blocks]
        results = await self._run_tasks_with_interrupt(tool_tasks, _interrupt_check)
        if results is None:
            return {"type": "interrupted"}

        # 对工具结果施加 token 预算（大批量结果缓存到文件）
        self._apply_tool_result_budget(results)

        # 检查是否有错误
        errors = [r for r in results if "error" in r]
        if errors and max_retries > 0:
            # 构建错误反馈消息
            error_msgs = [r["error"] for r in errors]

            # 将原始响应的 tool_use 块存入上下文
            self.working.append({
                "role": "assistant",
                "content": [block for block in response.content],
            })

            # 为所有工具调用添加 tool_result（成功和失败都要配对，避免 400/2013 错误）
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
                else:
                    is_error = ToolCall.classify_is_error(r.get("tool_name", ""), r["result_text"])
                    tool_results.append(
                        ToolCall.create_anthropic_tool_result(
                            r["tool_use_id"],
                            r["result_text"],
                            is_error=is_error,
                        )
                    )

            self.working.append({
                "role": "user",
                "content": tool_results + [{
                    "type": "text",
                    "text": f"部分工具调用失败: {'; '.join(error_msgs)}\n请重新生成正确的工具调用。",
                }],
            })

            return await self._act_with_native_tools(max_retries - 1, on_tool_call_start, _reuse_tools=anthropic_tools, on_thinking=on_thinking, on_tool_result=on_tool_result, _interrupt_check=_interrupt_check)

        # 记录所有成功的工具调用到 working memory
        # Separate backgrounded results from synchronous results
        assistant_content = []
        user_content = []
        backgrounded_results = []
        synchronous_results = []

        # 1. 如果有 text 内容，先添加 text 块（保留 LLM 的说明文字）
        if text_content:
            text_str = "\n".join(text_content).strip()
            if text_str:
                assistant_content.append({"type": "text", "text": text_str})

        # 2. 分类处理 tool_use 块
        for i, result in enumerate(results):
            if "error" in result:
                continue
            is_bg = self._is_backgrounded(result["result_text"])
            if is_bg:
                backgrounded_results.append(result)
                assistant_content.append(tool_use_blocks[i])
                # Extract background task info
                try:
                    bg_info = json.loads(result["result_text"])
                    task_id = bg_info.get("task_id", "?")
                except (json.JSONDecodeError, TypeError):
                    task_id = "?"
                # MUST include a tool_result to pair with tool_use (Anthropic API requirement)
                # Without this, the next LLM call triggers error 2013:
                #   "tool call result does not follow tool call"
                user_content.append(
                    ToolCall.create_anthropic_tool_result(
                        result["tool_use_id"],
                        json.dumps({
                            "backgrounded": True,
                            "task_id": task_id,
                            "message": f"Tool '{result['tool_name']}' moved to background (task #{task_id}). Results will be injected when ready."
                        }, ensure_ascii=False),
                        is_error=False,
                    )
                )
                # Also inject system-level note for the LLM's attention
                self.working.append({
                    "role": "user",
                    "content": (
                        f"[System: task-background] Tool '{result['tool_name']}' "
                        f"has moved to background (task #{task_id}). "
                        f"You can continue other work — results will be "
                        f"injected when ready."
                    ),
                })
            else:
                synchronous_results.append(result)
                assistant_content.append(tool_use_blocks[i])
                is_error = ToolCall.classify_is_error(result["tool_name"], result["result_text"])
                user_content.append(
                    ToolCall.create_anthropic_tool_result(
                        result["tool_use_id"],
                        result["result_text"],
                        is_error=is_error,
                    )
                )

        # 记录到 working memory (all results include tool_result for API pairing)
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

        # Determine response type
        all_sync = [r for r in results if "error" not in r and not self._is_backgrounded(r["result_text"])]

        if backgrounded_results and not all_sync:
            response_type = "tool_backgrounded"
        elif all_sync:
            response_type = "tool_batch" if len(all_sync) > 1 else "tool"
        else:
            # All results are backgrounded
            response_type = "tool_backgrounded"

        # 返回结果
        result = {
            "type": response_type,
            "tool_calls": [
                {
                    "tool_name": r["tool_name"],
                    "arguments": r["arguments"],
                    "arguments_summary": self._summarize_arguments(r["arguments"]),
                    "result_preview": self._preview_text(r["result_text"]),
                    "guard_logs": r["guard_result"].get("logs") if r.get("guard_result") else None,
                    "backgrounded": self._is_backgrounded(r["result_text"]),
                }
                for r in results if "error" not in r
            ],
            "has_backgrounded": len(backgrounded_results) > 0,
            "raw_reply": str(response),
            "usage": usage,
        }
        if thinking_content:
            thinking_text = "\n".join(thinking_content).strip()
            result["thinking"] = thinking_text
        return result

    async def _act_with_text_tools(self, max_retries=3, on_tool_call_start=None, messages=None, on_thinking=None, on_tool_result=None, _interrupt_check=None):
        """
        使用文本 JSON 格式的工具调用（向后兼容）

        这是原有的实现逻辑
        """
        if messages is None:
            messages = self.build_messages()

        # ── 中断检查：调用 LLM 之前 ──
        if _interrupt_check and _interrupt_check():
            return {"type": "interrupted"}

        # 调用 LLM（可被 ESC 中断：查询在后台线程运行，主循环轮询中断标志）
        reply = await self._run_llm_with_interrupt(
            lambda: self.query(messages),
            _interrupt_check
        )
        if reply is None:
            return {"type": "interrupted"}
        thinking = self.get_last_thinking()
        usage = self.get_last_usage()

        # 立即通过回调发出 thinking
        thinking = _strip_id_prefixes(thinking) if thinking else ""
        if thinking and on_thinking:
            on_thinking(thinking)

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
                text_call_id = f"txt_{id(tool_call_obj)}"
                if on_tool_call_start:
                    on_tool_call_start(tool_name, args, text_call_id, guard_result)

                # 执行工具调用
                try:
                    result = await self.tool_runtime.call_tool(tool_name, args)
                except asyncio.CancelledError:
                    return {
                        "tool_call": tool_call_obj,
                        "error": "AbortError: User aborted",
                        "guard_result": guard_result
                    }
                result_text = self._stringify_tool_result(result)

                # 立即通过回调发出单个工具结果
                if on_tool_result:
                    on_tool_result(tool_name, text_call_id, self._preview_text(result_text))

                return {
                    "tool_call": tool_call_obj,
                    "tool_name": tool_name,
                    "arguments": args,
                    "result": result,
                    "result_text": result_text,
                    "guard_result": guard_result
                }

            # ── 中断检查：LLM 已返回工具调用，但用户要求停止 ──
            if _interrupt_check and _interrupt_check():
                return {"type": "interrupted"}

            # 并发执行所有工具调用（可中断）
            tool_tasks = [asyncio.create_task(execute_single_tool(tc)) for tc in tool_calls]
            results = await self._run_tasks_with_interrupt(tool_tasks, _interrupt_check)
            if results is None:
                return {"type": "interrupted"}

            # 对工具结果施加 token 预算
            self._apply_tool_result_budget(results)

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
                return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start, on_thinking=on_thinking, on_tool_result=on_tool_result, _interrupt_check=_interrupt_check, extra_system_prompt=extra_system_prompt)

            # 记录所有成功的工具调用到 working memory
            backgrounded_results = []
            for result in results:
                if "error" not in result:
                    if self._is_backgrounded(result["result_text"]):
                        backgrounded_results.append(result)
                        self.working.append(result["tool_call"])
                        try:
                            bg_info = json.loads(result["result_text"])
                            task_id = bg_info.get("task_id", "?")
                        except (json.JSONDecodeError, TypeError):
                            task_id = "?"
                        self.working.append({
                            "role": "user",
                            "content": (
                                f"[System: task-background] Tool '{result['tool_name']}' "
                                f"has moved to background (task #{task_id}). "
                                f"You can continue other work — results will be "
                                f"injected when ready."
                            ),
                        })
                    else:
                        self.working.append(result["tool_call"])
                        content = result["result_text"]
                        if ToolCall.classify_is_error(result.get("tool_name", ""), content):
                            content = f"[ERROR] {content}"
                        self.working.append({
                            "role": "tool",
                            "name": result["tool_name"],
                            "content": content,
                        })

            # Determine response type
            all_sync = [r for r in results if "error" not in r and not self._is_backgrounded(r["result_text"])]
            if backgrounded_results and not all_sync:
                response_type = "tool_backgrounded"
            else:
                response_type = "tool_batch"

            # 返回多个工具调用的结果
            result = {
                "type": response_type,
                "tool_calls": [
                    {
                        "tool_name": r["tool_name"],
                        "arguments": r["arguments"],
                        "arguments_summary": self._summarize_arguments(r["arguments"]),
                        "result_preview": self._preview_text(r["result_text"]),
                        "guard_logs": r["guard_result"].get("logs") if r.get("guard_result") else None,
                        "backgrounded": self._is_backgrounded(r["result_text"]),
                    }
                    for r in results if "error" not in r
                ],
                "has_backgrounded": len(backgrounded_results) > 0,
                "raw_reply": reply,
                "usage": usage,
            }
            if thinking:
                result["thinking"] = thinking
            return result

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
                        return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start, on_thinking=on_thinking, on_tool_result=on_tool_result, _interrupt_check=_interrupt_check, extra_system_prompt=extra_system_prompt)
                    else:
                        # 重试次数耗尽，返回错误
                        error_response = f"工具调用失败（已重试 3 次）: {error_msg}"
                        self.working.append({"role": "assistant", "content": error_response})
                        return {
                            "type": "error",
                            "error": error_response,
                            "raw_reply": reply,
                            "usage": usage,
                        }

            # 使用修复后的工具名和参数
            tool_name = guard_result["tool_name"] if guard_result else raw_tool_name
            args = guard_result["arguments"] if guard_result else raw_args

            # 通知工具调用开始（在执行之前）
            seq_call_id = f"seq_{id(tool_call)}_{int(time.time() * 1000)}"
            if on_tool_call_start:
                on_tool_call_start(tool_name, args, seq_call_id, guard_result)

            # 执行工具调用（可中断）
            tool_task = asyncio.create_task(self.tool_runtime.call_tool(tool_name, args))
            results = await self._run_tasks_with_interrupt([tool_task], _interrupt_check)
            if results is None:
                return {"type": "interrupted"}
            result = results[0]
            result_text = self._stringify_tool_result(result)

            # 对工具结果施加 token 预算（原地修改 result_text）
            self._apply_tool_result_budget(results)
            result_text = self._stringify_tool_result(result)  # 刷新，可能已被替换

            # 检查工具执行结果是否为错误
            if ToolCall.classify_is_error(tool_name, result_text):
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
                    return await self.act(max_retries=max_retries - 1, on_tool_call_start=on_tool_call_start, on_thinking=on_thinking, on_tool_result=on_tool_result, _interrupt_check=_interrupt_check, extra_system_prompt=extra_system_prompt)

            # 成功执行 — 检查是否被后台化
            is_bg = self._is_backgrounded(result_text)

            # 记录到 working memory
            self.working.append(tool_call)
            if is_bg:
                try:
                    bg_info = json.loads(result_text)
                    task_id = bg_info.get("task_id", "?")
                except (json.JSONDecodeError, TypeError):
                    task_id = "?"
                self.working.append({
                    "role": "user",
                    "content": (
                        f"[System: task-background] Tool '{tool_name}' "
                        f"has moved to background (task #{task_id}). "
                        f"You can continue other work — results will be "
                        f"injected when ready."
                    ),
                })
            else:
                is_err = ToolCall.classify_is_error(tool_name, result_text)
                content = f"[ERROR] {result_text}" if is_err else result_text
                self.working.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": content,
                })

            # 任务引导：检测重复行为
            if self.task_guide:
                warning = self.task_guide.record_tool_call(tool_name, args)
                if warning:
                    self.working.append({
                        "role": "user",
                        "content": warning,
                    })

            result = {
                "type": "tool_backgrounded" if is_bg else "tool",
                "tool_name": tool_name,
                "arguments": args,
                "arguments_summary": self._summarize_arguments(args),
                "result_preview": self._preview_text(result_text),
                "raw_reply": reply,
                "usage": usage,
                "guard_logs": guard_result.get("logs") if guard_result else None,
                "has_backgrounded": is_bg,
            }
            if thinking:
                result["thinking"] = thinking
            return result

        reply = _strip_id_prefixes(reply)
        self.working.append({"role": "assistant", "content": reply})
        result = {
            "type": "answer",
            "answer": reply,
            "answer_preview": self._preview_text(reply),
            "raw_reply": reply,
            "usage": usage,
        }
        if thinking:
            result["thinking"] = thinking
        return result
