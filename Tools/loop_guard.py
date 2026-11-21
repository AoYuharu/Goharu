"""重复工具调用检测 — 在工具结果末尾夹带循环警告（verify_nudge 同款模式）。

检测两类卡死模式（2026-10-14 真实事故：agent 对 SPA 页面同一 URL
navigate→Sleep→get_text 三连循环 16 轮 / 20 分钟，最后幻觉出"验证码弹窗"）：

1. 同一 (工具名, 规范化参数) 连续重复 N 次（默认 3）——盲点同一元素/同一 URL。
2. 工具调用序列模式（如 A→B→C）整体重复 N 轮——流程级死循环。

命中后在结果末尾追加警告，引导模型换策略而不是原样重复。
警告有冷却期（同一指纹警告过后，再重复 N 次才再次警告），避免每步刷屏。
"""

import hashlib
import json
import logging
from collections import deque

from Tools.config_proxy import get_config

logger = logging.getLogger(__name__)

_WINDOW = 24  # 序列模式检测的滑动窗口（覆盖 3 轮 × 最长 8 步的模式）


def _fingerprint(tool_name, arguments):
    """工具调用的规范化指纹：参数 JSON 序列化后取短哈希。"""
    try:
        canon = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canon = str(arguments)
    digest = hashlib.md5(canon.encode("utf-8")).hexdigest()[:8]
    return f"{tool_name}({digest})"


class LoopGuard:
    """会话级单例：跟踪工具调用序列，检测重复模式。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._reset()
        return cls._instance

    def _reset(self):
        self._recent = deque(maxlen=_WINDOW)  # [(fingerprint, tool_name)]
        self._warned = {}  # fingerprint/pattern → 警告后又重复的次数（冷却计数）

    def _enabled(self):
        return bool(get_config().get("tools.loop_guard.enabled", True))

    def _threshold(self):
        return int(get_config().get("tools.loop_guard.repeat_threshold", 3) or 3)

    def after_tool_call(self, tool_name, arguments, result_text):
        """每次工具调用后记录并检测，需要警告则返回警告文本。"""
        if not self._enabled():
            return None
        fp = _fingerprint(tool_name, arguments)
        self._recent.append((fp, tool_name))
        threshold = self._threshold()

        # ── 检测 1：同一调用连续重复 ──
        streak = 0
        for prev_fp, _ in reversed(self._recent):
            if prev_fp == fp:
                streak += 1
            else:
                break
        if streak >= threshold and self._cooldown_ready(f"same:{fp}", streak, threshold):
            logger.warning("loop_guard: 同一调用连续重复 %d 次: %s", streak, fp)
            return (
                f"⚠️ 循环警告：你已连续 {streak} 次以完全相同的参数调用 {tool_name}。"
                "如果前几次都没有得到预期效果，原样重复几乎不可能改变结果——"
                "请停下来诊断原因（读错误信息、检查假设），然后换不同的参数或完全不同的方法。"
                "若确实无解，如实告诉用户你观察到的现象，不要编造障碍（如不存在的验证码/弹窗）。"
            )

        # ── 检测 2：序列模式整体重复（A→B→C 重复 N 轮）──
        seq = [fp for fp, _ in self._recent]
        pattern_warn = self._detect_pattern_repeat(seq, threshold)
        if pattern_warn:
            names, rounds = pattern_warn
            key = f"pattern:{'→'.join(names)}"
            if self._cooldown_ready(key, rounds, threshold):
                logger.warning("loop_guard: 序列模式重复 %d 轮: %s", rounds, " → ".join(names))
                return (
                    f"⚠️ 循环警告：你在重复同一个操作流程（{' → '.join(names)}），"
                    f"已经重复了 {rounds} 轮。如果这条路一直没走通，继续重复只是在消耗时间——"
                    "请总结目前观察到的现象，分析哪一步没有达到预期，然后换一种根本不同的策略，"
                    "或如实向用户描述你卡在哪里。"
                )
        return None

    def _cooldown_ready(self, key, count, threshold):
        """冷却：同一指纹在 count=threshold 时警告一次，之后每再翻一倍才再警告。"""
        last = self._warned.get(key, 0)
        if count >= threshold and (last == 0 or count >= last * 2):
            self._warned[key] = count
            return True
        return False

    @staticmethod
    def _detect_pattern_repeat(seq, threshold):
        """检测 seq 末尾是否是某个长度 2-8 的模式重复了 threshold 轮。

        返回 (模式中的工具名列表, 轮数) 或 None。
        """
        n = len(seq)
        for plen in range(2, 9):
            need = plen * threshold
            if n < need:
                continue
            tail = seq[-need:]
            pattern = tail[:plen]
            if all(tail[i] == pattern[i % plen] for i in range(len(tail))):
                # 确认模式内部不是同一调用的简单重复（那个由检测 1 覆盖）
                if len(set(pattern)) == 1:
                    continue
                # 工具名序列（去掉参数哈希，展示更可读）
                names = [p.split("(")[0] for p in pattern]
                return (names, threshold)
        return None


loop_guard = LoopGuard()
