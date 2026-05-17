"""
SubAgent - 子agent执行模块

负责执行特定类型的子任务（Explore、Plan等），具有受限的工具权限和独立的执行上下文。
支持 Anthropic 原生 tool_use/tool_result 调用格式。
"""

import asyncio
import json
import re
import shlex
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

from Agent.ContextCompactor import ContextCompactor
from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.TokenEstimator import TokenEstimator
from Core.ToolCall import ToolCall
from configurationLoader import config


def _write_agent_jsonl(log_dir: Path, agent_id: str, entry: Dict[str, Any]):
    """向 JSONL 文件追加一条日志记录"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{agent_id}.jsonl"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


class SubAgentConfig:
    """子agent配置类"""

    _PROMPT_DIR = Path(__file__).parent.parent / "prompts" / "subagent"

    _PROMPT_FILES = {
        "explore": "explore.md",
        "plan": "plan.md",
        "verify": "verify.md",
    }

    _prompt_cache: Dict[str, str] = {}

    @classmethod
    def _load_prompt(cls, agent_type: str) -> str:
        """从 .md 文件加载子agent系统提示词（带缓存）"""
        agent_type = agent_type.lower()
        if agent_type in cls._prompt_cache:
            return cls._prompt_cache[agent_type]
        filename = cls._PROMPT_FILES.get(agent_type)
        if not filename:
            raise ValueError(f"Unknown agent type: {agent_type}")
        prompt_path = cls._PROMPT_DIR / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"SubAgent prompt file not found: {prompt_path}")
        content = prompt_path.read_text(encoding="utf-8").strip()
        cls._prompt_cache[agent_type] = content
        return content

    # 按 agent 类型区分的只读命令白名单
    BASE_READONLY_COMMANDS = {
        # Linux / macOS
        'ls', 'pwd', 'cd', 'less', 'more', 'wc', 'sort',
        # Windows (只读)
        'dir', 'where', 'set', 'ver', 'date', 'time',
        'findstr', 'tasklist', 'systeminfo', 'whoami', 'hostname',
        # Git
        'git status', 'git log', 'git diff', 'git show',
        'git branch', 'git remote', 'git stash list', 'git tag',
        'git rev-parse', 'git config --list', 'git blame', 'git whatchanged',
        'git shortlog', 'git describe', 'git ls-files', 'git ls-tree',
    }

    EXPLORE_PLAN_COMMANDS = {
        'find',
    }

    VERIFY_COMMANDS = {
        'python', 'python3', 'py',
        'pip check',
        'git diff', 'git log', 'git status', 'git show', 'git rev-parse',
    }

    COMMAND_ALLOWLISTS = {
        'explore': BASE_READONLY_COMMANDS | EXPLORE_PLAN_COMMANDS,
        'plan': BASE_READONLY_COMMANDS | EXPLORE_PLAN_COMMANDS,
        'verify': BASE_READONLY_COMMANDS | VERIFY_COMMANDS,
    }

    # 禁止的命令模式（包含重定向、文件写入、删除等）
    FORBIDDEN_PATTERNS = [
        '>', '>>', '<<',  # 重定向
        '2>', '1>', '&>',  # fd 重定向
        'rm ', 'del ', 'rmdir', 'rd ',  # 删除
        'mkdir', 'touch', 'edit ', 'rename', 'ren ',  # 创建/修改
        'mv ', 'move ', 'cp ', 'copy ',  # 移动/复制
        'chmod', 'chown', 'attrib', 'icacls', 'cacls', 'takeown',  # 权限
        'pip install', 'pip uninstall', 'pip download',  # pip 写操作
        'python -m pip install',  # pip 间接写操作
    ]

    @classmethod
    def get_system_prompt(cls, agent_type: str) -> str:
        """获取指定类型agent的系统提示词（从 prompts/subagent/*.md 加载）"""
        return cls._load_prompt(agent_type)

    @classmethod
    def get_allowed_tools(cls, agent_type: str) -> List[str]:
        """获取指定类型agent允许使用的工具"""
        if agent_type.lower() in ["explore", "plan", "verify"]:
            return ["Read", "Grep", "Glob", "run_cmd"]
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

    @classmethod
    def _extract_command_prefixes(cls, command: str) -> List[str]:
        command = command.lower().strip()
        if not command:
            return []

        prefixes = []
        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = command.split()

        if tokens:
            prefixes.append(tokens[0])
            if len(tokens) >= 2:
                prefixes.append(f"{tokens[0]} {tokens[1]}")
            if len(tokens) >= 3:
                prefixes.append(f"{tokens[0]} {tokens[1]} {tokens[2]}")

        if command.startswith("python -m "):
            parts = command.split()
            if len(parts) >= 3:
                prefixes.append(f"python -m {parts[2]}")
        if command.startswith("python3 -m "):
            parts = command.split()
            if len(parts) >= 3:
                prefixes.append(f"python3 -m {parts[2]}")
        if command.startswith("py -m "):
            parts = command.split()
            if len(parts) >= 3:
                prefixes.append(f"py -m {parts[2]}")

        return prefixes

    @classmethod
    def is_command_allowed(cls, agent_type: str, command: str) -> bool:
        """检查命令是否在指定 agent 类型的只读白名单中"""
        command_lower = command.lower().strip()

        # 首先检查是否包含禁止的模式
        for forbidden in cls.FORBIDDEN_PATTERNS:
            if forbidden in command_lower:
                return False

        allowed_commands = cls.COMMAND_ALLOWLISTS.get(agent_type.lower())
        if not allowed_commands:
            return False

        prefixes = cls._extract_command_prefixes(command)
        for prefix in prefixes:
            if prefix in allowed_commands:
                return True

        for allowed_cmd in allowed_commands:
            if command_lower.startswith(allowed_cmd + " "):
                return True

        return command_lower in allowed_commands


class SubAgent:
    """子agent执行器"""

    def __init__(self, agent_type: str, task: str, agent_id: str, tools_registry, output_callback=None, shared_state=None):
        """
        初始化子agent

        Args:
            agent_type: agent类型（Explore、Plan等）
            task: 要执行的任务描述
            agent_id: agent唯一标识符
            tools_registry: 工具注册表实例
            output_callback: 输出回调函数
            shared_state: 跨线程共享状态 dict，用于超时后提取最后文本
        """
        self.agent_type = agent_type
        self.task = task
        self.agent_id = agent_id
        self.tools_registry = tools_registry
        self.output_callback = output_callback
        self.shared_state = shared_state or {}
        self.last_text_answer = self.shared_state.get("last_text_answer", None)

        # 获取系统提示词
        self.system_prompt = SubAgentConfig.get_system_prompt(agent_type)

        # 获取允许的工具列表
        self.allowed_tools = SubAgentConfig.get_allowed_tools(agent_type)

        # 初始化LLM
        self.llm = LargeLanguageModel()

        # 仅支持 Anthropic 原生工具调用
        llm_config = config.get("model.large-language-model", {}) or {}
        self.provider = llm_config.get("provider", "anthropic_compatible")

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.token_count = 0
        self.tool_calls = []

        # JSONL 日志
        self._log_dir = Path("runtime_memory/logs/agents/sub")

    def _get_max_iterations(self) -> int:
        """获取 agent 类型对应的最大迭代次数，优先使用类型专属配置"""
        return int(
            config.get(
                f"agent_delegate.{self.agent_type.lower()}.max_iterations",
                config.get("agent_delegate.max_iterations", 8),
            )
        )

    def _get_max_context_tokens(self) -> int:
        """获取 agent 类型对应的上下文 token 上限"""
        return int(
            config.get(
                f"agent_delegate.{self.agent_type.lower()}.max_context_tokens",
                config.get("agent_delegate.max_context_tokens", 30000),
            )
        )

    def _build_verify_task_message(self) -> str:
        """为 verify 子 agent 构建结构化任务消息"""
        task_text = self.task.strip()
        sections = {
            "original_user_task": "(not provided)",
            "key_files": "(not provided)",
            "implementation_summary": "(not provided)",
            "expected_behavior": "(not provided)",
            "risk_points": "(not provided)",
            "constraints": "(not provided)",
        }

        current_key = None
        for raw_line in task_text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            matched = re.match(r"^([A-Za-z_ ]+):\s*(.*)$", line)
            if matched:
                key = matched.group(1).strip().lower().replace(" ", "_")
                if key in sections:
                    current_key = key
                    value = matched.group(2).strip()
                    sections[current_key] = value or sections[current_key]
                    continue
            if current_key:
                prev = sections[current_key]
                sections[current_key] = (
                    f"{prev}\n{line}" if prev and prev != "(not provided)" else line
                )
            else:
                sections["original_user_task"] = (
                    f"{sections['original_user_task']}\n{line}"
                    if sections["original_user_task"] != "(not provided)"
                    else line
                )

        lines = [
            f"⚠️ 这是你的第1次执行。你最多有{self._get_max_iterations()}次工具调用机会，每次机会都很宝贵。",
            "请优先证伪而不是确认 happy path，并确保至少执行一个与当前改动直接相关的 adversarial probe。",
            "",
            "## Verification Task",
            f"- Original user task: {sections['original_user_task']}",
            f"- Key files: {sections['key_files']}",
            f"- Implementation summary: {sections['implementation_summary']}",
            f"- Expected behavior: {sections['expected_behavior']}",
            f"- Risk points: {sections['risk_points']}",
            f"- Constraints: {sections['constraints']}",
            "",
            "输出必须包含 Verification Scope、Checks Performed、Adversarial Probes、Verdict 四个部分。",
        ]
        return "\n".join(lines)

    def _build_initial_task_text(self) -> str:
        if self.agent_type.lower() == "verify":
            return self._build_verify_task_message()

        max_iter = self._get_max_iterations()
        return (
            f"⚠️ 这是你的第1次执行。你最多有{max_iter}次工具调用机会，每次机会都很宝贵。\n"
            f"请高效规划：优先使用并行工具调用，专注于最关键的信息收集。\n\n"
            f"任务:\n{self.task}"
        )

    def _notify(self, message: str, level: str = "info"):
        """通知输出消息"""
        if self.output_callback:
            self.output_callback(message, level)

    def _build_anthropic_tools(self) -> List[Dict[str, Any]]:
        """构建 Anthropic 原生格式的工具定义"""
        tools = []
        for tool_name in self.allowed_tools:
            entry = self.tools_registry.get_entry(tool_name)
            if entry:
                tool_def = {
                    "name": entry.name,
                    "description": entry.description,
                    "input_schema": dict(entry.arguments_schema or {}),
                }
                tools.append(tool_def)
        return tools

    def _build_prompt_messages(self) -> List[Dict[str, Any]]:
        """构建提示消息，支持PromptCaching"""
        messages = []

        system_message = {
            "role": "system",
            "content": self.system_prompt,
            "cache_control": {"type": "ephemeral"}
        }
        messages.append(system_message)

        task_message = {
            "role": "user",
            "content": self._build_initial_task_text()
        }
        messages.append(task_message)

        return messages

    # ── LLM Resilience ──────────────────────────────────────

    @staticmethod
    def _is_retryable_llm_error(error):
        """判断 LLM 错误是否可重试（5xx / 429 rate limit / timeout）"""
        status = getattr(error, "status_code", None)
        if status is not None:
            return status >= 500 or status == 429
        msg = str(error).lower()
        return any(kw in msg for kw in (
            "server error", "internal error", "rate limit", "timeout",
            "503", "502", "500", "504", "429",
        ))

    def _call_llm_with_retry(self, messages, tools=None, max_retries=2):
        """调用 LLM 并在瞬态错误时重试，重试耗尽后抛出异常"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                gen_kwargs = {}
                if tools:
                    gen_kwargs["tools"] = tools
                response = self.llm.query(messages, **gen_kwargs)
                # 累加 token 用量
                usage = self.llm.get_last_usage()
                if usage:
                    self.token_count += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                return response
            except Exception as e:
                last_error = e
                if attempt < max_retries and self._is_retryable_llm_error(e):
                    import time as _time
                    self._notify(
                        f"  ⚠ [{self.agent_id}] LLM调用失败，重试 ({attempt+1}/{max_retries}): {e}",
                        "warning",
                    )
                    _time.sleep(1.0)
                    continue
                raise
        raise last_error  # pragma: no cover — unreachable, keeps type checker happy

    def _handle_truncation(self, response, messages, tools, continue_count=0, max_continues=2):
        """max_tokens 截断时自动续写，递归直到完成或达到最大续写次数"""
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason != "max_tokens" or continue_count >= max_continues:
            return None  # 不续写

        # 保存截断内容
        native_text = self._extract_native_text(response)
        native_thinking = self._extract_native_thinking(response)
        parts = []
        if native_thinking:
            parts.append(native_thinking)
        if native_text:
            parts.append(native_text)
        if parts:
            messages.append({"role": "assistant", "content": "\n".join(parts)})

        # 注入续写指令
        messages.append({
            "role": "user",
            "content": "Continue exactly where you stopped. Do not repeat previous content.",
        })

        self._notify(
            f"  ⏩ [{self.agent_id}] max_tokens截断，自动续写 ({continue_count+1}/{max_continues})",
            "info",
        )

        new_response = self._call_llm_with_retry(messages, tools, max_retries=1)
        # 递归检查是否还需要续写
        deeper = self._handle_truncation(new_response, messages, tools, continue_count + 1, max_continues)
        return deeper if deeper is not None else new_response

    # ── Context Management ─────────────────────────────────

    def _manage_context(self, messages, system_messages_count=2):
        """Token 感知的上下文管理：先尝试压缩，失败再硬截断"""
        max_context = self._get_max_context_tokens()
        estimator = TokenEstimator()
        estimated = estimator.estimate(str(messages))

        if estimated < max_context:
            return messages

        # 尝试自动摘要
        if config.get("agent.context_compact.enabled", False):
            try:
                compactor = ContextCompactor()
                summary = compactor.compact(messages)
                if summary and len(summary) >= 20:
                    self._notify(
                        f"  📦 [{self.agent_id}] 上下文压缩 ({len(messages)} msg → {len(summary)} chars)",
                        "info",
                    )
                    return messages[:system_messages_count] + [
                        {"role": "user", "content": f"[上下文摘要]\n\n{summary}"}
                    ]
            except Exception as e:
                self._notify(f"  ⚠ [{self.agent_id}] 压缩失败，回退硬截断: {e}", "warning")

        # 硬截断：保留 system + 最近 N 对完整轮次
        max_turns = int(config.get("agent_delegate.max_history_turns", 3))
        body = messages[system_messages_count:]
        keep_count = min(max_turns * 2, len(body))
        truncated = messages[:system_messages_count]
        truncated.extend(body[-keep_count:])

        # 确保截断后不从 assistant 开始（避免半轮）
        if len(truncated) > system_messages_count and truncated[system_messages_count].get("role") == "assistant":
            truncated = messages[:system_messages_count] + body[-(keep_count + 1):]

        self._notify(
            f"  📐 [{self.agent_id}] 上下文截断: {len(messages)} → {len(truncated)} msgs",
            "info",
        )
        return truncated

    def _should_use_native_tools(self) -> bool:
        """判断是否满足 Anthropic 原生工具调用前提"""
        return (
            self.provider == "anthropic_compatible"
            and bool(self.allowed_tools)
        )

    def _is_native_response(self, response) -> bool:
        """判断响应是否为 Anthropic 原生响应对象（而非纯文本）"""
        return hasattr(response, "content")

    def _parse_native_tool_calls(self, response) -> List[Dict[str, Any]]:
        """从 Anthropic 原生响应中解析 tool_use 块"""
        tool_calls = []
        content = getattr(response, "content", [])
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id"),
                        "tool": block.get("name"),
                        "args": block.get("input", {}),
                    })
            else:
                if getattr(block, "type", None) == "tool_use":
                    tool_calls.append({
                        "id": getattr(block, "id"),
                        "tool": getattr(block, "name"),
                        "args": getattr(block, "input", {}),
                    })
        return tool_calls

    def _extract_native_text(self, response) -> str:
        """从 Anthropic 原生响应中提取 text 块内容"""
        text_parts = []
        content = getattr(response, "content", [])
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            else:
                if getattr(block, "type", None) == "text":
                    text_parts.append(getattr(block, "text", ""))
        return "\n".join(part for part in text_parts if part).strip()

    def _extract_native_thinking(self, response) -> Optional[str]:
        """从 Anthropic 原生响应中提取 thinking 内容"""
        thinking_parts = []
        content = getattr(response, "content", [])
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
            else:
                if getattr(block, "type", None) == "thinking":
                    thinking_parts.append(getattr(block, "thinking", ""))
        combined = "\n".join(part for part in thinking_parts if part).strip()
        return combined or None

    # ── Tool Execution ──────────────────────────────────────

    def _run_async_in_thread(self, coro, timeout=60):
        """在 SubAgent 工作线程中安全执行 async 协程"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中的事件循环（典型场景：run_in_executor 线程）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    asyncio.wait_for(coro, timeout=timeout)
                )
            finally:
                loop.close()

        # 已有事件循环时通过 Future 跨线程执行
        import concurrent.futures
        future = concurrent.futures.Future()

        async def _runner():
            try:
                result = await asyncio.wait_for(coro, timeout=timeout)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)

        import threading
        t = threading.Thread(target=lambda: asyncio.run(_runner()), daemon=True)
        t.start()
        return future.result(timeout=timeout + 5)

    def _validate_tool_access(self, tool_name: str, tool_args: dict) -> Optional[str]:
        """校验工具权限，返回 None 表示通过，否则返回错误 JSON"""
        if tool_name not in self.allowed_tools:
            return json.dumps({
                "error": f"工具 '{tool_name}' 不允许在 {self.agent_type} agent 中使用",
                "allowed_tools": self.allowed_tools,
            }, ensure_ascii=False)

        if tool_name == "run_cmd":
            command = tool_args.get("cmd", "")
            if not SubAgentConfig.is_command_allowed(self.agent_type, command):
                return json.dumps({
                    "error": f"命令 '{command}' 不在只读白名单中",
                    "hint": "仅允许只读命令，如 ls, cat, git status, git log, git diff 等",
                }, ensure_ascii=False)

        return None

    def _execute_tools_safely(self, tool_use_blocks: list) -> list:
        """通过 registry.dispatch() 安全执行工具（错误不崩溃，转为 feedback）

        Returns:
            [{"tool_use_id": ..., "tool": ..., "result": ..., "is_error": bool}, ...]
        """
        results = []
        tool_timeout = config.get("tools.timeout", 60)
        for tb in tool_use_blocks:
            tool_name = tb["tool"]
            tool_args = tb.get("args", tb.get("input", {}))

            # 权限校验
            access_error = self._validate_tool_access(tool_name, tool_args)
            if access_error:
                results.append({
                    "tool_use_id": tb["id"],
                    "tool": tool_name,
                    "args": tool_args,
                    "result": access_error,
                    "is_error": True,
                })
                continue

            # 通过 registry 统一调度（含参数验证、线程池执行）
            try:
                result = self._run_async_in_thread(
                    self.tools_registry.dispatch(tool_name, tool_args),
                    timeout=tool_timeout,
                )
                result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                is_error = ToolCall.classify_is_error(tool_name, result_str)
            except asyncio.TimeoutError:
                result_str = json.dumps({"error": f"Tool '{tool_name}' timed out after {tool_timeout}s"}, ensure_ascii=False)
                is_error = True
            except Exception as e:
                result_str = json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {e}"}, ensure_ascii=False)
                is_error = True

            results.append({
                "tool_use_id": tb["id"],
                "tool": tool_name,
                "args": tool_args,
                "result": result_str,
                "is_error": is_error,
            })

        return results

    def _build_result(self, status: str, content: str, iteration: int,
                      error: str = "", warning: str = "") -> Dict[str, Any]:
        """构建标准化的执行结果字典"""
        self.end_time = time.time()
        duration_ms = int((self.end_time - self.start_time) * 1000) if self.start_time else 0

        result = {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": status,
            "content": content,
            "token_count": self.token_count,
            "duration_ms": duration_ms,
            "tool_calls_count": len(self.tool_calls),
            "iterations": iteration + 1,
        }
        if error:
            result["error"] = error
        if warning:
            result["warning"] = warning

        # JSONL end 事件
        _write_agent_jsonl(self._log_dir, self.agent_id, {
            "event": "end",
            "status": status,
            "error": error,
            "warning": warning,
            "iterations": iteration + 1,
            "tool_calls_count": len(self.tool_calls),
            "token_count": self.token_count,
            "duration_ms": duration_ms,
            "timestamp": self.end_time,
        })

        return result

    def _scan_messages_for_last_text(self, messages: List[Dict]) -> Optional[str]:
        """扫描消息列表，返回最后一条 assistant 的 text/thinking 内容（排除纯 tool_use 消息）"""
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") in ("text", "thinking"):
                            text_parts.append(block.get("text", "") or block.get("thinking", ""))
                    elif hasattr(block, "type") and getattr(block, "type") in ("text", "thinking"):
                        text_parts.append(getattr(block, "text", "") or getattr(block, "thinking", ""))
                if text_parts:
                    return "\n".join(text_parts)
            elif isinstance(content, str) and content.strip():
                return content.strip()
        return None

    def execute(self) -> Dict[str, Any]:
        """
        执行子agent任务（多轮 ReAct 循环，含 LLM 重试、截断续写、registry 工具调度）

        Returns:
            执行结果字典，包含 status / content / error 等字段
        """
        self.start_time = time.time()
        MAX_LLM_RETRIES = 2
        MAX_AUTO_CONTINUE = 2

        _write_agent_jsonl(self._log_dir, self.agent_id, {
            "event": "start",
            "agent_type": self.agent_type,
            "task": self.task,
            "timestamp": self.start_time,
        })

        try:
            messages = self._build_prompt_messages()
            max_iterations = self._get_max_iterations()
            if not self._should_use_native_tools():
                raise ValueError(
                    "SubAgent requires anthropic_compatible provider and at least one allowed tool"
                )
            anthropic_tools = self._build_anthropic_tools()

            final_answer = None
            iteration = 0

            for iteration in range(max_iterations):
                self._notify(f"  🔄 [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info")
                remaining = max_iterations - (iteration + 1)

                try:
                    response = self._call_llm_with_retry(messages, anthropic_tools, MAX_LLM_RETRIES)
                except Exception as e:
                    self._notify(f"  ❌ [{self.agent_id}] LLM 调用完全失败: {e}", "error")
                    fallback = self._scan_messages_for_last_text(messages) or ""
                    return self._build_result("error", fallback, iteration, error=f"LLM call failed: {e}")

                # ══════ Phase 2: max_tokens 截断续写 ══════
                if self._is_native_response(response):
                    continued = self._handle_truncation(
                        response, messages, anthropic_tools, 0, MAX_AUTO_CONTINUE
                    )
                    if continued is not None:
                        response = continued

                # ══════ Phase 3: 解析响应 ══════
                if not self._is_native_response(response):
                    raise ValueError("Native subagent mode requires Anthropic response objects with content blocks")

                native_text = self._extract_native_text(response)
                native_thinking = self._extract_native_thinking(response)
                tool_use_blocks = self._parse_native_tool_calls(response)

                self._notify(
                    f"  💭 [{self.agent_id}] 思考: "
                    + ((native_text or native_thinking or "(no text)")[:200]),
                    "debug",
                )

                if native_text or native_thinking:
                    self.last_text_answer = native_text or native_thinking
                    self.shared_state["last_text_answer"] = self.last_text_answer

                _write_agent_jsonl(self._log_dir, self.agent_id, {
                    "event": "iteration", "iteration": iteration + 1, "native": True,
                    "text_preview": (native_text[:200] if native_text else None),
                    "thinking_preview": (native_thinking[:200] if native_thinking else None),
                    "tool_use_count": len(tool_use_blocks),
                    "tool_names": [tb["tool"] for tb in tool_use_blocks],
                    "timestamp": time.time(),
                })

                if native_thinking and not native_text and not tool_use_blocks:
                    messages.append({
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": native_thinking}],
                    })
                    continue

                if not tool_use_blocks:
                    self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info")
                    final_answer = native_text or ""
                    break

                assistant_content = []
                if native_text:
                    assistant_content.append({"type": "text", "text": native_text})
                for tb in tool_use_blocks:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tb["id"],
                        "name": tb["tool"],
                        "input": tb.get("args", tb.get("input", {})),
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = self._execute_tools_safely(tool_use_blocks)

                user_content = [{"type": "text", "text": (
                    f"⚠️ 剩余可执行次数: {remaining}/{max_iterations}。"
                    f"用完将强制结束。请高效利用剩余机会。" if remaining > 0 else
                    f"⚠️ 最后机会！本次执行后将被强制结束，必须输出文本答案，不能再调用工具。"
                )}]
                for tr in tool_results:
                    args_str = json.dumps(tr.get("args", {}), ensure_ascii=False)[:100]
                    self._notify(
                        f"  🔧 [{self.agent_id}] 调用工具: {tr['tool']}({args_str}...)", "info"
                    )
                    result_preview = tr["result"][:150] + "..." if len(tr["result"]) > 150 else tr["result"]
                    self._notify(
                        f"  {'⚠' if tr['is_error'] else '✓'} [{self.agent_id}] 工具结果: {result_preview}", "debug"
                    )
                    user_content.append(
                        ToolCall.create_anthropic_tool_result(
                            tr["tool_use_id"], tr["result"], is_error=tr["is_error"]
                        )
                    )
                    self.tool_calls.append({
                        "tool": tr["tool"], "args": tr.get("args", {}),
                        "is_error": tr["is_error"], "timestamp": time.time(),
                    })
                messages.append({"role": "user", "content": user_content})

                messages = self._manage_context(messages)

            # ══════ 循环结束：构建结果 ══════
            if final_answer is None:
                self._notify(f"  ⚠️ [{self.agent_id}] 达到最大迭代次数", "warning")
                final_answer = (
                    self.last_text_answer
                    or self._scan_messages_for_last_text(messages)
                    or ""
                )
                if not final_answer:
                    final_answer = (
                        f"子Agent [{self.agent_id}] 在 {max_iterations} 轮内"
                        f"未能生成文本答案（已执行 {len(self.tool_calls)} 次工具调用）"
                    )
                return self._build_result(
                    "partial", final_answer, iteration,
                    warning="Reached max iterations"
                )

            return self._build_result("success", final_answer, iteration)

        except Exception as e:
            import traceback
            self._notify(f"  ❌ [{self.agent_id}] 执行异常: {e}", "error")
            return self._build_result(
                "error", "", 0,
                error=f"{e}\n{traceback.format_exc()}",
            )
