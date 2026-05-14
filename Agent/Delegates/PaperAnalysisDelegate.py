"""
PaperAnalysisDelegate - 论文分析子智能体基类

基于 SubAgent 模式，提供 7 个专门的论文分析子智能体。
"""

import json
import time
from typing import Dict, List, Any, Optional
from pathlib import Path

from Agent.LargeLanguageModel import LargeLanguageModel
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


class PaperAnalysisDelegateConfig:
    """论文分析子智能体配置类"""

    @staticmethod
    def _load_prompt(filename: str) -> str:
        """从文件加载提示词"""
        prompt_path = Path(__file__).parent.parent.parent / "prompts" / "paper_analysis" / filename
        try:
            return prompt_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise FileNotFoundError(f"提示词文件未找到: {prompt_path}")


    @classmethod
    def get_system_prompt(cls, agent_type: str) -> str:
        """获取指定类型 agent 的系统提示词"""
        prompt_files = {
            "pdf_parser": "pdf_parser.md",
            "content_analysis": "content_analysis.md",
            "tech_extraction": "tech_extraction.md",
            "fake_data_reproduction": "fake_data_reproduction.md",
            "literature_analysis": "literature_analysis.md",
            "relation_analysis": "relation_analysis.md",
            "knowledge_integration": "knowledge_integration.md",
        }

        if agent_type.lower() not in prompt_files:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return cls._load_prompt(prompt_files[agent_type.lower()])

    @classmethod
    def get_allowed_tools(cls, agent_type: str) -> List[str]:
        """获取指定类型 agent 允许使用的工具"""
        tools_map = {
            "pdf_parser": ["parse_pdf"],
            "content_analysis": ["Read"],
            "tech_extraction": ["Read"],
            "fake_data_reproduction": ["Read", "run_cmd"],
            "literature_analysis": ["Read", "Grep"],
            "relation_analysis": ["Read", "getKnowledge"],
            "knowledge_integration": ["Read", "Write", "Edit"],
        }

        if agent_type.lower() not in tools_map:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return tools_map[agent_type.lower()]


