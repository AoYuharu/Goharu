"""
SubAgent - 子agent执行模块

负责执行特定类型的子任务（Explore、Plan等），具有受限的工具权限和独立的执行上下文。
支持 Anthropic 原生 tool_use/tool_result 调用格式。
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

from Agent.ContextCompactor import ContextCompactor
from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.TokenEstimator import TokenEstimator
from Memory.ToolCall import ToolCall
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

    # Explore agent的系统提示词
    EXPLORE_SYSTEM_PROMPT = """你是文件搜索专家，你擅长彻底导航和探索代码库。

=== 关键：只读模式 - 禁止文件修改 ===
这是一个只读探索任务。你被严格禁止：
- 创建新文件（禁止 Write、touch 或任何形式的文件创建）
- 修改现有文件（禁止 Edit 操作）
- 删除文件（禁止 rm 或删除）
- 移动或复制文件（禁止 mv 或 cp）
- 在任何地方创建临时文件，包括 /tmp
- 使用重定向操作符（>、>>、|）或 heredocs 写入文件
- 运行任何改变系统状态的命令

你的角色专门用于搜索和分析现有代码。你无法访问文件编辑工具 - 尝试编辑文件将失败。

## 工作流程（ReAct循环）

你应该遵循"思考→行动→观察→思考"的循环：

1. **思考**：分析当前任务，决定下一步要做什么
2. **行动**：调用工具（Glob、Grep、Read、run_cmd）获取信息
3. **观察**：查看工具返回的结果
4. **重复**：根据结果继续思考和行动，直到收集到足够信息

## 可用工具

- **Glob**：文件模式匹配，查找符合模式的文件
- **Grep**：搜索文件内容，支持正则表达式
- **Read**：读取文件内容，深入分析代码
- **run_cmd**：执行只读命令（ls、git status、git log、git diff等）

## 重要指南

- **持续探索**：不要在第一次工具调用后就停止，继续深入分析
- **使用Read**：找到相关文件后，使用Read读取内容进行分析
- **给出结论**：完成探索后，给出清晰的最终报告，而不是仅仅列出工具调用
- **高效执行**：尽可能在8轮内完成任务

## 最终输出要求

当你完成探索后，给出一个**清晰的最终报告**，包括：
- 发现了什么文件/代码
- 这些代码的功能和作用
- 关键的实现细节
- 回答用户的问题

**不要**只输出工具调用，**必须**给出分析结论。"""

    # Plan agent的系统提示词
    PLAN_SYSTEM_PROMPT = """你是软件架构师和规划专家。你的角色是探索代码库并设计实现计划。

=== 关键：只读模式 - 禁止文件修改 ===
这是一个只读规划任务。你被严格禁止：
- 创建新文件（禁止 Write、touch 或任何形式的文件创建）
- 修改现有文件（禁止 Edit 操作）
- 删除文件（禁止 rm 或删除）
- 移动或复制文件（禁止 mv 或 cp）
- 在任何地方创建临时文件，包括 /tmp
- 使用重定向操作符（>、>>、|）或 heredocs 写入文件
- 运行任何改变系统状态的命令

你的角色专门用于探索代码库和设计实现计划。你无法访问文件编辑工具 - 尝试编辑文件将失败。

## 工作流程（ReAct循环）

1. **理解需求**：分析用户的需求
2. **探索代码库**：使用工具（Glob、Grep、Read）理解现有架构
3. **设计方案**：基于探索结果设计实现方案
4. **细化计划**：提供详细的实现步骤

## 可用工具

- **Glob**：查找相关文件
- **Grep**：搜索现有实现模式
- **Read**：读取关键文件，理解架构
- **run_cmd**：执行只读命令（git log、git diff等）

## 重要指南

- **深入探索**：不要浅尝辄止，要深入理解现有代码
- **使用Read**：找到关键文件后，读取内容理解实现细节
- **给出完整计划**：最终输出应该是一个可执行的实现计划
- **高效执行**：尽可能在8轮内完成

## 必需输出

完成探索和设计后，给出**完整的实现计划**，包括：

