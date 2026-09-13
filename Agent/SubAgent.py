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
from Core.timing import timeit
from configurationLoader import config
from Tools.git_status_provider import get_git_verify_context


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
        """从 .md 文件加载子agent系统提示词（带缓存），并附加当前运行环境说明"""
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
        from Tools.platform_utils import get_environment_hint
        content = content + "\n\n" + get_environment_hint(for_subagent=True)
        cls._prompt_cache[agent_type] = content
        return content

    # 按 agent 类型区分的只读命令白名单
    BASE_READONLY_COMMANDS = {
        # POSIX / Git Bash（Windows 上 run_cmd 也走 bash，命令直觉统一为 POSIX）
        'ls', 'pwd', 'cd', 'less', 'more', 'wc', 'sort',
        'which', 'ps', 'uname', 'whoami', 'hostname',
        # Git
        'git status', 'git log', 'git diff', 'git show',
        'git branch', 'git remote', 'git stash list', 'git tag',
        'git rev-parse', 'git config --list', 'git blame', 'git whatchanged',
        'git shortlog', 'git describe', 'git ls-files', 'git ls-tree',
    }

    EXPLORE_PLAN_COMMANDS = {
        'find',
    }

    COMMAND_ALLOWLISTS = {
        'explore': BASE_READONLY_COMMANDS | EXPLORE_PLAN_COMMANDS,
        'plan': BASE_READONLY_COMMANDS | EXPLORE_PLAN_COMMANDS,
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

    # sleep 等待上限：验证代理不得用长睡眠干等（浪费轮次与时间），单次命令
    # 中 sleep 总时长超过该值即拦截。轮询等待应拆成多次短 sleep。
    SLEEP_MAX_SECONDS = 20
    _SLEEP_ARG_RE = re.compile(r'^(?P<num>\d+(?:\.\d+)?)(?P<unit>[smhd]?)$', re.IGNORECASE)
    _SLEEP_MULTI_ARG_RE = re.compile(r'^(?:(?P<n>\d+(?:\.\d+)?)(?P<u>[smhd]))+$', re.IGNORECASE)

    @classmethod
    def _sleep_exceeds_limit(cls, command: str) -> bool:
        """检查命令中 sleep 的总时长是否超过 SLEEP_MAX_SECONDS。

        支持纯秒数与 s/m/h/d 后缀（含拼接形式如 1m30s）；无法解析的参数
        一律视为超限（宁可误拦，不放过长睡眠）。
        """
        for seg in re.split(r'[;&|]', command):
            m = re.match(r'^\s*sleep\s+(.+)$', seg.strip(), re.IGNORECASE)
            if not m:
                continue
            total = 0.0
            for arg in m.group(1).split():
                simple = cls._SLEEP_ARG_RE.match(arg)
                if simple:
                    unit = simple.group('unit').lower()
                    multiplier = {'': 1.0, 's': 1.0, 'm': 60.0, 'h': 3600.0, 'd': 86400.0}[unit]
                    total += float(simple.group('num')) * multiplier
                    continue
                multi = cls._SLEEP_MULTI_ARG_RE.match(arg)
                if multi:
                    for piece in re.finditer(r'(\d+(?:\.\d+)?)([smhd])', arg, re.IGNORECASE):
                        unit = piece.group(2).lower()
                        multiplier = {'s': 1.0, 'm': 60.0, 'h': 3600.0, 'd': 86400.0}[unit]
                        total += float(piece.group(1)) * multiplier
                    continue
                return True  # 解析不了的 sleep 参数，视为超限
            if total > cls.SLEEP_MAX_SECONDS:
                return True
        return False

    # 每轮重申的核心约束（对齐 Claude Code 的 criticalSystemReminder：
    # 系统提示词只在开头注入一次，长跑后约束会衰减；该提醒在每一轮
    # user 消息中重新注入，保证核心约束始终贴近最新输入）
    CRITICAL_REMINDERS = {
        "explore": (
            "CRITICAL: 这是只读探索任务。禁止创建、修改、删除任何文件（包括临时文件）。"
            "找到足够信息后立即以文本返回分析报告，不要扩大搜索面。"
        ),
        "plan": (
            "CRITICAL: 这是只读规划任务。禁止创建、修改、删除任何文件（包括临时文件）。"
            "充分探索后输出完整的实现计划。"
        ),
        "verify": (
            "CRITICAL: 这是只读验证任务。禁止在项目目录创建、修改、删除任何文件"
            "（测试文件必须用 WriteTestFile 写到系统临时目录）。每条 PASS 必须有实际执行的命令和真实输出，"
            "禁止用 py_compile / 语法检查冒充验证。报告必须以 VERDICT: PASS / FAIL / PARTIAL 结尾。"
        ),
    }

    # 无害重定向（丢弃输出/合并 fd），检查前先剥离：`2>nul`、`2>/dev/null`、`2>&1`、`>nul`、`>/dev/null`
    _BENIGN_REDIRECT = re.compile(r"\s*\d?>{1,2}\s*(?:nul|/dev/null)\b|\s*2>&1", re.IGNORECASE)

    @classmethod
    def get_system_prompt(cls, agent_type: str) -> str:
        """获取指定类型agent的系统提示词（从 prompts/subagent/*.md 加载）"""
        return cls._load_prompt(agent_type)

    @classmethod
    def get_allowed_tools(cls, agent_type: str) -> List[str]:
        """获取指定类型agent允许使用的工具"""
        if agent_type.lower() in ["explore", "plan"]:
            return ["Read", "Grep", "Glob", "run_cmd"]
        elif agent_type.lower() == "verify":
            return ["Read", "Grep", "Glob", "run_cmd", "WriteTestFile"]
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

    # for 循环 do 部分允许的内部命令（只读输出类）
    # 例: bash `for d in *; do echo $d; done` —— echo/ls 等只读输出可放行；
    # del/rm/mv/cp/mkdir 等危险内部命令会被 FORBIDDEN_PATTERNS 先行拦截，这里是双保险
    FOR_INNER_ALLOWED = {'echo', 'ls', 'wc', 'sort'}
    _FOR_DO_PATTERN = re.compile(r'\bdo\s+@?([a-zA-Z][\w.\-]*)')

    @classmethod
    def is_command_allowed(cls, agent_type: str, command: str) -> bool:
        """检查命令是否在指定 agent 类型的只读白名单中"""
        command_lower = command.lower().strip()
        # 剥离无害重定向（2>nul / 2>/dev/null / 2>&1 等）再检查禁止模式
        cleaned = cls._BENIGN_REDIRECT.sub("", command_lower)

        # 首先检查是否包含禁止的模式
        for forbidden in cls.FORBIDDEN_PATTERNS:
            if forbidden in cleaned:
                return False

        # sleep 等待上限（主要约束 verify 的黑名单放行模式）
        if cls._sleep_exceeds_limit(cleaned):
            return False

        # verify 采用黑名单模式：不在 FORBIDDEN_PATTERNS 里即放行
        # （不再受只读白名单约束，使其能运行 node/pytest 等真实验证命令）
        if agent_type.lower() == "verify":
            return True

        allowed_commands = cls.COMMAND_ALLOWLISTS.get(agent_type.lower())
        if not allowed_commands:
            return False

        # shell 语法结构：for 循环 —— 校验 do 部分的内部命令必须只读
        if command_lower.startswith("for "):
            m = cls._FOR_DO_PATTERN.search(cleaned)
            if not m:
                return False
            return m.group(1) in cls.FOR_INNER_ALLOWED

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

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.token_count = 0
        # 最近一次 LLM 响应的 usage（供 TUI 子代理视图显示 Token/Cache Hit）
        self.last_usage = None
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
        """为 verify 子 agent 构建任务消息。

        Git 状态（收敛到主 Agent 本轮触及文件）与主 Agent 的工具调用历史
        由框架在此预填充；主 Agent 只提供轻量说明（改了什么、为什么）。
        """
        task_text = self.task.strip()

        try:
            from Tools.touched_files import get_touched_files
            touched = get_touched_files()
        except Exception:
            touched = None
        git_context = get_git_verify_context(touched_files=touched)

        try:
            from Tools.tool_call_board import get_main_calls, render_board
            main_board = render_board(get_main_calls(), "主 Agent 本轮工具调用历史")
        except Exception:
            main_board = None

        lines = [
            f"⚠️ 这是你的第1次执行。你最多有{self._get_max_iterations()}次工具调用机会，每次机会都很宝贵。",
            "Git 状态与 diff、主 Agent 的工具调用历史已由框架预填充到下方。禁止再自己运行 git status / git diff。",
            "你是独立于主 Agent 的对抗性验证者：依据预填充的 diff 和主 Agent 的轻量说明，自己读改动文件、自己设计并编写测试用例、自己运行真实测试。",
            "先运行真实测试（pytest / 复现场景），再读代码；禁止用 py_compile / 语法检查冒充验证。",
            "请优先证伪而不是确认 happy path，并确保至少执行一个与当前改动直接相关的 adversarial probe。",
            "",
            "## Git 状态与改动（已预填充）",
            git_context,
            "",
        ]
        if main_board:
            lines += [main_board, ""]
        lines += [
            "## 主 Agent 的说明（轻量）",
            task_text if task_text else "(主 Agent 未提供说明，请依据 diff 自行判断)",
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

    def _notify(self, message: str, level: str = "info", category: str = "info",
                content: Optional[str] = None, tool: Optional[str] = None, is_error: bool = False):
        """通知输出消息（结构化 payload，供 Gateway 转发 TUI）

        category: iteration/thinking/tool_call/tool_result/final/error/info
        content: 完整内容（思考全文、工具参数/结果等），TUI 用于上下文视图渲染
        """
        if self.output_callback:
            payload = {
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "task": self.task,
                "kind": "activity",
                "level": level,
                "category": category,
                "message": message,
            }
            if content is not None:
                payload["content"] = content
            if tool:
                payload["tool"] = tool
            if is_error:
                payload["is_error"] = True
            self.output_callback(payload)

    def _emit_token_stats(self):
        """向 TUI 推送本子代理的最新 Token/Cache 统计（kind=stats，不进活动流）。

        current_tokens = 最近一次请求的总输入（直连+缓存读+缓存写），即该
        子代理当前的上下文体量；命中率口径与主代理 token.stats 一致。
        """
        u = self.last_usage or {}
        uncached = u.get("input_tokens") or 0
        cache_read = u.get("cache_read_input_tokens") or 0
        cache_write = u.get("cache_creation_input_tokens") or 0
        total_input = uncached + cache_read + cache_write
        if self.output_callback:
            self.output_callback({
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "task": self.task,
                "kind": "stats",
                "level": "debug",
                "category": "info",
                "message": "",
                "token_stats": {
                    "current_tokens": total_input,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "uncached_input_tokens": uncached,
                    "prompt_cache_ratio": (cache_read / total_input) if total_input > 0 else 0.0,
                },
            })

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

        task_text = self._build_initial_task_text()
        reminder = SubAgentConfig.CRITICAL_REMINDERS.get(self.agent_type.lower())
        if reminder:
            task_text = reminder + "\n\n" + task_text
        messages.append({"role": "user", "content": task_text})
        self._advance_cache_breakpoint(messages)

        return messages

    @staticmethod
    def _advance_cache_breakpoint(messages, system_count: int = 1) -> None:
        """把历史缓存断点推进到最新一条消息（每步移动一次）。

        断点语义：缓存从会话开头到断点为止的全部内容。消息列表是纯追加式，
        每步把断点移到最新消息，上一轮的完整历史即可整体命中缓存——
        verify 等长循环（最多 50 轮）收益最大。MiniMax 网关按内容匹配前缀，
        断点标记移动不影响命中（E2E 实测 cache_write=0）。
        """
        for m in messages[system_count:]:
            m.pop("cache_control", None)
        if len(messages) > system_count:
            messages[-1]["cache_control"] = {"type": "ephemeral"}

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
                with timeit("subagent.llm.query"):
                    response = self.llm.query(messages, **gen_kwargs)
                # 累加 token 用量
                usage = self.llm.get_last_usage()
                if usage:
                    self.token_count += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    self.last_usage = usage
                    self._emit_token_stats()
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
            "role": "system",
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
        """判断当前 provider 是否支持原生工具调用且有可用工具"""
        return (
            self.llm.core.provider_supports_native_tools
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
        t = threading.Thread(target=lambda: asyncio.run(_runner()), daemon=True, name=f"subagent-{getattr(self, 'agent_type', 'run')}")
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
            if SubAgentConfig._sleep_exceeds_limit(command):
                return json.dumps({
                    "error": (f"sleep 总时长超过上限 {SubAgentConfig.SLEEP_MAX_SECONDS}s，"
                              f"已拦截（命令: {command}）"),
                    "hint": "禁止用长睡眠干等。轮询等待请拆成多次 ≤20s 的短 sleep，"
                            "或直接运行测试/进程并依赖其自身的超时与退出码。",
                }, ensure_ascii=False)
            if not SubAgentConfig.is_command_allowed(self.agent_type, command):
                # 区分拦截原因，让模型知道怎么改
                cleaned = SubAgentConfig._BENIGN_REDIRECT.sub("", command.lower().strip())
                matched = next(
                    (p for p in SubAgentConfig.FORBIDDEN_PATTERNS if p in cleaned), None,
                )
                if matched:
                    reason = f"命令包含被禁止的模式 '{matched}'（子代理禁止重定向写入/删除/权限修改）"
                else:
                    reason = f"命令 '{command}' 不在只读白名单中"
                return json.dumps({
                    "error": reason,
                    "hint": "仅允许只读命令，如 ls、git status/log/diff、find、wc、sort、ps 等；"
                            "可附带 2>/dev/null。读文件请用 Read，搜索用 Grep/Glob。",
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
                with timeit(f"subagent.tool.{tool_name}"):
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

        # 与主 Agent 相同的结果预算：超大结果截断到缓存文件，
        # 防止子代理上下文被巨量工具输出塞爆而被迫强制压缩
        from Agent.ToolResultBudget import ToolResultBudget
        budget_cfg = config.get("tools.result_budget", {}) or {}
        budget_items = []
        for r in results:
            item = {"tool_name": r["tool"], "result_text": r["result"]}
            if r["is_error"]:
                item["error"] = True
            budget_items.append(item)
        ToolResultBudget.apply(
            budget_items,
            cache_dir=budget_cfg.get("cache_dir", "./runtime_memory/tool_cache"),
            max_single_tokens=budget_cfg.get("max_single_tokens", 8000),
            max_batch_tokens=budget_cfg.get("max_batch_tokens", 24000),
        )
        for r, item in zip(results, budget_items):
            r["result"] = item["result_text"]

        return results

    # verify 报告末尾的机器可解析判定行，如 "VERDICT: PASS"（容忍 markdown 加粗与大小写）
    _VERDICT_PATTERN = re.compile(
        r"VERDICT:\s*\*{0,2}(PASS|FAIL|PARTIAL)\*{0,2}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    @classmethod
    def parse_verdict(cls, content: str) -> Optional[str]:
        """从 verify 报告中解析 VERDICT 判定（PASS/FAIL/PARTIAL）。

        取最后一个匹配——报告正文可能引用格式说明中出现过的示例判定行。
        无法解析时返回 None，调用方应将其视为"报告不完整"。
        """
        if not content:
            return None
        matches = cls._VERDICT_PATTERN.findall(content)
        if not matches:
            return None
        return matches[-1].upper()

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

        # verify 类型：机器解析 VERDICT，主 agent 不再需要通读自由文本找结论
        if self.agent_type.lower() == "verify" and status != "error":
            verdict = self.parse_verdict(content)
            if verdict:
                result["verdict"] = verdict
                self._notify(
                    f"  📋 [{self.agent_id}] VERDICT: {verdict}",
                    "info", category="final",
                )
            else:
                missing_msg = "报告缺少 VERDICT: PASS/FAIL/PARTIAL 行，判定无效"
                warning = f"{warning}；{missing_msg}" if warning else missing_msg
                self._notify(
                    f"  ⚠️ [{self.agent_id}] {missing_msg}",
                    "warn", category="final",
                )
        if warning:
            result["warning"] = warning

        # JSONL end 事件
        _write_agent_jsonl(self._log_dir, self.agent_id, {
            "event": "end",
            "status": status,
            "verdict": result.get("verdict"),
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
                    "SubAgent requires a tool-capable provider and at least one allowed tool"
                )
            anthropic_tools = self._build_anthropic_tools()

            final_answer = None
            iteration = 0

            for iteration in range(max_iterations):
                self._notify(f"  🔄 [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info",
                             category="iteration",
                             content=f"{iteration + 1}/{max_iterations}")
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
                    category="thinking",
                    content=(native_text or native_thinking or "")[:4000],
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
                    self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info",
                                 category="final", content=(native_text or "")[:8000])
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

                if remaining > 0:
                    countdown = (
                        f"⚠️ 剩余可执行次数: {remaining}/{max_iterations}。"
                        f"用完将强制结束。请高效利用剩余机会。"
                    )
                    # verify 专属预警：第 35 轮起仍未结束时，引导把剩余轮次集中到
                    # 真实运行测试（动态验证），而非继续读代码 / 探测环境
                    if self.agent_type.lower() == "verify" and (iteration + 1) >= 35:
                        countdown += (
                            " 即将结束：请把剩余轮次集中到「真实运行测试」的动态代码验证上，"
                            "不要再用轮次读代码或探测环境。"
                        )
                else:
                    countdown = (
                        "⚠️ 最后机会！本次执行后将被强制结束，必须输出文本答案，不能再调用工具。"
                    )
                user_content = [{"type": "text", "text": countdown}]
                reminder = SubAgentConfig.CRITICAL_REMINDERS.get(self.agent_type.lower())
                if reminder:
                    user_content.append({"type": "text", "text": reminder})
                for tr in tool_results:
                    args_str = json.dumps(tr.get("args", {}), ensure_ascii=False)[:100]
                    self._notify(
                        f"  🔧 [{self.agent_id}] 调用工具: {tr['tool']}({args_str}...)", "info",
                        category="tool_call", tool=tr["tool"],
                        content=json.dumps(tr.get("args", {}), ensure_ascii=False)[:2000],
                    )
                    result_preview = tr["result"][:150] + "..." if len(tr["result"]) > 150 else tr["result"]
                    self._notify(
                        f"  {'⚠' if tr['is_error'] else '✓'} [{self.agent_id}] 工具结果: {result_preview}", "debug",
                        category="tool_result", tool=tr["tool"],
                        content=tr["result"][:4000], is_error=tr["is_error"],
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
                    try:
                        from Tools.tool_call_board import record_subagent_call
                        record_subagent_call(
                            self.agent_id,
                            tr["tool"],
                            json.dumps(tr.get("args", {}), ensure_ascii=False)[:100],
                            tr["is_error"],
                        )
                    except Exception:
                        pass
                messages.append({"role": "user", "content": user_content})

                messages = self._manage_context(messages)
                self._advance_cache_breakpoint(messages)

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
