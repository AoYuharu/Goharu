"""Verify 提醒（nudge）— 工具结果层的行为兜底。

机制对齐 Claude Code TaskUpdateTool 的 verificationNudge：不靠系统提示词
自觉，而是在工具返回结果末尾夹带提醒（模型每一步必读的最新输入）。

Goharu 没有任务清单系统，等价的坏模式检测是：
「累计 N 次成功的文件修改（Write/Edit）且期间从未调用 verify 子智能体」。
命中后，在该次写操作的结果末尾追加提醒；调用 verify 后计数清零。

接入点：InProcessToolRuntime.call_tool 在格式化结果后调用 after_tool_call()，
把返回的提醒文本拼接到结果末尾。
"""

import logging

from configurationLoader import config

logger = logging.getLogger(__name__)

_MUTATING_TOOLS = {"Write", "Edit"}


class VerifyNudge:
    """会话级单例：跟踪修改次数与 verify 调用。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._mutation_count = 0
            cls._instance._gate_fires = 0
        return cls._instance

    def _enabled(self) -> bool:
        return bool(config.get("tools.verify_nudge.enabled", True))

    def _threshold(self) -> int:
        return int(config.get("tools.verify_nudge.mutation_threshold", 3) or 3)

    def gate_final_answer(self) -> str | None:
        """主 agent 输出最终答案前的硬闸门。

        累计修改达到阈值且 verify 未跑过时，返回拦截消息（调用方应把该消息
        塞回会话让模型先调 verify，而不是直接回答用户）。返回 None = 放行。

        防死循环：每次会话最多拦截 max_gate_fires 次，超过后放行并记日志——
        模型若坚持不验证，不能把它卡死在循环里。
        """
        if not self._enabled():
            return None
        if not bool(config.get("tools.verify_nudge.gate_final_answer", True)):
            return None
        if self._mutation_count < self._threshold():
            return None

        max_fires = int(config.get("tools.verify_nudge.max_gate_fires", 2) or 2)
        if self._gate_fires >= max_fires:
            logger.warning(
                "verify 硬闸门已达最大拦截次数 (%d)，放行最终答案（仍有 %d 处修改未验证）",
                max_fires, self._mutation_count,
            )
            return None

        self._gate_fires += 1
        logger.info(
            "verify 硬闸门拦截最终答案（第 %d 次）: %d 处修改未验证",
            self._gate_fires, self._mutation_count,
        )
        return (
            f"系统拦截：你在本轮任务中已完成 {self._mutation_count} 处文件修改，"
            f"但尚未调用 verify 子智能体进行独立验收，因此不能直接向用户报告完成。\n"
            f"现在必须调用 AgentDelegate(agent_type=\"verify\", task=...)，"
            f"task 中完整提供：原始任务、被修改文件清单、实现思路、新增功能、预期行为、"
            f"你设计的测试用例、构建/测试/运行命令、风险点、约束条件。\n"
            f"Verify 返回 PASS 且你抽查通过后，才能向用户报告成功；"
            f"返回 FAIL 则修复后重新验证。"
        )

    def after_tool_call(self, tool_name: str, arguments: dict, result_text: str) -> str | None:
        """每次工具调用结束后检查，需要提醒则返回提醒文本，否则返回 None。"""
        if not self._enabled():
            return None

        # verify 子智能体跑过 → 计数与闸门次数都清零（新一轮验收周期开始）
        if tool_name == "AgentDelegate":
            agent_type = str((arguments or {}).get("agent_type", "")).lower()
            if agent_type == "verify":
                self._mutation_count = 0
                self._gate_fires = 0
            return None

        if tool_name not in _MUTATING_TOOLS:
            return None

        # 只统计成功的修改（错误结果不含未格式化的 "error" 字段说明；格式化后
        # 的错误以 ❌ 开头）
        if result_text.startswith("❌"):
            return None

        self._mutation_count += 1
        threshold = self._threshold()
        if self._mutation_count < threshold:
            return None

        logger.info(
            "verify nudge 触发: 已连续 %d 次文件修改未验证",
            self._mutation_count,
        )
        return (
            f"NOTE: 你已连续完成 {self._mutation_count} 处文件修改，但尚未做过独立验证。"
            f"在向用户报告完成之前，请调用 AgentDelegate(agent_type=\"verify\") 进行对抗性验收"
            f"（传入原始任务、被修改文件、实现思路、构建/测试命令）。"
            f"你自己的自测不能替代 Verify 的独立判定。"
        )


verify_nudge = VerifyNudge()