1. **现状分析**：当前架构和相关模块
2. **设计方案**：如何实现需求
3. **实现步骤**：详细的步骤和顺序
4. **关键文件**：需要修改/创建的文件列表

### 实现的关键文件
列出实现此计划最关键的 3-5 个文件：
- path/to/file1.py
- path/to/file2.py
- path/to/file3.py

记住：你只能探索和规划。你不能也不得写入、编辑或修改任何文件。"""

    # 只读命令白名单（Linux + Windows 兼容）
    READONLY_COMMANDS = {
        # Linux / macOS
        'ls', 'pwd', 'cd', 'cat', 'head', 'tail', 'less', 'more',
        'find', 'tree', 'grep', 'awk', 'sed -n', 'wc', 'sort',
        # Windows (只读)
        'dir', 'type', 'where', 'set', 'ver', 'echo', 'date', 'time',
        'findstr', 'tasklist', 'systeminfo', 'whoami', 'hostname',
        # 脚本/工具 (只读子命令)
        'python', 'pip', 'powershell', 'cmd',
        # Git
        'git status', 'git log', 'git diff', 'git show',
        'git branch', 'git remote', 'git stash list', 'git tag',
        'git rev-parse', 'git config --list', 'git blame', 'git whatchanged',
        'git shortlog', 'git describe', 'git ls-files', 'git ls-tree',
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
        """获取指定类型agent的系统提示词"""
        if agent_type.lower() == "explore":
            return cls.EXPLORE_SYSTEM_PROMPT
        elif agent_type.lower() == "plan":
            return cls.PLAN_SYSTEM_PROMPT
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

    @classmethod
    def get_allowed_tools(cls, agent_type: str) -> List[str]:
        """获取指定类型agent允许使用的工具"""
        if agent_type.lower() in ["explore", "plan"]:
            return ["Read", "Grep", "Glob", "run_cmd"]
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

    @classmethod
    def is_command_allowed(cls, command: str) -> bool:
        """检查命令是否在只读白名单中"""
        command_lower = command.lower().strip()

        # 首先检查是否包含禁止的模式
        for forbidden in cls.FORBIDDEN_PATTERNS:
            if forbidden in command_lower:
                return False

        # 检查是否以白名单中的命令开头
        for allowed_cmd in cls.READONLY_COMMANDS:
            if command_lower.startswith(allowed_cmd):
                return True

        return False


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

        # 判断是否使用 Anthropic 原生工具调用
        llm_config = config.get("model.large-language-model", {}) or {}
        self.provider = llm_config.get("provider", "local_hf")
        self.use_native_tools = llm_config.get("use_native_tools", True)

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.token_count = 0
        self.tool_calls = []

        # JSONL 日志
        self._log_dir = Path("runtime_memory/logs/agents/sub")

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

    def _build_tools_schema(self) -> str:
        """构建工具schema描述（文本格式，用于 local_hf 回退）"""
        tools_desc = []

        for tool_name in self.allowed_tools:
            tool_entry = self.tools_registry.get_entry(tool_name)
            if tool_entry:
                tools_desc.append(f"## {tool_name}")
                tools_desc.append(f"描述: {tool_entry.description}")

                schema = tool_entry.arguments_schema
                if schema:
                    tools_desc.append(f"参数: {json.dumps(schema, ensure_ascii=False, indent=2)}")

                tools_desc.append("")

        return "\n".join(tools_desc)

    def _build_prompt_messages(self) -> List[Dict[str, Any]]:
        """构建提示消息，支持PromptCaching"""
        messages = []

        # 系统提示词（Level 1缓存 - 总是缓存）
        system_message = {
            "role": "system",
            "content": self.system_prompt,
            "cache_control": {"type": "ephemeral"}
        }
        messages.append(system_message)

        # 工具schema（Level 1缓存 - 总是缓存）
        # 对于 Anthropic 原生工具，这仅作为文本回退；工具定义通过 API tools 参数传递
        tools_schema = self._build_tools_schema()
        tools_message = {
            "role": "system",
            "content": f"# 可用工具\n\n{tools_schema}",
            "cache_control": {"type": "ephemeral"}
        }
        messages.append(tools_message)

        # 用户任务（不缓存 - 每次都不同）
        max_iter = config.get("agent_delegate.max_iterations", 8)
        task_message = {
            "role": "user",
            "content": (
                f"⚠️ 这是你的第1次执行。你最多有{max_iter}次工具调用机会，每次机会都很宝贵。\n"
                f"请高效规划：优先使用并行工具调用，专注于最关键的信息收集。\n\n"
                f"任务:\n{self.task}"
            )
        }
        messages.append(task_message)

        return messages

    def _compact_messages(self, messages, system_messages_count=2):
        """上下文超限时自动摘要，失败时回退到硬截断"""
        if not config.get("agent.context_compact.enabled", False):
            return self._hard_truncate(messages, system_messages_count)

        threshold = int(config.get("agent.context_compact.threshold_tokens", 80000))
        estimator = TokenEstimator()
        estimated = estimator.estimate(str(messages))

        if estimated < threshold:
            return messages

        try:
            compactor = ContextCompactor()
            summary = compactor.compact(messages)
            if not summary or len(summary) < 20:
                raise ValueError("Compaction empty")
            self._notify(f"  📦 [{self.agent_id}] 上下文已自动压缩 ({len(messages)} msg → {len(summary)} chars)", "info")
            return messages[:system_messages_count] + [
                {"role": "user", "content": f"[自动上下文摘要]\n\n{summary}"}
            ]
        except Exception as e:
            self._notify(f"  ⚠ [{self.agent_id}] 压缩失败，回退到硬截断: {e}", "warning")
            return self._hard_truncate(messages, system_messages_count)

    def _hard_truncate(self, messages, system_messages_count=2):
        """硬截断：保留 system 消息 + 最后 N 对完整轮次"""
        max_turns = int(config.get("agent_delegate.max_history_turns", 3))
        keep = system_messages_count + max_turns * 2
        truncated = messages[:system_messages_count] + messages[-keep + system_messages_count:]
        if len(truncated) > system_messages_count and truncated[system_messages_count].get("role") == "assistant":
            offset = max_turns * 2 + 1
            truncated = messages[:system_messages_count] + messages[-(offset):]
        return truncated

    def _should_use_native_tools(self) -> bool:
        """判断是否应该使用 Anthropic 原生工具调用"""
        return (
            self.provider == "anthropic_compatible"
            and self.use_native_tools
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

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        执行工具调用

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            工具执行结果
        """
        # 检查工具是否允许
        if tool_name not in self.allowed_tools:
            return json.dumps({
                "error": f"工具 {tool_name} 不允许在 {self.agent_type} agent中使用",
                "allowed_tools": self.allowed_tools
            }, ensure_ascii=False)

        # 特殊处理run_cmd - 检查命令白名单
        if tool_name == "run_cmd":
            command = tool_args.get("cmd", "")
            if not SubAgentConfig.is_command_allowed(command):
                return json.dumps({
                    "error": f"命令 '{command}' 不在只读白名单中",
                    "hint": "只允许使用只读命令，如: ls, cat, git status, git log, git diff 等"
                }, ensure_ascii=False)

        # 获取工具并执行
        tool_entry = self.tools_registry.get_entry(tool_name)
        if not tool_entry:
            return json.dumps({
                "error": f"工具 {tool_name} 未找到"
            }, ensure_ascii=False)

        try:
            handler = tool_entry.handler
            if not handler:
                return json.dumps({
                    "error": f"工具 {tool_name} 没有处理函数"
                }, ensure_ascii=False)

            # 记录工具调用
            self.tool_calls.append({
                "tool": tool_name,
                "args": tool_args,
                "timestamp": time.time()
            })

            # 获取超时时间（默认60s，子agent自身无超时）
            timeout = config.get("tools.timeout", 60)
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                timeout = None

            import asyncio
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

            # 执行工具（同步或异步，带超时）
            if asyncio.iscoroutinefunction(handler):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    if timeout:
                        result = loop.run_until_complete(
                            asyncio.wait_for(handler(**tool_args), timeout=timeout)
                        )
                    else:
                        result = loop.run_until_complete(handler(**tool_args))
                finally:
                    loop.close()
            else:
                if timeout:
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(handler, **tool_args)
                        try:
                            result = future.result(timeout=timeout)
                        except FutureTimeoutError:
                            return json.dumps({
                                "error": f"Tool '{tool_name}' timed out after {timeout}s"
                            }, ensure_ascii=False)
                else:
                    result = handler(**tool_args)

            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        except asyncio.TimeoutError:
            return json.dumps({
                "error": f"Tool '{tool_name}' timed out after {timeout}s"
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": f"工具执行失败: {str(e)}"
            }, ensure_ascii=False)

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLM响应中解析工具调用（文本格式，仅用于 local_hf 回退）

        支持多种格式：
        1. {"tool": "tool_name", "args": {...}}
        2. {"tool": "tool_name", "arguments": {...}}
        3. {tool => "tool_name", args => {...}}
        """
        tool_calls = []

        try:
            import re

            # 格式1和2: 标准JSON格式
            json_pattern = r'\{[^{}]*"tool"[^{}]*\}'
            matches = re.findall(json_pattern, response, re.DOTALL)

            for match in matches:
                try:
                    tool_call = json.loads(match)
                    if "tool" in tool_call:
                        args = tool_call.get("args") or tool_call.get("arguments") or {}
                        tool_calls.append({
                            "tool": tool_call["tool"],
                            "args": args
                        })
                except json.JSONDecodeError:
                    continue

            # 格式3: {tool => "name", args => {...}} 格式
            arrow_pattern = r'\{tool\s*=>\s*"([^"]+)"[^}]*args\s*=>\s*(\{[^}]+\})\}'
            arrow_matches = re.findall(arrow_pattern, response, re.DOTALL)

            for tool_name, args_str in arrow_matches:
                try:
                    args = {}
                    arg_pattern = r'--(\w+)\s+"([^"]+)"'
                    arg_matches = re.findall(arg_pattern, args_str)
                    for key, value in arg_matches:
                        args[key] = value

                    if not args:
                        args = json.loads(args_str)

                    tool_calls.append({
                        "tool": tool_name,
                        "args": args
                    })
                except Exception:
                    continue

        except Exception:
            pass

        return tool_calls

    def _scan_messages_for_last_text(self, messages: List[Dict], use_native: bool) -> Optional[str]:
        """扫描消息列表，返回最后一条 assistant 的 text/thinking 内容（排除纯 tool_use 消息）"""
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if use_native and isinstance(content, list):
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
        执行子agent任务（多轮ReAct循环）

        Returns:
            执行结果字典，包含status, content, error等字段
        """
        self.start_time = time.time()

        # JSONL start 事件
        _write_agent_jsonl(self._log_dir, self.agent_id, {
            "event": "start",
            "agent_type": self.agent_type,
            "task": self.task,
            "timestamp": self.start_time,
        })

        try:
            # 构建初始提示消息
            messages = self._build_prompt_messages()

            # 最大循环次数（防止无限循环）
            max_iterations = config.get("agent_delegate.max_iterations", 8)

            # 保留的历史轮数（避免上下文过长）
            max_history_turns = config.get("agent_delegate.max_history_turns", 3)

            # 判断是否使用 Anthropic 原生工具调用
            use_native = self._should_use_native_tools()
            anthropic_tools = self._build_anthropic_tools() if use_native else None

            final_answer = None
            is_first_iteration = True

            # ReAct循环
            for iteration in range(max_iterations):
                # 通知当前迭代
                self._notify(f"  🔄 [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info")

                # 调用LLM（原生工具模式传递 tools 参数）
                if use_native:
                    response = self.llm.query(messages, tools=anthropic_tools)
                else:
                    response = self.llm.query(messages)

                # 累加token数量
                response_text_for_count = str(response) if not isinstance(response, str) else response
                self.token_count += self.llm.getTokenSize(str(messages[-1:]) + response_text_for_count)

                # JSONL 迭代事件
                if use_native and self._is_native_response(response):
                    native_text = self._extract_native_text(response)
                    native_thinking = self._extract_native_thinking(response)
                    native_tc = self._parse_native_tool_calls(response)
                    _write_agent_jsonl(self._log_dir, self.agent_id, {
                        "event": "iteration",
                        "iteration": iteration + 1,
                        "native": True,
                        "text_preview": (native_text[:200] if native_text else None),
                        "thinking_preview": (native_thinking[:200] if native_thinking else None),
                        "tool_use_count": len(native_tc),
                        "tool_names": [tc["tool"] for tc in native_tc],
                        "timestamp": time.time(),
                    })
                else:
                    response_text = response if isinstance(response, str) else str(response)
                    tool_calls = self._parse_tool_calls(response_text)
                    _write_agent_jsonl(self._log_dir, self.agent_id, {
                        "event": "iteration",
                        "iteration": iteration + 1,
                        "native": False,
                        "text_preview": response_text[:200],
                        "tool_use_count": len(tool_calls),
                        "tool_names": [tc.get("tool") for tc in tool_calls],
                        "timestamp": time.time(),
                    })

                # 处理 Anthropic 原生响应格式
                if use_native and self._is_native_response(response):
                    tool_use_blocks = self._parse_native_tool_calls(response)
                    text_content = self._extract_native_text(response)
                    thinking_content = self._extract_native_thinking(response)

                    # 通知思考内容
                    preview = text_content[:200] if text_content else (
                        thinking_content[:200] if thinking_content else "(no text)"
                    )
                    self._notify(f"  💭 [{self.agent_id}] 思考: {preview}", "debug")

                    # 持久化最后一条文本/思考（用于超时/超轮后回传给主agent）
                    if text_content or thinking_content:
                        self.last_text_answer = text_content or thinking_content
                        self.shared_state["last_text_answer"] = self.last_text_answer

                    # 仅 thinking 块，无文本无工具 → 模型还在思考，继续循环
                    if thinking_content and not text_content and not tool_use_blocks:
                        messages.append({"role": "assistant", "content": thinking_content.strip()})
                        continue

                    if tool_use_blocks:
                        # 构建 assistant 消息（含 tool_use 块）
                        assistant_content = []
                        if text_content:
                            assistant_content.append({"type": "text", "text": text_content})
                        for tb in tool_use_blocks:
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tb["id"],
                                "name": tb["tool"],
                                "input": tb["args"],
                            })
                        messages.append({"role": "assistant", "content": assistant_content})

                        # 执行所有工具，构建 tool_result 块
                        remaining = max_iterations - (iteration + 1)
                        user_content = [
                            {"type": "text", "text": (
                                f"⚠️ 剩余可执行次数: {remaining}/{max_iterations}。"
                                f"用完将强制结束。请高效利用剩余机会。" if remaining > 0 else
                                f"⚠️ 最后机会！本次执行后将被强制结束，必须输出文本答案，不能再调用工具。"
                            )},
                        ]
                        for tb in tool_use_blocks:
                            args_str = json.dumps(tb["args"], ensure_ascii=False)[:100]
                            self._notify(f"  🔧 [{self.agent_id}] 调用工具: {tb['tool']}({args_str}...)", "info")

                            result = self._execute_tool(tb["tool"], tb["args"])

                            result_preview = result[:150] + "..." if len(result) > 150 else result
                            self._notify(f"  ✓ [{self.agent_id}] 工具结果: {result_preview}", "debug")

                            # 正确设置 is_error（与 ActorAgent 一致）
                            error_flag = False
                            if isinstance(result, str):
                                try:
                                    rj = json.loads(result)
                                    error_flag = "error" in rj and rj.get("error")
                                except (json.JSONDecodeError, TypeError):
                                    pass

                            user_content.append(
                                ToolCall.create_anthropic_tool_result(tb["id"], result, is_error=error_flag)
                            )

                            # 记录工具调用
                            self.tool_calls.append({
                                "tool": tb["tool"],
                                "args": tb["args"],
                                "result_preview": result_preview,
                                "is_error": error_flag,
                                "timestamp": time.time(),
                            })

                        messages.append({"role": "user", "content": user_content})

                        # 上下文超限时自动摘要（替代硬截断）
                        if len(messages) > 4 + max_history_turns * 2:
                            messages = self._compact_messages(messages)

                        continue
                    else:
                        # 无工具调用 → 最终答案
                        self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info")
                        final_answer = text_content or response_text_for_count
                        break

                else:
                    # 文本格式（local_hf 回退）- 原有逻辑
                    thinking_preview = response[:200] + "..." if len(response) > 200 else response
                    self._notify(f"  💭 [{self.agent_id}] 思考: {thinking_preview}", "debug")

                    # 持久化最后一条文本（用于超时/超轮后回传给主agent）
                    if isinstance(response, str) and response.strip():
                        self.last_text_answer = response.strip()
                        self.shared_state["last_text_answer"] = self.last_text_answer

                    tool_calls = self._parse_tool_calls(response)

                    if tool_calls:
                        messages.append({
                            "role": "assistant",
                            "content": response
                        })

                        remaining = max_iterations - (iteration + 1)
                        tool_results_text = [
                            f"⚠️ 剩余可执行次数: {remaining}/{max_iterations}。用完将强制结束。请高效利用剩余机会。"
                        ]
                        for tool_call in tool_calls:
                            tool_name = tool_call.get("tool")
                            tool_args = tool_call.get("args", {})

                            args_str = json.dumps(tool_args, ensure_ascii=False)[:100]
                            self._notify(f"  🔧 [{self.agent_id}] 调用工具: {tool_name}({args_str}...)", "info")

                            result = self._execute_tool(tool_name, tool_args)

                            result_preview = result[:150] + "..." if len(result) > 150 else result
                            self._notify(f"  ✓ [{self.agent_id}] 工具结果: {result_preview}", "debug")

                            tool_results_text.append(f"### {tool_name}\n{result}")

                            # 记录工具调用
                            self.tool_calls.append({
                                "tool": tool_name,
                                "args": tool_args,
                                "result_preview": result_preview,
                                "timestamp": time.time(),
                            })

                        messages.append({
                            "role": "user",
                            "content": "\n\n".join(tool_results_text)
                        })

                        if len(messages) > 4 + max_history_turns * 2:
                            messages = self._compact_messages(messages)

                        continue
                    else:
                        self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info")
                        final_answer = response
                        break

            # 如果达到最大迭代次数仍未给出最终答案，返回最后一条文本/思考内容
            if final_answer is None:
                self._notify(f"  ⚠️ [{self.agent_id}] 达到最大迭代次数，提取最后文本内容", "warning")
                if self.last_text_answer:
                    final_answer = self.last_text_answer
                else:
                    # 兜底：扫描 messages 中的最后一条 assistant text/thinking
                    final_answer = self._scan_messages_for_last_text(messages, use_native)
                if not final_answer:
                    final_answer = f"子Agent [{self.agent_id}] 在 {max_iterations} 轮内未能生成文本答案（已执行 {len(self.tool_calls)} 次工具调用）"

            self.end_time = time.time()

            # JSONL end 事件
            _write_agent_jsonl(self._log_dir, self.agent_id, {
                "event": "end",
                "status": "success",
                "iterations": iteration + 1,
                "tool_calls_count": len(self.tool_calls),
                "token_count": self.token_count,
                "duration_ms": int((self.end_time - self.start_time) * 1000),
                "timestamp": self.end_time,
            })

            return {
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "status": "success",
                "content": final_answer,
                "token_count": self.token_count,
                "duration_ms": int((self.end_time - self.start_time) * 1000),
                "tool_calls_count": len(self.tool_calls),
                "iterations": iteration + 1
            }

        except Exception as e:
            self.end_time = time.time()
            self._notify(f"  ❌ [{self.agent_id}] 执行失败: {str(e)}", "error")

            # JSONL end 事件（错误）
            _write_agent_jsonl(self._log_dir, self.agent_id, {
                "event": "end",
                "status": "error",
                "error": str(e),
                "tool_calls_count": len(self.tool_calls),
                "token_count": self.token_count,
                "duration_ms": int((self.end_time - self.start_time) * 1000) if self.end_time else 0,
                "timestamp": self.end_time,
            })

            return {
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "status": "error",
                "content": "",
                "error": str(e),
                "token_count": self.token_count,
                "duration_ms": int((self.end_time - self.start_time) * 1000) if self.end_time else 0,
                "tool_calls_count": len(self.tool_calls)
            }
