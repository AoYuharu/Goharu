"""Agent Loop 子系统对外门面 —— 外部 harness 的 ReAct 循环入口。

最小接入：

    import asyncio
    from Tools.api import create_tools
    from Agent.api import create_agent

    async def main():
        tools = await create_tools(modules=["Tools.builtin.core_tools"])
        agent = await create_agent(
            tools=tools,
            llm=my_llm,        # 结构化接口：query(messages, on_retry=None, tools=..., tool_choice=...)
            memory=None,       # 可选：Memory.bridge.create_memory(...) 或 None（内存态）
            on_event=lambda etype, payload: print(etype, payload),  # 可选事件回调
        )
        reply = await agent.run_turn("帮我看看当前目录有什么文件")
        print(reply)

    asyncio.run(main())

边界（不含，留给宿主）：消息批处理合并、ESC 中断、PipelineManager 自动记忆管线、
micro-compact 调度。Goharu 的完整接线见 Gateway/gateway_entry.py。
"""

import logging
import uuid
from datetime import datetime

from Agent.ActorAgent import ActorAgent, shutdown_llm_executor

logger = logging.getLogger(__name__)

__all__ = ["create_agent", "StandaloneAgent", "shutdown_llm_executor"]


class _ListWorkingMemory:
    """memory=None 时的内存态工作记忆（仅本轮进程存活）。

    接口对齐 ActorAgent 的结构化接口契约：append / get_context；
    不提供 retrieve_prompt_memory → 检索自动关闭（ActorAgent hasattr 检查）。
    """

    def __init__(self):
        self._messages = []

    def append(self, message):
        msg = dict(message)
        msg.setdefault("id", f"msg_{uuid.uuid4().hex[:12]}")
        msg.setdefault("timestamp", datetime.now().isoformat())
        self._messages.append(msg)

    def get_context(self):
        return list(self._messages)

    def clear_context(self):
        self._messages.clear()


class StandaloneAgent:
    """最小 ReAct turn 驱动：包装 ActorAgent 的单步 act() 为完整用户轮。

    事件回调 on_event(event_type, payload)：
      thinking {text} / text {text} / tool_call {tool, arguments} /
      tool_result {tool, preview} / retry {attempt, max, phase} /
      step {step} / answer {answer} / error {error}
    """

    def __init__(self, actor, working, on_event=None, max_steps=8, max_tool_retries=3):
        self.actor = actor
        self.working = working
        self.on_event = on_event
        self.max_steps = max_steps
        self.max_tool_retries = max_tool_retries

    def _emit(self, event_type, **payload):
        if self.on_event is None:
            return
        try:
            self.on_event(event_type, payload)
        except Exception as e:
            logger.warning("on_event callback failed: %s", e)

    async def run_turn(self, user_message: str) -> str:
        """跑一个完整用户轮：可能含多步工具调用，返回最终回复文本。"""
        self.working.append({"role": "user", "content": user_message})
        self.actor.reset_retrieval_cache()

        for step in range(1, self.max_steps + 1):
            self._emit("step", step=step, max_steps=self.max_steps)
            response = await self.actor.act(
                max_retries=self.max_tool_retries,
                on_tool_call_start=lambda tool, args, *rest: self._emit(
                    "tool_call", tool=tool, arguments=args),
                on_thinking=lambda text: self._emit("thinking", text=text),
                on_text=lambda text: self._emit("text", text=text),
                on_tool_result=lambda tool, *rest: self._emit(
                    "tool_result", tool=tool,
                    preview=str(rest[-1] if rest else "")[:200]),
                on_retry=lambda attempt, mx, backoff, phase, *rest: self._emit(
                    "retry", attempt=attempt, max=mx, backoff=backoff, phase=phase),
            )
            rtype = response.get("type")
            logger.info("run_turn step %d: %s", step, rtype)

            if rtype == "answer":
                answer = response.get("answer", "")
                self._emit("answer", answer=answer)
                return answer
            if rtype in ("tool", "tool_batch", "tool_backgrounded", "continue"):
                continue
            if rtype == "interrupted":
                return "请求已被中断。"
            if rtype == "error":
                error = str(response.get("error", "Unknown error"))
                self._emit("error", error=error)
                return f"Error: {error}"

            logger.warning("unknown actor response type: %s", rtype)
            break

        return f"已达到最大步数（{self.max_steps}）。请缩小任务范围或分步提问。"


