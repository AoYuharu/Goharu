from Agent.LargeLanguageModel import LargeLanguageModel
import json

from Memory.ToolCall import ToolCall
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer
from Tools.guard import ToolCallGuard
from Tools.task_guide import TaskGuide


class ActorAgent(LargeLanguageModel):
    def __init__(self, tool_runtime, working):
        super().__init__()
        self.tool_runtime = tool_runtime
        self.working = working
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()
        self.guard = None  # 延迟初始化，等待工具列表加载
        self.task_guide = TaskGuide()  # 任务引导器

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

    async def act(self, max_retries=3):
        """
        执行一次推理，支持工具调用重试

        Args:
            max_retries: 工具调用失败时的最大重试次数（防线5）
        """
        reply = self.query(self.build_messages())

        tool_call = ToolCall.try_from_text(reply)
        if tool_call is not None:
            raw_tool_name = tool_call.tool_name
            raw_args = tool_call.arguments

            # 防线 1-4：使用防护器修复工具调用
            guard_result = None
            if self.guard:
                guard_result = self.guard.guard(raw_tool_name, raw_args)

                # 记录防护日志（verbose 模式）
                if guard_result.get("logs"):
                    guard_logs = "\n".join(guard_result["logs"])
                    # 可选：记录到 working memory 或日志系统
                    # print(f"[Guard] {guard_logs}")

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
                        return await self.act(max_retries=max_retries - 1)
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
                    return await self.act(max_retries=max_retries - 1)

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
