"""Prompting 子系统对外门面 —— ContextManager：一站式上下文管理。

职责：把（系统提示词 + 对话历史 + 记忆检索包）组装成 Anthropic Messages 格式的
messages 列表，保持 prompt cache 前缀逐字节稳定（断点纪律见 README），
并内置完整压缩阶梯：

- L0 规则微折叠：micro_compact（过期长工具结果折叠，无 LLM）
- L1 窗口截断：truncate_history（保留最近 N 条，配对安全）
- L2 LLM 摘要压缩：should_llm_compact / llm_compact（token 阈值，需注入 llm）
- L3 记忆蒸馏：属 Memory 子系统（retrieve 回注），见 Memory/README.md

最小接入：

    from Prompting.api import create_context_manager

    ctx = create_context_manager(system_prompt="你是一个助手。")
    history = [{"role": "user", "content": "你好", "id": "msg_1"}]
    messages = ctx.build_messages(history, retrieval_pack=None)

    report = ctx.context_report(history)   # 各压缩层状态与建议
    if report["llm_compact"]["recommended"]:
        summary = ctx.llm_compact(history)  # 需 create 时注入 llm

Goharu 特有注入（git_status / Skills 列表）在独立模式下默认关闭。
"""

import logging
from pathlib import Path

from Core.TokenEstimator import TokenEstimator
from Core.ToolResultBudget import ToolResultBudget
from Core.PromptSection import PromptSection
from Prompting.ContextCompactor import ContextCompactor
from Prompting.MicroCompactor import MicroCompactor
from Prompting.loop_messages import truncate_history as _truncate_history
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptLoader import PromptLoader
from Prompting.PromptRenderer import PromptRenderer

logger = logging.getLogger(__name__)

__all__ = [
    "create_context_manager",
    "create_context_pipeline",  # 兼容别名
    "create_prompt_loader",
    "ContextManager",
    "ContextPipeline",  # 兼容别名
]


def create_prompt_loader(config=None, base_dir=None) -> PromptLoader:
    """创建提示词文件加载器（供宿主注入到 ActorAgent 等组装器）。

    config: dict（按 prompt.system.<key> 读文件列表）或任何带 .get/.path 的
    配置对象（宿主可叠加"功能段"——如启用某功能时追加 actor 段文件）；
    None → 回退 Goharu 全局配置。
    本模块不认识任何具体功能，功能启停的决策属于组合根（宿主）。
    """
    if config is None or hasattr(config, "get"):
        return PromptLoader(config)
    return PromptLoader(_PipelineConfig(config, base_dir))


class _PipelineConfig:
    """PromptLoader 兼容适配器：dict 配置 + .path 属性（相对路径解析基准）。"""

    def __init__(self, data: dict | None, base_dir=None):
        self._data = data or {}
        base = Path(base_dir) if base_dir else Path.cwd()
        self.path = str(base / "config.yaml")

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        node = self._data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class _InlinePromptLoader(PromptLoader):
    """system_prompt 字符串直注：不读文件，shared 为空。"""

    def __init__(self, system_prompt: str, config_obj):
        super().__init__(config_obj)
        self._inline = system_prompt

    def load_system_sections(self, key):
        if key == "actor":
            return [PromptSection(kind="system", title="actor", content=self._inline)]
        if key == "shared":
            return []
        return super().load_system_sections(key)


