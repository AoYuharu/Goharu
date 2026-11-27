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
import os
import re

from Tools.config_proxy import get_config

logger = logging.getLogger(__name__)

_MUTATING_TOOLS = {"Write", "Edit"}

# 只统计"代码类"文件；文档/临时文件/日志不触发验收提醒
_CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".json", ".yaml", ".yml",
    ".toml", ".sh", ".ps1", ".html", ".css",
}

# 高风险目录（与 prompts/actor/base.md 的验收范围一致）：这些位置的改动
# 才触发"必须 Verify"的硬闸门；其他代码改动只做提醒，不拦截最终回答。
_RISKY_SEGMENTS = (
    "agent/", "tools/", "gateway/", "tui-ts/", "prompts/",
    "config.yaml", "config_e2e_cache.yaml",
)


def _mutation_path(arguments: dict) -> str:
    raw = str((arguments or {}).get("path") or "")
    return raw.replace("\\", "/")


def _is_code_path(path: str) -> bool:
    if not path:
        return True  # 拿不到路径时按代码处理（保守）
    lowered = path.lower()
    if "/temp/" in lowered or lowered.startswith("/tmp/"):
        return False
    return os.path.splitext(lowered)[1] in _CODE_EXTS


def _is_risky_path(path: str) -> bool:
    lowered = path.lower()
    return any(segment in lowered for segment in _RISKY_SEGMENTS)


class VerifyNudge:
    """会话级单例：跟踪修改次数与 verify 调用。"""

    _instance = None
    _mutation_count = 0
    _gate_fires = 0
    _risky_touched = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._mutation_count = 0
            cls._instance._gate_fires = 0
            cls._instance._risky_touched = False
        return cls._instance

    def _enabled(self) -> bool:
        return bool(get_config().get("tools.verify_nudge.enabled", True))

    def _threshold(self) -> int:
        return int(get_config().get("tools.verify_nudge.mutation_threshold", 3) or 3)

    def gate_final_answer(self) -> str | None:
        """主 agent 输出最终答案前的硬闸门。

        累计修改达到阈值且 verify 未跑过时，返回拦截消息（调用方应把该消息
        塞回会话让模型先调 verify，而不是直接回答用户）。返回 None = 放行。

        防死循环：每次会话最多拦截 max_gate_fires 次，超过后放行并记日志——
        模型若坚持不验证，不能把它卡死在循环里。
        """
        if not self._enabled():
            return None
        if not bool(get_config().get("tools.verify_nudge.gate_final_answer", True)):
            return None
        if self._mutation_count < self._threshold():
            return None
        # 只有高风险目录（Agent/Tools/Gateway/TUI/prompts/config）的改动才硬拦截；
        # 普通代码改动由提醒文本建议验收，不阻断最终回答
        if not self._risky_touched:
            return None

        max_fires = int(get_config().get("tools.verify_nudge.max_gate_fires", 2) or 2)
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
            f"系统拦截：你在本轮任务中修改了 {self._mutation_count} 处文件，"
            f"其中涉及高风险区域（Agent/Tools/Gateway/TUI/prompts/config），"
            f"但尚未调用 verify 子智能体进行独立验收，因此不能直接向用户报告完成。\n"
            f"现在必须调用 AgentDelegate(agent_type=\"verify\", task=...)。"
            f"task 里只写两件事：改了哪些文件、原始目的是什么（不要给测试命令/预期行为，"
            f"避免引导它的验证方向）。\n"
            f"Verify 返回 PASS 且你抽查通过后，才能向用户报告成功；"
            f"返回 FAIL 则修复后重新验证。"
        )

    def after_tool_call(self, tool_name: str, arguments: dict, result_text: str) -> str | None:
        """每次工具调用结束后检查，需要提醒则返回提醒文本，否则返回 None。"""
        if not self._enabled():
            return None

        # verify 子智能体只有 PASS 才清零；FAIL/PARTIAL/error/无 verdict 不算验收完成
        if tool_name == "AgentDelegate":
            agent_type = str((arguments or {}).get("agent_type", "")).lower()
            if agent_type == "verify" and re.search(r"VERDICT:\s*PASS\b", result_text, re.IGNORECASE):
                self._mutation_count = 0
                self._gate_fires = 0
                self._risky_touched = False
            return None

        if tool_name not in _MUTATING_TOOLS:
            return None

        # 只统计成功的修改（错误结果不含未格式化的 "error" 字段说明；格式化后
        # 的错误以 ❌ 开头）
        if result_text.startswith("❌"):
            return None

        # 文档/日志/临时文件不算"需要验收的改动"
        path = _mutation_path(arguments)
        if not _is_code_path(path):
            return None

        self._mutation_count += 1
        if _is_risky_path(path):
            self._risky_touched = True

        threshold = self._threshold()
        if self._mutation_count < threshold:
            return None

        logger.info(
            "verify nudge 触发: 已连续 %d 次代码修改未验证 (risky=%s)",
            self._mutation_count, self._risky_touched,
        )
        if self._risky_touched:
            return (
                f"NOTE: 你已修改 {self._mutation_count} 处代码，且涉及高风险区域"
                f"（Agent/Tools/Gateway/TUI/prompts/config）。向用户报告完成前，"
                f"请调用 AgentDelegate(agent_type=\"verify\") 做独立验收；"
                f"task 只写改了哪些文件和原始目的。"
            )
        return (
            f"NOTE: 你已修改 {self._mutation_count} 处代码。如果这次改动改变了实际行为"
            f"（不是注释/格式/文案），请先用真实命令自测；有行为变化且需要独立验收时，"
            f"再调用 AgentDelegate(agent_type=\"verify\")。"
        )


verify_nudge = VerifyNudge()