class PaperAnalysisDelegate:
    """论文分析子智能体执行器"""

    def __init__(self, agent_type: str, task: str, agent_id: str, tools_registry, output_callback=None):
        """
        初始化论文分析子智能体

        Args:
            agent_type: agent 类型（pdf_parser, content_analysis 等）
            task: 要执行的任务描述
            agent_id: agent 唯一标识符
            tools_registry: 工具注册表实例
            output_callback: 输出回调函数
        """
        self.agent_type = agent_type
        self.task = task
        self.agent_id = agent_id
        self.tools_registry = tools_registry
        self.output_callback = output_callback

        # 获取系统提示词
        self.system_prompt = PaperAnalysisDelegateConfig.get_system_prompt(agent_type)

        # 获取允许的工具列表
        self.allowed_tools = PaperAnalysisDelegateConfig.get_allowed_tools(agent_type)

        # 初始化 LLM
        self.llm = LargeLanguageModel()

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.token_count = 0
        self.tool_calls = []

        # JSONL 日志
        self._log_dir = Path("runtime_memory/logs/agents/paper")

    def _notify(self, message: str, level: str = "info"):
        """通知输出消息"""
        if self.output_callback:
            self.output_callback(message, level)

    def _build_tools_schema(self) -> str:
        """构建工具 schema 描述"""
        tools_desc = []

        for tool_name in self.allowed_tools:
            tool_entry = self.tools_registry.get_entry(tool_name)
            if tool_entry:
                tools_desc.append(f"## {tool_name}")
                tools_desc.append(f"描述: {tool_entry.description}")

                # 添加参数 schema
                schema = tool_entry.arguments_schema
                if schema:
                    tools_desc.append(f"参数: {json.dumps(schema, ensure_ascii=False, indent=2)}")

                tools_desc.append("")

        return "\n".join(tools_desc)

    def _build_prompt_messages(self) -> List[Dict[str, Any]]:
        """构建提示消息，支持 PromptCaching"""
        messages = []

        # 系统提示词（Level 1 缓存 - 总是缓存）
        system_message = {
            "role": "system",
            "content": self.system_prompt,
            "cache_control": {"type": "ephemeral"}  # 标记为可缓存
        }
        messages.append(system_message)

        # 工具 schema（Level 1 缓存 - 总是缓存）
        tools_schema = self._build_tools_schema()
        tools_message = {
            "role": "system",
            "content": f"# 可用工具\n\n{tools_schema}",
            "cache_control": {"type": "ephemeral"}  # 标记为可缓存
        }
        messages.append(tools_message)

        # 项目上下文（Level 1 缓存 - 总是缓存）
        import os
        project_root = os.getcwd()
        context_message = {
            "role": "system",
            "content": f"# 项目上下文\n\n当前工作目录: {project_root}\n所有文件路径都应该基于此目录。",
            "cache_control": {"type": "ephemeral"}  # 标记为可缓存
        }
        messages.append(context_message)

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
                "error": f"工具 {tool_name} 不允许在 {self.agent_type} agent 中使用",
                "allowed_tools": self.allowed_tools
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
                # 如果是异步函数，尝试在当前事件循环中运行
                try:
                    # 尝试获取当前事件循环
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 如果循环正在运行，使用 nest_asyncio 允许嵌套
                        import nest_asyncio
                        nest_asyncio.apply()
                        result = loop.run_until_complete(handler(**tool_args))
                    else:
                        result = loop.run_until_complete(handler(**tool_args))
                except RuntimeError:
                    # 没有事件循环，使用 asyncio.run 自动管理生命周期
                    async def _run_async():
                        return await handler(**tool_args)
                    result = asyncio.run(_run_async())
            else:
                result = handler(**tool_args)

            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({
                "error": f"工具执行失败: {str(e)}"
            }, ensure_ascii=False)

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """从 LLM 响应中解析工具调用"""
        tool_calls = []

        try:
            import re

            # 方式1: 标准 JSON 格式 {"tool": "xxx", "args": {...}}
            # 支持嵌套的大括号
            json_pattern = r'\{(?:[^{}]|\{[^{}]*\})*"tool"(?:[^{}]|\{[^{}]*\})*\}'
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
                    # JSON解析失败，可能是因为嵌套引号问题
                    # 尝试提取tool和args
                    try:
                        tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', match)
                        if tool_match:
                            tool_name = tool_match.group(1)
                            # 提取args对象
                            args_match = re.search(r'"args"\s*:\s*\{([^}]+)\}', match, re.DOTALL)
                            args = {}
                            if args_match:
                                args_content = args_match.group(1)
                                # 提取每个参数 - 支持包含转义引号的值
                                param_pattern = r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"'
                                for param_match in re.finditer(param_pattern, args_content):
                                    key = param_match.group(1)
                                    value = param_match.group(2)
                                    # 处理转义字符
                                    value = value.replace('\\"', '"').replace('\\\\', '\\')
                                    args[key] = value

                            tool_calls.append({
                                "tool": tool_name,
                                "args": args
                            })
                    except:
                        continue

            # 方式2: [TOOL_CALL] 标记格式
            if not tool_calls:
                tool_call_pattern = r'\[TOOL_CALL\](.*?)\[/TOOL_CALL\]'
                matches = re.findall(tool_call_pattern, response, re.DOTALL)

                for match in matches:
                    # 手动解析 {tool => "xxx", args => {--key "value"}} 格式
                    try:
                        # 提取 tool 名称
                        tool_match = re.search(r'tool\s*=>\s*["\']([^"\']+)["\']', match)
                        if not tool_match:
                            continue

                        tool_name = tool_match.group(1)

                        # 提取 args 部分
                        args_match = re.search(r'args\s*=>\s*\{([^}]+)\}', match, re.DOTALL)
                        args = {}

                        if args_match:
                            args_content = args_match.group(1)
                            # 解析 --key "value" 格式
                            arg_pattern = r'--(\w+)\s+["\']([^"\']*(?:["\'][^"\']*)*)["\']'
                            for arg_match in re.finditer(arg_pattern, args_content):
                                key = arg_match.group(1)
                                value = arg_match.group(2)
                                # 处理转义的引号
                                value = value.replace('\\"', '"').replace("\\'", "'")
                                args[key] = value

                        tool_calls.append({
                            "tool": tool_name,
                            "args": args
                        })
                    except Exception:
                        continue

            # 方式3: XML invoke 格式 (MiniMax 原生格式)
            # <invoke name="ToolName" arg1="value1" arg2="value2"/>
            if not tool_calls:
                # 查找 <invoke> 标签，支持多行属性
                xml_pattern = r'<invoke\s+name="([^"]+)"\s*([^>]*)/?>'
                for xml_match in re.finditer(xml_pattern, response, re.DOTALL):
                    tool_name = xml_match.group(1)
                    attrs_text = xml_match.group(2)

                    # 解析属性
                    args = {}
                    attr_pattern = r'(\w+)\s*=\s*"([^"]*)"'
                    for attr_match in re.finditer(attr_pattern, attrs_text):
                        key = attr_match.group(1)
                        value = attr_match.group(2)
                        # 跳过 name（已用作 tool_name）
                        if key != "name":
                            args[key] = value

                    if tool_name:
                        tool_calls.append({
                            "tool": tool_name,
                            "args": args
                        })

            # 方式4: 纯文本 --key "value" 格式 (一些 MiniMax 输出)
            if not tool_calls:
                text_args_pattern = r'--(\w+)\s+"([^"]*)"'
                args = dict(re.findall(text_args_pattern, response))
                if args:
                    # 尝试从上下文中推断工具名
                    for known_tool in ["Read", "Write", "Edit", "Glob", "Grep"]:
                        if known_tool in response:
                            tool_calls.append({
                                "tool": known_tool,
                                "args": args
                            })
                            break

            # 方式5: MiniMax XML 格式（含 <parameter name="key">value</parameter>）
            # <invoke name="ToolName"><parameter name="key">value</parameter></invoke>
            if not tool_calls:
                xml_invoke_pattern = r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>'
                for xml_match in re.finditer(xml_invoke_pattern, response, re.DOTALL):
                    tool_name = xml_match.group(1)
                    params_text = xml_match.group(2)

                    # 解析 <parameter name="key">value</parameter>
                    args = {}
                    param_pattern = r'<parameter\s+name="([^"]+)"\s*>(.*?)</parameter>'
                    for param_match in re.finditer(param_pattern, params_text, re.DOTALL):
                        key = param_match.group(1)
                        value = param_match.group(2).strip()
                        if key != "name":
                            args[key] = value

                    if tool_name:
                        tool_calls.append({
                            "tool": tool_name,
                            "args": args
                        })

        except Exception:
            # 所有解析方式都失败了，tool_calls 为空
            pass

        return tool_calls

    def execute(self) -> Dict[str, Any]:
        """
        执行论文分析子智能体任务（多轮 ReAct 循环）

        Returns:
            执行结果字典，包含 status, content, error 等字段
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

            # 最大循环次数
            max_iterations = config.get("agent_delegate.max_iterations", 8)

            # 保留的历史轮数
            max_history_turns = config.get("agent_delegate.max_history_turns", 3)

            final_answer = None

            # ReAct 循环
            for iteration in range(max_iterations):
                # 通知当前迭代
                self._notify(f"  [{self.agent_id}] 迭代 {iteration + 1}/{max_iterations}", "info")

                # 调用 LLM（子智能体需要更大的输出空间）
                try:
                    response = self.llm.query(messages, max_tokens=8192)
                except Exception as llm_error:
                    self._notify(f"  [{self.agent_id}] LLM调用失败: {str(llm_error)}", "error")
                    # JSONL 迭代错误事件
                    _write_agent_jsonl(self._log_dir, self.agent_id, {
                        "event": "iteration",
                        "iteration": iteration + 1,
                        "error": str(llm_error),
                        "timestamp": time.time(),
                    })
                    raise  # 重新抛出异常

                # 累加 token 数量
                self.token_count += self.llm.getTokenSize(str(messages[-1:]) + response)

                # JSONL 迭代事件（LLM 输出完整记录在 LLMResponseLogger 中）
                response_preview = response[:200] + "..." if len(response) > 200 else response
                _write_agent_jsonl(self._log_dir, self.agent_id, {
                    "event": "iteration",
                    "iteration": iteration + 1,
                    "response_preview": response_preview,
                    "response_length": len(response),
                    "timestamp": time.time(),
                })

                # 显示思考过程（截断显示）
                thinking_preview = response[:200] + "..." if len(response) > 200 else response
                self._notify(f"  [{self.agent_id}] 思考: {thinking_preview}", "debug")

                # 解析工具调用
                tool_calls = self._parse_tool_calls(response)

                if tool_calls:
                    # 有工具调用 - 执行工具并继续循环
                    # 将 assistant 的响应加入消息历史
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
                        self._notify(f"  [{self.agent_id}] 调用工具: {tool_name}({args_str}...)", "info")

                        result = self._execute_tool(tool_name, tool_args)

                        # 显示工具结果预览
                        result_preview = result[:150] + "..." if len(result) > 150 else result
                        self._notify(f"  [{self.agent_id}] 工具结果: {result_preview}", "debug")

                        tool_results_text.append(f"### {tool_name}\n{result}")

                    # 将工具结果作为 user 消息加入历史
                    messages.append({
                        "role": "user",
                        "content": "## 工具执行结果\n\n" + "\n\n".join(tool_results_text)
                    })

                    # 保持上下文窗口在合理范围内
                    if len(messages) > 2 + max_history_turns * 2:
                        messages = messages[:2] + messages[-(max_history_turns * 2):]

                    # 继续下一轮循环
                    continue
                else:
                    # 没有工具调用 - 这是最终答案
                    self._notify(f"  [{self.agent_id}] 得出最终结论", "info")
                    final_answer = response
                    break

            # 如果达到最大迭代次数仍未给出最终答案
            if final_answer is None:
                self._notify(f"  [{self.agent_id}] 达到最大迭代次数", "warning")
                final_answer = f"[达到最大迭代次数 {max_iterations}]\n\n" + response

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
            self._notify(f"  [{self.agent_id}] 执行失败: {str(e)}", "error")

            # JSONL end 事件（错误）
            _write_agent_jsonl(self._log_dir, self.agent_id, {
                "event": "end",
                "status": "error",
                "error": str(e),
                "tool_calls_count": len(self.tool_calls),
                "token_count": self.token_count,
                "duration_ms": int((self.end_time - self.start_time) * 1000),
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