class ContextManager:
    """一站式上下文管理：组装 / 断点 / 压缩阶梯 / token 估算。

    缓存纪律（build_messages 内建，无需调用方操心）：
    - 断点 ≤2：最后一个 system 块 + 最后一个可缓存历史轮
    - retrieval_pack 等 per-turn 内容贴在当轮用户消息上，并按消息 id 记入
      memory_contexts（调用方跨 turn 持有的 dict），历史渲染时贴回原消息，
      保证旧消息内容永不变化 → 前缀缓存跨 turn 稳定
    """

    def __init__(
        self,
        config: dict | None = None,
        system_prompt: str | None = None,
        base_dir=None,
        include_git_status: bool = False,
        include_skills: bool = False,
        llm=None,
        max_messages: int = 0,
        compact_threshold_tokens: int = 30000,
    ):
        cfg = _PipelineConfig(config, base_dir)
        if system_prompt is not None:
            loader = _InlinePromptLoader(system_prompt, cfg)
        else:
            # 不传 system_prompt：按 config 的 prompt.system.actor / .shared 读文件
            loader = PromptLoader(cfg)
        self.assembler = PromptAssembler(
            loader,
            include_git_status=include_git_status,
            include_skills=include_skills,
            # 独立部署无 persona.md 文件：persona 摘要走 per-turn 注入（旧行为）
            include_persona=False,
        )
        self.renderer = PromptRenderer()
        self._estimator = TokenEstimator()
        # L2 LLM 压缩器惰性构造：llm=None 时走 Goharu LLMCore 单例（仓库内行为），
        # 但不打 compact 就不构造（避免无谓读取全局配置/初始化 provider）
        self._compactor = None
        self._compactor_llm = llm
        self.max_messages = int(max_messages or 0)
        self.compact_threshold_tokens = int(compact_threshold_tokens or 30000)

    @property
    def compactor(self) -> ContextCompactor:
        if self._compactor is None:
            self._compactor = ContextCompactor(llm=self._compactor_llm)
        return self._compactor

    def build_messages(
        self,
        history,
        *,
        retrieval_pack=None,
        memory_contexts=None,
        extra_system_prompt=None,
        reminder_state=None,
    ):
        """组装 messages 列表。

        Args:
            history: 对话记录 [{"role": "user"/"assistant", "content": str, "id"?,
                     "timestamp"?}...]（id 是记忆钉账的锚点，强烈建议提供）
            retrieval_pack: {"profile_summary": str, "memories": [...], "scenes": [...],
                            "memory_stats"?: {...}}（Memory 子系统 retrieve() 的返回原样传入即可）
            memory_contexts: 调用方持有的 dict（跨 turn 复用同一对象！）
            extra_system_prompt: 附加指令（追加在对话末尾的 system 块）
            reminder_state: 便利贴节流状态 dict（跨 turn 复用；None 则无状态退化：
                            date_change 不触发、技能清单每轮全量）

        Returns:
            Anthropic Messages 格式列表（system 块在头部，cache_control 已布点）
        """
        document = self.assembler.build_actor_document(
            history,
            extra_system_prompt=extra_system_prompt,
            retrieval_pack=retrieval_pack,
            memory_contexts=memory_contexts,
            reminder_state=reminder_state,
        )
        return self.renderer.render_document(document)

    def estimate_tokens(self, text: str) -> int:
        """估算文本 token 数（有 HF tokenizer 用真实计数，否则 len//3+1）。"""
        return self._estimator.estimate(text)

    def micro_compact(self, messages, age_threshold_hours=None,
                      keep_tool_results=None, min_content_chars=None):
        """L0 规则微折叠：折叠过期的长工具结果（无 LLM，纯规则）。

        返回 (messages, folded_count)。消息需带 ISO timestamp 字段。
        """
        return MicroCompactor.compact(
            messages,
            age_threshold_hours=age_threshold_hours,
            keep_tool_results=keep_tool_results,
            min_content_chars=min_content_chars,
        )

    # ------------------------------------------------------------------
    # L1 窗口截断
    # ------------------------------------------------------------------

    def truncate_history(self, history, max_messages=None):
        """L1 窗口截断：保留最近 N 条消息，保持 tool_use/tool_result 配对完整。

        max_messages=None 时用创建时的 max_messages 配置；<=0 表示不截断。
        """
        limit = self.max_messages if max_messages is None else int(max_messages)
        return _truncate_history(history, limit)

    # ------------------------------------------------------------------
    # L2 LLM 摘要压缩
    # ------------------------------------------------------------------

    def estimate_messages_tokens(self, messages) -> int:
        """估算消息列表的总 token 数。"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += self._estimator.estimate(str(block.get("text", "")))
            else:
                total += self._estimator.estimate(str(content))
        return total

    def should_llm_compact(self, messages, max_tokens=None) -> bool:
        """是否该做 L2 LLM 摘要压缩（token 阈值判定）。"""
        threshold = max_tokens if max_tokens is not None else self.compact_threshold_tokens
        return self.estimate_messages_tokens(messages) >= threshold

    def llm_compact(self, messages, system_prompt=None) -> str:
        """L2 LLM 摘要压缩：把整段历史压缩为一条摘要文本。

        需要在 create_context_manager 时注入 llm（独立部署）；
        仓库内未注入则回退 Goharu LLMCore 单例。
        """
        return self.compactor.compact(messages, system_prompt=system_prompt)

    # ------------------------------------------------------------------
    # 工具结果预算（压缩阶梯的辅助层）
    # ------------------------------------------------------------------

    def apply_result_budget(
        self,
        results,
        cache_dir=None,
        max_single_tokens=None,
        max_batch_tokens=None,
    ) -> bool:
        """工具结果 token 预算：超限结果落盘缓存，原位替换为占位符引用。

        工具结果是上下文膨胀的最大来源——单条超 max_single_tokens（默认 8000）
        或一批超 max_batch_tokens（默认 24000）时，完整内容写入
        cache_dir（默认 ./runtime_memory/tool_cache），消息里只留引导模型
        用 Read 按需读取的占位符。

        Args:
            results: [{"tool_name": str, "result_text": str}, ...]（原地修改）
        Returns:
            bool：是否有结果被替换
        """
        return ToolResultBudget.apply(
            results,
            cache_dir=cache_dir,
            max_single_tokens=max_single_tokens,
            max_batch_tokens=max_batch_tokens,
        )

    # ------------------------------------------------------------------
    # 决策辅助
    # ------------------------------------------------------------------

    def context_report(self, messages) -> dict:
        """各压缩层状态一览（给 harness 的压缩决策用）。

        返回 {message_count, estimated_tokens, micro_compact, window, llm_compact}。
        """
        compacted, would_fold = MicroCompactor.compact(list(messages))
        del compacted
        tokens = self.estimate_messages_tokens(messages)
        over_window = (
            self.max_messages > 0 and len(messages) > self.max_messages
        )
        return {
            "message_count": len(messages),
            "estimated_tokens": tokens,
            "micro_compact": {"would_fold": would_fold},
            "window": {
                "max_messages": self.max_messages,
                "would_drop": max(0, len(messages) - self.max_messages)
                if self.max_messages > 0 else 0,
                "over": over_window,
            },
            "llm_compact": {
                "threshold_tokens": self.compact_threshold_tokens,
                "recommended": tokens >= self.compact_threshold_tokens,
            },
        }


# 兼容别名：旧名 ContextPipeline 指向 ContextManager
ContextPipeline = ContextManager


def create_context_manager(
    config: dict | None = None,
    system_prompt: str | None = None,
    base_dir=None,
    include_git_status: bool = False,
    include_skills: bool = False,
    llm=None,
    max_messages: int = 0,
    compact_threshold_tokens: int = 30000,
) -> ContextManager:
    """创建上下文管理器。

    Args:
        config: 可选配置 dict（如 {"prompt": {"system": {"actor": ["path/to/base.md"]}}}）；
                仅在 system_prompt 为 None 时用于定位提示词文件
        system_prompt: 系统提示词字符串（直注，不读文件）
        base_dir: config 相对路径的解析基准（默认当前目录）
        include_git_status / include_skills: Goharu 特有注入，独立部署默认关闭
        llm: 可选，L2 摘要压缩用（query(messages)->str 或响应对象）；
             None 时仓库内回退 Goharu LLMCore
        max_messages: L1 窗口截断阈值（0=不截断）
        compact_threshold_tokens: L2 压缩触发阈值（should_llm_compact 默认值）
    """
    return ContextManager(
        config=config,
        system_prompt=system_prompt,
        base_dir=base_dir,
        include_git_status=include_git_status,
        include_skills=include_skills,
        llm=llm,
        max_messages=max_messages,
        compact_threshold_tokens=compact_threshold_tokens,
    )


def create_context_pipeline(
    config: dict | None = None,
    system_prompt: str | None = None,
    base_dir=None,
    include_git_status: bool = False,
    include_skills: bool = False,
) -> ContextPipeline:
    """兼容旧名：等价于 create_context_manager（不含压缩阶梯参数）。"""
    return create_context_manager(
        config=config,
        system_prompt=system_prompt,
        base_dir=base_dir,
        include_git_status=include_git_status,
        include_skills=include_skills,
    )
