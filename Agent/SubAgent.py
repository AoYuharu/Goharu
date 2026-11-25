"""
SubAgent - 子agent执行模块（薄驱动）

循环引擎与主 Agent 完全同一套：ActorAgent.act()（解析 / thinking 结构化
回传 / max_tokens 截断续写 / 工具并发执行 / 结果预算）。本文件只保留子代理
的差异策略：
  - 权力范围：_SubToolRuntime（allowed_tools 白名单 + run_cmd 命令策略）
  - 提示词：  SubAgent._build_step_messages（system + task + reminder，
              无记忆/persona 注入），经 document_builder 注入引擎
  - 驱动：    execute() 迭代循环（倒计时 + CRITICAL_REMINDER 每轮注入、
              JSONL 日志、verify VERDICT 解析）

历史：子循环原是复制粘贴的第二份 ReAct 实现，2026-10-18 因 thinking 压平
回传触发 400（主循环已修、子循环漏同步）后收敛到单一引擎，此后此类约束只
需修一处。
"""

import asyncio
import copy
import json
import re
import shlex
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

from Agent.ActorAgent import ActorAgent
from Agent.LargeLanguageModel import LargeLanguageModel
from Core.TokenEstimator import TokenEstimator
from Prompting.ContextCompactor import ContextCompactor
from Prompting.loop_messages import (
    sanitize_messages_for_api,
    truncate_history,
)
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
        "architect": "architect.md",
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

    # architect 专用禁止模式：只挡 shell 重定向写文件（写文件必须走 Write/Edit）。
    # 其余高危操作（rm/del/taskkill/reg 等）不在此硬拦——它们落到 run_cmd
    # 工具内部的三态安全链：dangerous 硬黑名单直接拒、require_confirmation
    # 灰名单弹人工确认（这是 architect 与只读子代理的核心权限差异）。
    ARCHITECT_FORBIDDEN_PATTERNS = [
        '>', '>>', '<<',  # 重定向
        '2>', '1>', '&>',  # fd 重定向
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
        "architect": (
            "CRITICAL: 你是 Goharu 项目的管理/升级智能体，拥有写权限——但纪律优先："
            "先 Read 后 Edit；文件写走 Write/Edit，禁止 shell 重定向写文件；"
            "跨模块改动只走 facade 与注入缝，不碰模块内部；"
            "灰名单命令（del/taskkill/reg 等）会阻塞等人工确认，被拒绝就换方案不要重试；"
            "独立只读调查可用 AgentDelegate 委托 explore，可并行，禁止其他类型；"
            "改动完成后必须运行对应模块的契约测试并报告真实输出。"
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
        elif agent_type.lower() == "architect":
            # Goharu 自身管理/升级：全量读写 + MessageHero 对外沟通 + SetConfig 改配置。
            # 高危灰名单命令（del/taskkill/reg 等）由 run_cmd 工具内部的
            # confirmation_manager 拦截弹人工确认
            return ["Read", "Grep", "Glob", "run_cmd", "Write", "Edit",
                    "MessageHero", "SetConfig", "ErrorHook", "AgentDelegate"]
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
        """检查命令是否在指定 agent 类型的允许范围内"""
        command_lower = command.lower().strip()
        # 剥离无害重定向（2>nul / 2>/dev/null / 2>&1 等）再检查禁止模式
        cleaned = cls._BENIGN_REDIRECT.sub("", command_lower)

        # sleep 等待上限对所有类型生效
        if cls._sleep_exceeds_limit(cleaned):
            return False

        # verify 采用黑名单模式：不在 FORBIDDEN_PATTERNS 里即放行
        # （不再受只读白名单约束，使其能运行 node/pytest 等真实验证命令）
        if agent_type.lower() == "verify":
            for forbidden in cls.FORBIDDEN_PATTERNS:
                if forbidden in cleaned:
                    return False
            return True

        # architect 采用宽黑名单：只挡 shell 重定向写文件；del/taskkill 等
        # 灰名单命令放行给 run_cmd 内部确认链弹人工确认（见类上注释）
        if agent_type.lower() == "architect":
            for forbidden in cls.ARCHITECT_FORBIDDEN_PATTERNS:
                if forbidden in cleaned:
                    return False
            return True

        # explore/plan：先过禁止模式，再走只读白名单
        for forbidden in cls.FORBIDDEN_PATTERNS:
            if forbidden in cleaned:
                return False

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


class _SubToolRuntime:
    """子代理工具运行时适配器：把 allowed_tools 白名单与命令策略包成
    ActorAgent 需要的 tool_runtime 接口（last_tool_definitions / list_tools /
    call_tool）。权力范围的唯一执行点——白名单外的工具与违规命令在这里
    转为错误结果文本，绝不会真正执行。

    直连 registry.dispatch（不经主运行时的后台化/确认链），子代理工具
    天然同步完成；超时由 tools.timeout 控制。
    """

    def __init__(self, agent_type: str, allowed_tools: List[str], tools_registry):
        self.agent_type = agent_type
        self.allowed_tools = list(allowed_tools)
        self.registry = tools_registry
        definitions = []
        for tool_name in self.allowed_tools:
            entry = tools_registry.get_entry(tool_name)
            if entry:
                schema = dict(entry.arguments_schema or {})
                if self.agent_type.lower() == "architect" and tool_name == "AgentDelegate":
                    schema = copy.deepcopy(entry.arguments_schema or {})
                    agent_type_schema = schema.get("properties", {}).get("agent_type")
                    if agent_type_schema is not None:
                        agent_type_schema["enum"] = ["explore"]
                definitions.append({
                    "name": entry.name,
                    "description": entry.description,
                    "inputSchema": schema,
                })
        self.last_tool_definitions = definitions
        # 本步已执行调用的全量记录（驱动层用于通知/看板，含完整结果文本——
        # 引擎回调只给 preview，这里保留旧子代理视图的全内容保真度）
        self.last_calls: List[Dict[str, Any]] = []

    def reset_step_calls(self):
        self.last_calls = []

    async def list_tools(self):
        return [d["name"] for d in self.last_tool_definitions]

    def _validate_access(self, tool_name: str, tool_args: dict) -> Optional[str]:
        """校验工具权限，返回 None 表示通过，否则返回错误 JSON"""
        if tool_name not in self.allowed_tools:
            return json.dumps({
                "error": f"工具 '{tool_name}' 不允许在 {self.agent_type} agent 中使用",
                "allowed_tools": self.allowed_tools,
            }, ensure_ascii=False)

        if self.agent_type.lower() == "architect" and tool_name == "AgentDelegate":
            requested_type = tool_args.get("agent_type", "")
            if not isinstance(requested_type, str) or requested_type.lower() != "explore":
                return json.dumps({
                    "error": "Architect 只能委托只读 Explore 子代理",
                    "hint": ("AgentDelegate.agent_type 必须为 'explore'；"
                             "禁止 Plan、Verify、Architect 及递归委托。"),
                    "allowed_agent_types": ["explore"],
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
                cleaned = SubAgentConfig._BENIGN_REDIRECT.sub("", command.lower().strip())
                if self.agent_type.lower() == "architect":
                    patterns = SubAgentConfig.ARCHITECT_FORBIDDEN_PATTERNS
                    hint = ("shell 重定向写文件被禁止——创建文件用 Write，改文件先 Read 再 Edit；"
                            "del/taskkill/reg 等高危命令会弹人工确认，被拒绝请换方案不要重试。")
                else:
                    patterns = SubAgentConfig.FORBIDDEN_PATTERNS
                    hint = ("仅允许只读命令，如 ls、git status/log/diff、find、wc、sort、ps 等；"
                            "可附带 2>/dev/null。读文件请用 Read，搜索用 Grep/Glob。")
                matched = next((p for p in patterns if p in cleaned), None)
                if matched:
                    reason = f"命令包含被禁止的模式 '{matched}'（子代理禁止重定向写入/删除/权限修改）"
                else:
                    reason = f"命令 '{command}' 不在允许的命令范围内"
                return json.dumps({
                    "error": reason,
                    "hint": hint,
                }, ensure_ascii=False)

        return None

    async def call_tool(self, tool_name: str, tool_args: dict) -> str:
        tool_args = tool_args or {}
        access_error = self._validate_access(tool_name, tool_args)
        if access_error is None:
            tool_timeout = config.get("tools.timeout", 60)
            try:
                with timeit(f"subagent.tool.{tool_name}"):
                    result = await asyncio.wait_for(
                        self.registry.dispatch(tool_name, tool_args),
                        timeout=tool_timeout,
                    )
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            except asyncio.TimeoutError:
                result_text = json.dumps(
                    {"error": f"Tool '{tool_name}' timed out after {tool_timeout}s"},
                    ensure_ascii=False)
            except Exception as e:
                result_text = json.dumps(
                    {"error": f"Tool execution failed: {type(e).__name__}: {e}"},
                    ensure_ascii=False)
        else:
            result_text = access_error

        self.last_calls.append({
            "tool": tool_name,
            "args": tool_args,
            "result": result_text,
            "is_error": access_error is not None
                        or ToolCall.classify_is_error(tool_name, result_text),
        })
        return result_text


class _SubWorkingMemory:
    """子代理工作记忆：消息唯一事实源（append / get_context）。
    引擎每步经 document_builder 从这里重放历史；不提供
    retrieve_prompt_memory → 记忆检索天然关闭。"""

    def __init__(self):
        self._messages: List[Dict[str, Any]] = []

    def append(self, message):
        self._messages.append(dict(message))

    def get_context(self):
        return list(self._messages)

    def replace(self, messages):
        """用压缩/截断结论整体替换历史（与旧子循环的收缩语义一致）"""
        self._messages = [dict(m) for m in messages]


class SubAgent:
    """子agent执行器"""

    def __init__(self, agent_type: str, task: str, agent_id: str, tools_registry, output_callback=None, shared_state=None, llm=None):
        """
        初始化子agent

        Args:
            agent_type: agent类型（Explore、Plan等）
            task: 要执行的任务描述
            agent_id: agent唯一标识符
            tools_registry: 工具注册表实例
            output_callback: 输出回调函数
            shared_state: 跨线程共享状态 dict，用于超时后提取最后文本
            llm: 可选注入的 LLM（结构化接口：query(messages, ...) 返回带
                 .content/.stop_reason 的响应；可选 get_last_usage()）。
                 None → Goharu LLMCore 单例
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

        # 初始化LLM（注入优先，否则 Goharu LLMCore 单例）
        self.llm = llm if llm is not None else LargeLanguageModel()

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.token_count = 0
        # 本轮工作记忆（execute() 时创建；_build_step_messages 读它重放历史）
        self._working = None
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

    def _build_step_messages(self, history, tool_definitions=None) -> List[Dict[str, Any]]:
        """子代理消息组装（注入引擎的 document_builder 实现）。

        working[0] = system（恒带断点），working[1] = 任务 user 消息（含
        CRITICAL_REMINDER），其后为引擎累积的 assistant/tool_result 往返。
        发送前消毒配对完整性；断点推进到最后一条消息——历史纯追加，
        前缀逐字节稳定，MiniMax 网关按内容匹配前缀，断点标记移动不影响
        命中（E2E 实测 cache_write=0），verify 等长循环收益最大。
        """
        msgs = sanitize_messages_for_api([dict(m) for m in history])
        managed = self._manage_context(msgs)
        if managed is not msgs:
            # 压缩/截断的结论写回 working（历史本身收缩，与旧子循环一致），
            # 否则超限后每步都会重复触发 LLM 压缩
            self._working.replace(managed)
            msgs = managed
        for m in msgs[1:]:
            m.pop("cache_control", None)
        if len(msgs) > 1:
            msgs[-1]["cache_control"] = {"type": "ephemeral"}
        return msgs

    def _manage_context(self, msgs) -> List[Dict[str, Any]]:
        """Token 预算管理：超限时先 LLM 压缩（可选），失败/未启用则硬截断。
        保留前两条（system + 任务 user）+ 配对安全的近期历史尾部
        （truncate_history 会丢弃截断点后孤立的 tool_result，避免 API 400）。"""
        max_context = self._get_max_context_tokens()
        estimated = TokenEstimator().estimate(str(msgs))
        if estimated < max_context:
            return msgs

        if config.get("agent.context_compact.enabled", False):
            try:
                summary = ContextCompactor(llm=self.llm).compact(msgs)
                if summary and len(summary) >= 20:
                    self._notify(
                        f"  📦 [{self.agent_id}] 上下文压缩 ({len(msgs)} msg → {len(summary)} chars)",
                        "info",
                    )
                    return msgs[:2] + [
                        {"role": "user", "content": f"[上下文摘要]\n\n{summary}"}
                    ]
            except Exception as e:
                self._notify(f"  ⚠ [{self.agent_id}] 压缩失败，回退硬截断: {e}", "warning")

        max_turns = int(config.get("agent_delegate.max_history_turns", 3))
        truncated = msgs[:2] + truncate_history(msgs[2:], max_turns * 2)
        self._notify(
            f"  📐 [{self.agent_id}] 上下文截断: {len(msgs)} → {len(truncated)} msgs",
            "info",
        )
        return truncated

    def _trailing_texts(self, remaining: int, iteration: int) -> List[str]:
        """每轮 tool_result 消息尾部追加的倒计时与关键提醒。
        经引擎 tool_result_trailing_texts 注入缝生效；verify 第 35 轮起
        引导把剩余轮次集中到真实运行测试的动态验证。"""
        max_iterations = self._get_max_iterations()
        if remaining > 0:
            countdown = (
                f"⚠️ 剩余可执行次数: {remaining}/{max_iterations}。"
                f"用完将强制结束。请高效利用剩余机会。"
            )
            if self.agent_type.lower() == "verify" and (iteration + 1) >= 35:
                countdown += (
                    " 即将结束：请把剩余轮次集中到「真实运行测试」的动态代码验证上，"
                    "不要再用轮次读代码或探测环境。"
                )
        else:
            countdown = (
                "⚠️ 最后机会！本次执行后将被强制结束，必须输出文本答案，不能再调用工具。"
            )
        reminder = SubAgentConfig.CRITICAL_REMINDERS.get(self.agent_type.lower())
        return [t for t in (countdown, reminder) if t]

    # ── Tool Execution ──────────────────────────────────────

    def _run_async_in_thread(self, coro, timeout: Optional[int] = 60):
        """在 SubAgent 工作线程中安全执行 async 协程（timeout=None 表示不设上限）"""
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
        return future.result(timeout=timeout + 5 if timeout is not None else None)

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
        执行子agent任务——薄驱动：循环调用与主 Agent 同一套 ActorAgent.act()，
        每轮经注入缝带上子代理差异（工具白名单、提示词、倒计时/提醒）。

        Returns:
            执行结果字典，包含 status / content / error 等字段
        """
        self.start_time = time.time()
        MAX_LLM_RETRIES = 2

        _write_agent_jsonl(self._log_dir, self.agent_id, {
            "event": "start",
            "agent_type": self.agent_type,
            "task": self.task,
            "timestamp": self.start_time,
        })

        try:
            # ── 工作记忆：system + 任务 user（含 CRITICAL_REMINDERS）为前两条 ──
            working = _SubWorkingMemory()
            working.append({
                "role": "system",
                "content": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            })
            task_text = self._build_initial_task_text()
            reminder = SubAgentConfig.CRITICAL_REMINDERS.get(self.agent_type.lower())
            if reminder:
                task_text = reminder + "\n\n" + task_text
            # architect：播种时一次性检索记忆塔并贴入任务消息（首轮注入后即冻结，
            # 前缀跨迭代逐字节稳定，不破坏子代理缓存断点策略）
            if self.agent_type.lower() == "architect":
                from Agent.architect_memory import (
                    retrieve_architect_memory, format_experience_block,
                )
                pack = retrieve_architect_memory([{"role": "user", "content": task_text}])
                task_text = format_experience_block(pack) + task_text
            working.append({"role": "user", "content": task_text})
            self._working = working

            # ── 引擎与权力范围：差异全部经注入，循环代码只有一份 ──
            runtime = _SubToolRuntime(self.agent_type, self.allowed_tools, self.tools_registry)
            if not runtime.last_tool_definitions:
                raise ValueError(
                    "SubAgent requires at least one allowed tool present in the registry"
                )
            engine = ActorAgent(
                runtime, working, llm=self.llm,
                document_builder=self._build_step_messages,
                verify_gate_enabled=False,
            )

            max_iterations = self._get_max_iterations()
            final_answer = None
            iteration = 0

            for iteration in range(max_iterations):
                self._notify(f"  🔄 [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info",
                             category="iteration",
                             content=f"{iteration + 1}/{max_iterations}")
                remaining = max_iterations - (iteration + 1)
                runtime.reset_step_calls()

                # 本步回调累积器（JSONL preview / last_text_answer 用）
                step_text: List[str] = []
                step_thinking: List[str] = []

                def _on_thinking(text):
                    step_thinking.append(text)

                def _on_text(text):
                    step_text.append(text)

                def _on_tool_call_start(tool_name, args, *rest):
                    args_str = json.dumps(args, ensure_ascii=False)[:100]
                    self._notify(
                        f"  🔧 [{self.agent_id}] 调用工具: {tool_name}({args_str}...)", "info",
                        category="tool_call", tool=tool_name,
                        content=json.dumps(args, ensure_ascii=False)[:2000],
                    )

                def _on_retry(attempt, mx, backoff, phase, *rest):
                    self._notify(
                        f"  ⚠ [{self.agent_id}] LLM重试 ({attempt}/{mx}, {phase}): {backoff}s 后重试",
                        "warning",
                    )

                result = self._run_async_in_thread(
                    engine.act(
                        max_retries=MAX_LLM_RETRIES,
                        on_tool_call_start=_on_tool_call_start,
                        on_thinking=_on_thinking,
                        on_text=_on_text,
                        on_retry=_on_retry,
                        tool_result_trailing_texts=lambda: self._trailing_texts(remaining, iteration),
                    ),
                    timeout=None,
                )

                # 用量与缓存统计（口径与主代理 token.stats 一致；
                # 注入路径 usage 可能是 SDK 对象，这里归一成 dict）
                usage = result.get("usage")
                if usage is not None and not isinstance(usage, dict):
                    usage = {
                        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    }
                if usage:
                    self.last_usage = usage
                    self.token_count += (
                        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    )
                    self._emit_token_stats()

                # 工具调用记账：通知（全量结果）+ tool_calls 列表 + 看板
                step_calls = runtime.last_calls
                for tr in step_calls:
                    result_preview = tr["result"][:150] + "..." if len(tr["result"]) > 150 else tr["result"]
                    self._notify(
                        f"  {'⚠' if tr['is_error'] else '✓'} [{self.agent_id}] 工具结果: {result_preview}", "debug",
                        category="tool_result", tool=tr["tool"],
                        content=tr["result"][:4000], is_error=tr["is_error"],
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

                text_preview = "\n".join(step_text).strip() or (result.get("answer") or "")
                thinking_preview = "\n".join(step_thinking).strip() or (result.get("thinking") or "")
                if text_preview or thinking_preview:
                    self.last_text_answer = text_preview or thinking_preview
                    self.shared_state["last_text_answer"] = self.last_text_answer
                self._notify(
                    f"  💭 [{self.agent_id}] 思考: "
                    + ((text_preview or thinking_preview or "(no text)")[:200]),
                    "debug",
                    category="thinking",
                    content=(text_preview or thinking_preview)[:4000],
                )
                _write_agent_jsonl(self._log_dir, self.agent_id, {
                    "event": "iteration", "iteration": iteration + 1, "native": True,
                    "text_preview": (text_preview[:200] or None),
                    "thinking_preview": (thinking_preview[:200] or None),
                    "tool_use_count": len(step_calls),
                    "tool_names": [tr["tool"] for tr in step_calls],
                    "timestamp": time.time(),
                })

                rtype = result.get("type")
                if rtype == "answer":
                    self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info",
                                 category="final", content=(result.get("answer") or "")[:8000])
                    final_answer = result.get("answer") or ""
                    break
                if rtype in ("tool", "tool_batch", "tool_backgrounded", "continue"):
                    continue
                if rtype == "interrupted":
                    break
                # error：引擎已优雅返回错误文本（重试耗尽/空响应），不向上抛
                self._notify(f"  ❌ [{self.agent_id}] LLM 调用失败: {result.get('error')}", "error",
                             category="error")
                fallback = self._scan_messages_for_last_text(working.get_context()) or ""
                return self._build_result(
                    "error", fallback, iteration,
                    error=f"LLM call failed: {result.get('error')}",
                )

            # ══════ 循环结束：构建结果 ══════
            if final_answer is None:
                self._notify(f"  ⚠️ [{self.agent_id}] 达到最大迭代次数", "warning")
                final_answer = (
                    self.last_text_answer
                    or self._scan_messages_for_last_text(working.get_context())
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
        finally:
            # architect：transcript 归档进记忆塔（L0→L1→L2）。归档含多次 LLM
            # 调用（数十秒级），放守护线程执行，不阻塞结果返回主代理；
            # 归档锁保证并发串行（Agent/architect_memory.py 内全 try/except）
            if self.agent_type.lower() == "architect":
                _wm = getattr(self, "_working", None)
                if _wm is not None:
                    import threading as _threading
                    from Agent.architect_memory import archive_transcript
                    _snapshot = _wm.get_context()
                    _threading.Thread(
                        target=archive_transcript,
                        args=(_snapshot,),
                        daemon=True,
                        name=f"architect-archive-{self.agent_id}",
                    ).start()
