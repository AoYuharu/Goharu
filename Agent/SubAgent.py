"""
SubAgent - 子agent执行模块

负责执行特定类型的子任务（Explore、Plan等），具有受限的工具权限和独立的执行上下文。
"""

import json
import time
from typing import Dict, List, Any, Optional

from Agent.LargeLanguageModel import LargeLanguageModel
from configurationLoader import config


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

    # 只读命令白名单
    READONLY_COMMANDS = {
        'ls', 'dir', 'pwd', 'cd', 'cat', 'head', 'tail', 'less', 'more',
        'find', 'tree', 'git status', 'git log', 'git diff', 'git show',
        'git branch', 'git remote', 'grep', 'awk', 'sed -n', 'wc'
    }

    # 禁止的命令模式（包含重定向、文件写入等）
    FORBIDDEN_PATTERNS = [
        '>', '>>', '<<', 'rm ', 'del ', 'mkdir', 'touch',
        'mv ', 'move ', 'cp ', 'copy ', 'chmod', 'chown'
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

    def __init__(self, agent_type: str, task: str, agent_id: str, tools_registry, output_callback=None):
        """
        初始化子agent

        Args:
            agent_type: agent类型（Explore、Plan等）
            task: 要执行的任务描述
            agent_id: agent唯一标识符
            tools_registry: 工具注册表实例
            output_callback: 输出回调函数
        """
        self.agent_type = agent_type
        self.task = task
        self.agent_id = agent_id
        self.tools_registry = tools_registry
        self.output_callback = output_callback

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
        self.tool_calls = []

    def _notify(self, message: str, level: str = "info"):
        """通知输出消息"""
        if self.output_callback:
            self.output_callback(message, level)

    def _build_tools_schema(self) -> str:
        """构建工具schema描述"""
        tools_desc = []

        for tool_name in self.allowed_tools:
            tool_entry = self.tools_registry.get_entry(tool_name)
            if tool_entry:
                tools_desc.append(f"## {tool_name}")
                tools_desc.append(f"描述: {tool_entry.description}")

                # 添加参数schema
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
            "cache_control": {"type": "ephemeral"}  # 标记为可缓存
        }
        messages.append(system_message)

        # 工具schema（Level 1缓存 - 总是缓存）
        tools_schema = self._build_tools_schema()
        tools_message = {
            "role": "system",
            "content": f"# 可用工具\n\n{tools_schema}",
            "cache_control": {"type": "ephemeral"}  # 标记为可缓存
        }
        messages.append(tools_message)

        # 用户任务（不缓存 - 每次都不同）
        task_message = {
            "role": "user",
            "content": self.task
        }
        messages.append(task_message)

        return messages

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
            command = tool_args.get("command", "")
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

            # 执行工具（同步调用）
            import asyncio
            if asyncio.iscoroutinefunction(handler):
                # 如果是异步函数，在新的事件循环中运行
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(handler(**tool_args))
                finally:
                    loop.close()
            else:
                result = handler(**tool_args)

            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({
                "error": f"工具执行失败: {str(e)}"
            }, ensure_ascii=False)

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLM响应中解析工具调用

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
                        # 统一参数字段名
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
                    # 尝试解析args部分
                    # 将 --key "value" 格式转换为 JSON
                    args = {}
                    arg_pattern = r'--(\w+)\s+"([^"]+)"'
                    arg_matches = re.findall(arg_pattern, args_str)
                    for key, value in arg_matches:
                        args[key] = value

                    if not args:
                        # 如果没有匹配到--格式，尝试直接解析JSON
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

    def execute(self) -> Dict[str, Any]:
        """
        执行子agent任务（多轮ReAct循环）

        Returns:
            执行结果字典，包含status, content, error等字段
        """
        self.start_time = time.time()

        try:
            # 构建初始提示消息
            messages = self._build_prompt_messages()

            # 最大循环次数（防止无限循环）
            max_iterations = config.get("agent_delegate.max_iterations", 8)

            # 保留的历史轮数（避免上下文过长）
            max_history_turns = config.get("agent_delegate.max_history_turns", 3)

            final_answer = None

            # ReAct循环
            for iteration in range(max_iterations):
                # 通知当前迭代
                self._notify(f"  🔄 [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info")

                # 调用LLM
                response = self.llm.query(messages)

                # 累加token数量
                self.token_count += self.llm.getTokenSize(str(messages[-1:]) + response)

                # 显示思考过程（截断显示）
                thinking_preview = response[:200] + "..." if len(response) > 200 else response
                self._notify(f"  💭 [{self.agent_id}] 思考: {thinking_preview}", "debug")

                # 解析工具调用
                tool_calls = self._parse_tool_calls(response)

                if tool_calls:
                    # 有工具调用 - 执行工具并继续循环
                    # 将assistant的响应加入消息历史
                    messages.append({
                        "role": "assistant",
                        "content": response
                    })

                    # 执行所有工具调用
                    tool_results_text = []
                    for tool_call in tool_calls:
                        tool_name = tool_call.get("tool")
                        tool_args = tool_call.get("args", {})

                        # 通知工具调用
                        args_str = json.dumps(tool_args, ensure_ascii=False)[:100]
                        self._notify(f"  🔧 [{self.agent_id}] 调用工具: {tool_name}({args_str}...)", "info")

                        result = self._execute_tool(tool_name, tool_args)

                        # 显示工具结果预览
                        result_preview = result[:150] + "..." if len(result) > 150 else result
                        self._notify(f"  ✓ [{self.agent_id}] 工具结果: {result_preview}", "debug")

                        tool_results_text.append(f"### {tool_name}\n{result}")

                    # 将工具结果作为user消息加入历史
                    messages.append({
                        "role": "user",
                        "content": "## 工具执行结果\n\n" + "\n\n".join(tool_results_text)
                    })

                    # 保持上下文窗口在合理范围内
                    # 保留：系统提示词(2条) + 最近N轮对话
                    if len(messages) > 2 + max_history_turns * 2:
                        # 保留前2条（系统提示词和工具schema）+ 最近N轮
                        messages = messages[:2] + messages[-(max_history_turns * 2):]

                    # 继续下一轮循环
                    continue
                else:
                    # 没有工具调用 - 这是最终答案
                    self._notify(f"  ✅ [{self.agent_id}] 得出最终结论", "info")
                    final_answer = response
                    break

            # 如果达到最大迭代次数仍未给出最终答案
            if final_answer is None:
                self._notify(f"  ⚠️ [{self.agent_id}] 达到最大迭代次数", "warning")
                final_answer = f"[达到最大迭代次数 {max_iterations}]\n\n" + response

            self.end_time = time.time()

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
