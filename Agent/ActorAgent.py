from Agent.LargeLanguageModel import LargeLanguageModel
import asyncio
import atexit
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from Core.Message import CoreMessage, MessageSource
from Core.ToolCall import ToolCall
from Core.LifecycleLog import lifecycle
from Core.timing import timeit
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer
from Tools.guard import ToolCallGuard

import re

from configurationLoader import config

logger = logging.getLogger(__name__)

# PromptRenderer 会为每条历史消息注入 XML 标签（如 &lt;user_message id="msg_xxx"&gt;），
# LLM 可能模仿这个格式在输出开头写入同样的 ID 标记。这里用正则剥离这些前缀。
_ID_PREFIX_PATTERN = re.compile(r'^(\[ID:msg_[a-f0-9]+\]\s*)+')


def _strip_id_prefixes(text):
    """移除 LLM 输出中从 Prompt 上下文模仿的 [ID:xxx] 前缀。"""
    return _ID_PREFIX_PATTERN.sub('', text)

# ── 文本格式工具调用打捞 ──────────────────────────────────────────
# 部分模型（如 MiniMax）偶发不走原生 tool_use，而是把工具调用写成
# 文本标记。检测并转换为合成 tool_use 块，让正常执行链路接管。
_TEXT_TOOL_CALL_RE = re.compile(r"\[TOOL_CALL\]\s*(?P<body>.*?)\s*\[/TOOL_CALL\]", re.DOTALL)
_TEXT_TOOL_NAME_RE = re.compile(r'tool\s*=>\s*"(?P<name>[^"]+)"')
_TEXT_ARGS_RE = re.compile(r"args\s*=>\s*\{(?P<args>.*)", re.DOTALL)
_ARG_PAIR_RE = re.compile(r"--(?P<key>[A-Za-z_][\w-]*)\s+(?P<value>[^\n]+)")
_MINIMAX_TC_RE = re.compile(r"<minimax:tool_call>\s*(?P<body>.*?)\s*</minimax:tool_call>", re.DOTALL)


def _coerce_arg_value(raw):
    v = raw.strip().rstrip(",").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    return v


def _salvage_text_tool_calls(text_content):
    """从文本内容中打捞文本格式的工具调用。

    Returns:
        (tool_use_blocks, remaining_text_content) 或 None（无打捞）。
    """
    full = "\n".join(text_content)
    if "[TOOL_CALL]" not in full and "<minimax:tool_call>" not in full:
        return None

    blocks = []
    # 格式 A: [TOOL_CALL] {tool => "X", args => { --k v }} [/TOOL_CALL]
    for m in _TEXT_TOOL_CALL_RE.finditer(full):
        body = m.group("body")
        name_m = _TEXT_TOOL_NAME_RE.search(body)
        if not name_m:
            continue
        args = {}
        args_m = _TEXT_ARGS_RE.search(body)
        if args_m:
            inner = args_m.group("args").rstrip("}\n ")
            for pair in _ARG_PAIR_RE.finditer(inner):
                args[pair.group("key")] = _coerce_arg_value(pair.group("value"))
        blocks.append({
            "type": "tool_use",
            "id": f"salvaged_{uuid.uuid4().hex[:12]}",
            "name": name_m.group("name"),
            "input": args,
        })

    # 格式 B: <minimax:tool_call>JSON</minimax:tool_call>
    for m in _MINIMAX_TC_RE.finditer(full):
        body = m.group("body")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        name = data.get("name") or data.get("tool")
        args = data.get("arguments") or data.get("args") or {}
        if name and isinstance(args, dict):
            blocks.append({
                "type": "tool_use",
                "id": f"salvaged_{uuid.uuid4().hex[:12]}",
                "name": name,
                "input": args,
            })

    if not blocks:
        return None

    remaining = _TEXT_TOOL_CALL_RE.sub("", full)
    remaining = _MINIMAX_TC_RE.sub("", remaining)
    remaining = remaining.replace("</minimax:tool_call>", "").strip()

    logger.warning(
        "Salvaged %d text-format tool call(s) (model bypassed native tool_use): %s",
        len(blocks), ", ".join(b["name"] for b in blocks),
    )
    return blocks, ([remaining] if remaining else [])