def _wire_confirmation(on_confirm):
    """把宿主的同步确认回调接到 confirmation_manager 的异步缝上。

    on_confirm(cmd, description) -> bool：工具线程阻塞等待确认时，
    该回调被（在工具线程上）同步调用，返回值即放行/拒绝。
    """
    from Tools.confirmation_manager import confirmation_manager

    def notifier(confirm_id, cmd, description):
        try:
            approved = bool(on_confirm(cmd, description))
        except Exception as e:
            logger.warning("on_confirm callback failed, denying: %s", e)
            approved = False
        confirmation_manager.resolve(confirm_id, approved)

    confirmation_manager.set_notifier(notifier)


async def create_agent(
    tools,
    llm=None,
    llm_config=None,
    memory=None,
    config=None,
    on_event=None,
    max_steps=8,
    max_tool_retries=3,
    on_confirm=None,
    subagent_llm=None,
    prompt_loader=None,
) -> StandaloneAgent:
    """创建独立 Agent Loop。

    Args:
        tools: Tools.api.create_tools() 返回的运行时（需含 last_tool_definitions）
        llm: 可选。结构化接口对象：query(messages, on_retry=None, tools=...,
             tool_choice=...) -> 带 .content 块（text/tool_use/thinking）与
             .stop_reason 的响应。None → Goharu LLMCore 单例（需 configurationLoader）
        llm_config: 可选。配置 dict → 自动经 Agent.llm.create_llm 创建客户端
             （与 llm 二选一；llm 优先）。键表见 Agent/llm.py
        memory: 可选。Memory.bridge.create_memory() 或任何 append/get_context
             对象（有 retrieve_prompt_memory 则自动启用检索注入）；None → 内存态
        config: 可选 dict，注入 Tools 层配置代理（background_timeout / verify_nudge /
             security 等，键表见 Tools/README.md）
        on_event: 可选事件回调 on_event(event_type: str, payload: dict)
        max_steps: 单轮最大 LLM 步数（默认 8，同 Goharu agent.maxDepth）
        max_tool_retries: 单次推理内工具失败重试上限（默认 3，透传 act）
        on_confirm: 可选危险命令确认回调 on_confirm(cmd, description) -> bool。
             传入后自动开启 tools.security.allow_confirmation（灰名单命令走确认，
             否则灰名单按黑名单硬拦）。回调在工具线程上同步调用。
        subagent_llm: 可选，AgentDelegate 子代理（Explore/Plan/Verify）使用的 LLM。
             None 时自动跟随 llm/llm_config；三者都 None 则子代理走 Goharu
             LLMCore 单例。独立于主 llm 指定可用小模型跑子代理以省钱。
        prompt_loader: 可选，Prompting.PromptLoader 实例（Prompting.api.
             create_prompt_loader 构造）。宿主组合根据此决定"哪些提示词段进
             system"（如按功能启停追加段文件）；None → 全局配置的 prompt.system.*
    """
    if llm is None and llm_config is not None:
        from Agent.llm import create_llm
        llm = create_llm(llm_config)

    # 子代理 LLM 接线：注入优先，其次跟随主 llm（无副作用模块，不触发工具注册）
    effective_sub_llm = subagent_llm if subagent_llm is not None else llm
    if effective_sub_llm is not None:
        from Tools.subagent_config import set_subagent_llm
        set_subagent_llm(effective_sub_llm)

    merged_config = dict(config or {})
    if on_confirm is not None:
        # 确保灰名单命令走确认而非硬拦（扁平键优先，与 ConfigView 兼容）
        tools_cfg = dict(merged_config.get("tools") or {})
        security_cfg = dict(tools_cfg.get("security") or {})
        security_cfg.setdefault("allow_confirmation", True)
        tools_cfg["security"] = security_cfg
        merged_config["tools"] = tools_cfg
    if merged_config:
        from Tools.config_proxy import set_config
        set_config(merged_config)
    if on_confirm is not None:
        _wire_confirmation(on_confirm)

    # 确保工具定义已加载（act 内部也有兜底，这里提前加载以尽早暴露错误）
    await tools.list_tools()

    working = memory if memory is not None else _ListWorkingMemory()
    actor = ActorAgent(tools, working, llm=llm, prompt_loader=prompt_loader)
    agent = StandaloneAgent(
        actor, working, on_event=on_event,
        max_steps=max_steps, max_tool_retries=max_tool_retries,
    )
    logger.info(
        "create_agent: tools=%d llm=%s memory=%s confirm=%s",
        len(getattr(tools, "last_tool_definitions", []) or []),
        "injected" if llm is not None else "llmcore",
        type(working).__name__,
        on_confirm is not None,
    )
    return agent