# 专用 executor，不受事件循环生命周期影响
# Python 3.9+ 的 ThreadPoolExecutor 会在 __init__ 注册 atexit handler，
# 导致进程关闭时 executor 被自动 shutdown。这里主动 unregister 该 handler，
# 只在手动调用 shutdown_llm_executor() 时才关闭。
_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-query-")
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
        # 记忆检索缓存：只在 turn 开始搜一次，后续 step 复用
        self._retrieval_cache = None
        self._retrieval_done = False
        # 每轮检索结果的记账表：用户消息 id → 记忆上下文块。
        # 渲染时按 id 永久贴回原消息，保证历史前缀跨 turn 逐字节稳定
        # （prompt cache 命中的前提；只贴最新消息会使旧前缀失效）。
        self._memory_contexts = {}

    def reset_retrieval_cache(self):
        """清空记忆检索缓存（Gateway 在每轮 turn 开始时调用，act 是每步级的不能在这重置）。"""
        self._retrieval_done = False
        self._retrieval_cache = None

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
    def _build_assistant_content(thinking_content, text_content, tool_use_blocks):
        """按 Anthropic Messages API 格式重建 assistant 消息内容。

        严格透明：模型输出的 thinking / text / tool_use 块原样回传
        （仅剥离 [ID:msg_xxx] 前缀），thinking 不再被丢弃。
        """
        content = []
        for t in thinking_content:
            t = _strip_id_prefixes(str(t).strip())
            if t:
                content.append({"type": "thinking", "thinking": t})
        for t in text_content:
            t = _strip_id_prefixes(str(t).strip())
            if t:
                content.append({"type": "text", "text": t})
        for tb in tool_use_blocks:
            content.append(tb)
        return content

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

    @staticmethod
    def _truncate_history(history, max_messages):
        """保留最近 N 条消息，防止上下文随会话无限膨胀。

        截断必须保持 tool_use/tool_result 配对完整：若截断点落在
        assistant tool_use 与其 user tool_result 之间，首条消息会是
        孤立的 tool_result（配对的 tool_use 已被截掉），Anthropic API
        会直接报 400——因此持续丢弃首部的孤立 tool_result 消息。
        """
        if max_messages <= 0 or len(history) <= max_messages:
            return history
        trimmed = history[-max_messages:]
        dropped_pairs = 0
        while trimmed:
            first = trimmed[0]
            content = first.get("content") if isinstance(first, dict) else None
            has_tool_result = isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
            if not has_tool_result:
                break
            trimmed = trimmed[1:]
            dropped_pairs += 1
        logger.info(
            "Context truncation: %d → %d messages (dropped %d orphan tool_result)",
            len(history), len(trimmed), dropped_pairs,
        )
        return trimmed

    @staticmethod
    def _sanitize_messages_for_api(messages):
        """清理不符合 Anthropic API 规则的消息序列。

        API 要求：assistant 的 tool_use 必须被后续 user 的 tool_result 配对；
        任何孤立的 tool_use 或 tool_result 都会触发 400/2013。
        这里只修复“配对完整性”，不修改消息语义。
        """
        if not messages:
            return messages

        n = len(messages)
        keep = [True] * n

        # pending_assistants: assistant 消息索引 -> 未匹配 tool_use_id 集合
        pending_assistants = {}

        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content") or []
            blocks = content if isinstance(content, list) else []

            if role == "assistant":
                tool_use_ids = {
                    block.get("id")
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
                }
                if tool_use_ids:
                    pending_assistants[i] = tool_use_ids
            elif role == "user":
                result_ids = {
                    block.get("tool_use_id")
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
                }
                if not result_ids:
                    continue

                # 优先匹配最近一个待处理的 assistant
                matched_idx = None
                for idx in sorted(pending_assistants.keys(), reverse=True):
                    if result_ids.issubset(pending_assistants[idx]):
                        matched_idx = idx
                        break

                if matched_idx is not None:
                    pending_assistants[matched_idx] -= result_ids
                    if not pending_assistants[matched_idx]:
                        del pending_assistants[matched_idx]
                else:
                    # 孤立的 tool_result：只移除 tool_result 块
                    non_result_blocks = [
                        block for block in blocks
                        if not (isinstance(block, dict) and block.get("type") == "tool_result")
                    ]
                    if non_result_blocks:
                        messages[i]["content"] = non_result_blocks
                    else:
                        keep[i] = False

        # 处理仍未完全匹配的 assistant：删除未匹配的 tool_use 块
        for idx, unmatched_ids in pending_assistants.items():
            content = messages[idx].get("content") or []
            blocks = content if isinstance(content, list) else []
            new_blocks = [
                block for block in blocks
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") in unmatched_ids
                )
            ]
            if new_blocks:
                messages[idx]["content"] = new_blocks
            else:
                keep[idx] = False

        cleaned = [msg for i, msg in enumerate(messages) if keep[i]]

        # 防御性处理：如果清理后最前面是尚未配对的 assistant tool_use，直接删除
        while cleaned:
            first = cleaned[0]
            if first.get("role") != "assistant":
                break
            content = first.get("content") or []
            blocks = content if isinstance(content, list) else []
            has_tool_use = any(
                isinstance(b, dict) and b.get("type") == "tool_use"
                for b in blocks
            )
            if not has_tool_use:
                break
            cleaned.pop(0)

        if len(cleaned) != len(messages):
            logger.info(
                "History sanitized for API: %d → %d messages",
                len(messages), len(cleaned),
            )
        return cleaned

    def build_messages_with_document(self, extra_system_prompt=None):
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

        # 初始化防护器（首次调用时）；之后每次同步工具表，MCP 热增删的工具即时生效
        if tool_definitions:
            if self.guard is None:
                self.guard = ToolCallGuard(tool_definitions)
            elif len(self.guard.available_tools) != len(tool_definitions):
                self.guard.update_tools(tool_definitions)

        import configurationLoader
        retrieval_enabled = bool(configurationLoader.config.get("memory.retrieval.enabled", False))

        retrieval_pack = self._retrieval_cache if self._retrieval_done else None
        if not self._retrieval_done:
            if retrieval_enabled and hasattr(self.working, "retrieve_prompt_memory"):
                try:
                    with timeit("actor.build.retrieve"):
                        retrieval_pack = self.working.retrieve_prompt_memory()
                    lifecycle("记忆检索", "完成", level=2)
                except Exception:
                    retrieval_pack = None
            # 缓存本轮检索结果，后续 step 直接复用，不重复检索
            self._retrieval_cache = retrieval_pack
            self._retrieval_done = True

        history = self._truncate_history(
            self.working.get_context(),
            int(configurationLoader.config.get("agent.max_context_messages", 0) or 0),
        )
        history = self._sanitize_messages_for_api(history)

        with timeit("actor.build.assemble"):
            document = self.prompt_assembler.build_actor_document(
                history=history,
                extra_system_prompt=extra_system_prompt,
                tool_definitions=tool_definitions,
                retrieval_pack=retrieval_pack,
                memory_contexts=self._memory_contexts,
            )
        with timeit("actor.build.render"):
            messages = self.prompt_renderer.render_document(document)
        lifecycle("提示词组装", f"{len(messages)} 条消息", level=2)
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

    def _should_use_native_tools(self):
        """判断当前 provider 是否支持原生工具调用且工具定义已加载"""
        tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

        return (
            self.core.provider_supports_native_tools
            and tool_definitions is not None
            and bool(tool_definitions)
        )

    @staticmethod
    async def _run_llm_with_interrupt(
        query_fn, interrupt_check, poll_interval=0.3
    ):
        """在后台线程运行阻塞的 LLM 查询，只定期检查中断标志。

        看门狗只负责用户中断（ESC），不再强制 LLM 硬超时——LLM 超时由
        provider 每请求客户端超时 + LLMCore 内层重试循环负责。
        executor 已被 shutdown（进程关闭）时返回 None 让调用方当作中断处理。
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
                future.cancel()
                return None

            try:
                return await asyncio.wait_for(
                    asyncio.shield(future), timeout=poll_interval
                )
            except asyncio.TimeoutError:
                pass

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

    async def act(self, max_retries=3, on_tool_call_start=None, on_thinking=None, on_tool_result=None, on_text=None, on_retry=None, _interrupt_check=None, extra_system_prompt=None, on_built=None):
        """
        执行一次推理，支持工具调用重试和并发执行多个工具

        Args:
            max_retries: 工具调用失败时的最大重试次数（防线5）
            on_tool_call_start: 工具调用开始时的回调函数，接收(tool_name, args)
            on_thinking: 收到 thinking 块时立即回调，接收(thinking_text)，在工具执行前触发
            on_tool_result: 单个工具执行完成时立即回调，接收(tool_name, result_text)，不等其他工具
            on_text: 收到 text 块时立即回调，接收(text_content)，在工具执行前触发
            _interrupt_check: 可选中断检查回调，返回 True 表示用户请求了中断
            extra_system_prompt: 额外的系统提示词（如 Snip 提醒），注入到本步 prompt 中
            on_built: 消息组装完成后的回调，接收 build_result dict。Gateway 用它
                      基于同一次构建发 token 统计，避免每步重复组装一次。
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
        if on_built is not None:
            try:
                on_built(build_result)
            except Exception as exc:
                logger.warning("on_built callback failed: %s", exc)
        logger.info("act: messages built (%d msgs), entering LLM call", len(messages))

        if not self._should_use_native_tools():
            raise ValueError(
                "ActorAgent requires a tool-capable provider and loaded tool definitions"
            )

        anthropic_tools = self._convert_tools_to_anthropic_format(tool_definitions)
        logger.debug(
            "act: native tools=%d messages=%d interrupt=%s",
            len(tool_definitions) if tool_definitions else 0,
            len(messages),
            bool(_interrupt_check),
        )
        return await self._act_with_native_tools(
            max_retries, on_tool_call_start, messages, anthropic_tools,
            on_thinking=on_thinking, on_tool_result=on_tool_result,
            on_text=on_text, on_retry=on_retry,
            _interrupt_check=_interrupt_check
        )

    async def _act_with_native_tools(self, max_retries=3, on_tool_call_start=None, messages=None, _reuse_tools=None, on_thinking=None, on_tool_result=None, on_text=None, on_retry=None, _auto_continue_count=0, _interrupt_check=None):
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
        logger.info("act: dispatching LLM request (messages=%d)", len(messages))
        with timeit("actor.llm.query"):
            try:
                response = await self._run_llm_with_interrupt(
                    lambda: self.query(messages, on_retry=on_retry, **gen_kwargs),
                    _interrupt_check
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 重试耗尽或不可重试错误：优雅报错，不向上抛、不触发 Gateway 崩溃重启
                logger.error("LLM query failed after retries: %s", e)
                return {
                    "type": "error",
                    "error": f"LLM 调用失败（已重试 10 次）: {type(e).__name__}: {e}",
                }
        if response is None:
            return {"type": "interrupted"}

        # 提取usage信息
        usage = self.core._extract_usage(response)

        # 解析响应
        if not hasattr(response, "content"):
            raise ValueError("Native tool mode requires Anthropic response objects with content blocks")

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

        # ── 打捞文本格式的工具调用（模型未走原生 tool_use 的降级路径）──
        if not tool_use_blocks:
            salvaged = _salvage_text_tool_calls(text_content)
            if salvaged:
                tool_use_blocks, text_content = salvaged

        # 立即通过回调发出 thinking（在工具执行之前）
        if thinking_content and on_thinking:
            thinking_text = _strip_id_prefixes("\n".join(thinking_content).strip())
            if thinking_text:
                on_thinking(thinking_text)

        # 立即通过回调发出 text（在工具执行之前）
        # 仅在伴随工具调用时发出：纯文本响应会作为 answer 返回，
        # 若此时也发 text_content，同一段文本会在前端渲染两次。
        if text_content and on_text and tool_use_blocks:
            text_str = _strip_id_prefixes("\n".join(text_content).strip())
            if text_str:
                on_text(text_str)

        # ── max_tokens 截断自动续写 / 工具调用截断恢复 ──────────────────────
        MAX_AUTO_CONTINUE = 2
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens" and _auto_continue_count < MAX_AUTO_CONTINUE:
            if tool_use_blocks:
                # 原生 tool_use 被截断：视为参数非法，不执行，直接返回错误让模型重试
                logger.warning(
                    "Native tool_use truncated by max_tokens; treating as invalid tool calls"
                )
                error_msgs = []
                tool_results = []
                for block in tool_use_blocks:
                    tool_name = block.get("name", "?")
                    error_msg = (
                        f"InputValidationError: tool call '{tool_name}' was truncated by the output "
                        f"token limit and its arguments are incomplete/invalid. "
                        f"last tool use has been wrongly stopped by output token limit."
                    )
                    error_msgs.append(error_msg)
                    tool_results.append(
                        ToolCall.create_anthropic_tool_result(
                            block.get("id"),
                            error_msg,
                            is_error=True,
                        )
                    )

                self.working.append({
                    "role": "assistant",
                    "content": [block for block in response.content],
                })
                self.working.append({
                    "role": "user",
                    "content": tool_results + [{
                        "type": "text",
                        "text": (
                            f"部分工具调用失败: {'; '.join(error_msgs)}\n"
                            "请重新生成正确的工具调用。"
                        ),
                    }],
                })

                if max_retries > 0:
                    return await self._act_with_native_tools(
                        max_retries - 1, on_tool_call_start,
                        _reuse_tools=_reuse_tools,
                        on_thinking=on_thinking, on_tool_result=on_tool_result,
                        on_text=on_text, on_retry=on_retry, _interrupt_check=_interrupt_check
                    )
                return {
                    "type": "error",
                    "error": "; ".join(error_msgs),
                }

            # 非工具调用文本截断：记录截断输出并让模型继续
            truncated_content = self._build_assistant_content(thinking_content, text_content, [])
            if truncated_content:
                self.working.append({"role": "assistant", "content": truncated_content})

            # 判断输出里是否包含未打捞成功的伪工具调用标记
            # （模型把工具调用写成文本但被 max_tokens 截断，导致 JSON/XML 不完整）
            full_text = "\n".join(text_content)
            has_truncated_tool_call = (
                "[TOOL_CALL]" in full_text or "<minimax:tool_call>" in full_text
            ) and _salvage_text_tool_calls(text_content) is None

            if has_truncated_tool_call:
                # 工具调用被截断：继续续写只会重复/补全无效 JSON，
                # 应让模型拆分成更小粒度重新生成。
                logger.warning(
                    "Tool call truncated by max_tokens; instructing model to chunk content"
                )
                self.working.append({
                    "role": "system",
                    "content": (
                        "Your previous output contained a tool call that was truncated by the "
                        "output token limit and cannot be executed. Do not continue the truncated "
                        "content. Instead, split the task into smaller pieces: first Write a "
                        "minimal runnable skeleton, then fill it in incrementally with multiple "
                        "Edit calls, or split large files into smaller modules."
                    ),
                })
            else:
                # 普通文本截断：让模型继续输出
                self.working.append({
                    "role": "system",
                    "content": (
                        "Output token limit hit. Resume directly — no apology, no recap of what you "
                        "were doing. Pick up mid-thought if that is where the cut happened. Break "
                        "remaining work into smaller pieces."
                    ),
                })

            new_messages = self.build_messages()
            return await self._act_with_native_tools(
                max_retries, on_tool_call_start, new_messages,
                _reuse_tools=_reuse_tools,
                on_thinking=on_thinking, on_tool_result=on_tool_result,
                on_text=on_text, on_retry=on_retry,
                _auto_continue_count=_auto_continue_count + 1,
                _interrupt_check=_interrupt_check
            )

        # 仅包含 thinking 块 → 模型还在思考，继续循环
        if thinking_content and not text_content and not tool_use_blocks:
            thinking_text = _strip_id_prefixes("\n".join(thinking_content).strip())
            if thinking_text:
                self.working.append({
                    "role": "assistant",
                    "content": self._build_assistant_content(thinking_content, [], []),
                })
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
                # verify 硬闸门：有足量未验证的文件修改时拦截最终答案，
                # 强制先调 verify 子智能体（防绕过验收）
                from Tools.verify_nudge import verify_nudge
                gate_msg = verify_nudge.gate_final_answer()
                if gate_msg:
                    self.working.append({
                        "role": "assistant",
                        "content": self._build_assistant_content(thinking_content, text_content, []),
                    })
                    self.working.append({"role": "user", "content": gate_msg})
                    return await self._act_with_native_tools(
                        max_retries, on_tool_call_start, self.build_messages(),
                        _reuse_tools=anthropic_tools,
                        on_thinking=on_thinking, on_tool_result=on_tool_result,
                        on_text=on_text, on_retry=on_retry, _interrupt_check=_interrupt_check
                    )
                self.working.append({
                    "role": "assistant",
                    "content": self._build_assistant_content(thinking_content, text_content, []),
                })
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
                return await self._act_with_native_tools(max_retries - 1, on_tool_call_start, _reuse_tools=anthropic_tools, on_thinking=on_thinking, on_tool_result=on_tool_result, on_text=on_text, on_retry=on_retry, _interrupt_check=_interrupt_check)
            # 重试耗尽：优雅报错，绝不上抛——冲出这里会触发 Gateway 崩溃重启
            logger.error("Empty LLM response after exhausting %d retries", max_retries)
            return {
                "type": "error",
                "error": "模型连续返回空响应（多次重试后仍为空）。请检查模型配置或稍后重试。",
            }

        # 并发执行所有工具调用

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
            lifecycle("工具调用", f"{tool_name}({self._summarize_arguments(args)})", level=2)
            if on_tool_call_start:
                on_tool_call_start(tool_name, args, call_id, guard_result)

            # 执行工具调用
            try:
                with timeit(f"tool.execute.{tool_name}"):
                    result = await self.tool_runtime.call_tool(tool_name, args)
            except asyncio.CancelledError:
                return {
                    "error": "AbortError: User aborted",
                    "tool_use_id": tool_use_block.get("id"),
                }
            result_text = self._stringify_tool_result(result)
            lifecycle("工具结果", f"{tool_name}: {self._preview_text(result_text)}", level=2)

            # 记录到工具调用历史板（供 Verify 审计主 Agent 本轮做了什么）
            try:
                from Tools.tool_call_board import record_main_call
                record_main_call(
                    tool_name,
                    self._summarize_arguments(args),
                    self._preview_text(result_text),
                    is_error=ToolCall.classify_is_error(tool_name, result_text),
                    backgrounded=self._is_backgrounded(result_text),
                )
            except Exception:
                pass

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
            # 将原始响应的 tool_use 块存入上下文
            self.working.append({
                "role": "assistant",
                "content": [block for block in response.content],
            })

            # 为所有工具调用添加 tool_result（成功和失败都要配对，避免 400/2013 错误）
            # 错误结果统一用 InputValidationError: 前缀兜底，方便模型识别
            tool_results = []
            for r in results:
                if "error" in r:
                    error_text = r["error"]
                    if not str(error_text).startswith("InputValidationError:"):
                        error_text = f"InputValidationError: {error_text}"
                    tool_results.append(
                        ToolCall.create_anthropic_tool_result(
                            r["tool_use_id"],
                            error_text,
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

            error_msgs = [r["error"] for r in errors]
            self.working.append({
                "role": "user",
                "content": tool_results + [{
                    "type": "text",
                    "text": f"部分工具调用失败: {'; '.join(error_msgs)}\n请重新生成正确的工具调用。",
                }],
            })

            return await self._act_with_native_tools(max_retries - 1, on_tool_call_start, _reuse_tools=anthropic_tools, on_thinking=on_thinking, on_tool_result=on_tool_result, on_text=on_text, on_retry=on_retry, _interrupt_check=_interrupt_check)

        # 记录所有成功的工具调用到 working memory
        # Separate backgrounded results from synchronous results
        user_content = []
        backgrounded_results = []
        synchronous_results = []
        kept_tool_uses = []  # 非 error 结果的 tool_use 块，用于重建 assistant 消息

        # 分类处理 tool_use 块（tool_result 配对 + 后台/同步区分）
        for i, result in enumerate(results):
            if "error" in result:
                continue
            is_bg = self._is_backgrounded(result["result_text"])
            if is_bg:
                backgrounded_results.append(result)
                kept_tool_uses.append(tool_use_blocks[i])
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
                self.working.append(CoreMessage.context_only(
                    MessageSource.BG_TASK,
                    (
                        f"[System: task-background] Tool '{result['tool_name']}' "
                        f"has moved to background (task #{task_id}). "
                        f"You can continue other work — results will be "
                        f"injected when ready."
                    ),
                ).to_dict())
            else:
                synchronous_results.append(result)
                kept_tool_uses.append(tool_use_blocks[i])
                is_error = ToolCall.classify_is_error(result["tool_name"], result["result_text"])
                user_content.append(
                    ToolCall.create_anthropic_tool_result(
                        result["tool_use_id"],
                        result["result_text"],
                        is_error=is_error,
                    )
                )

        # 记录到 working memory：严格透明回传 thinking + text + tool_use（Anthropic 格式）
        assistant_content = self._build_assistant_content(
            thinking_content, text_content, kept_tool_uses
        )
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
