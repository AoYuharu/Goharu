"""每轮便利贴引擎 — claude-code system-reminder 机制的 Goharu 精简版。

设计（对照 claude-code attachments 管线）：
- 便利贴与消息流分离：提供者产出 (kind, text)，由 PromptAssembler 统一包裹成
  <system-reminder> 贴到当轮用户消息——位于全部缓存断点之后，不破坏前缀缓存。
- 节流/去重状态由调用方持有的 dict 承载（跨 turn 复用同一对象，与
  memory_contexts 账本同生命周期）；不传 state 则退化为无状态：
  date_change 只-bootstrap 不触发、技能清单每轮全量（facade 单次调用场景的安全默认）。
- 缓存稳定铁律：绝不把时间戳写进 system 前缀；跨天用 date_change 尾部追加，
  不回头改已渲染的历史消息（对齐 claude-code 的 userContext 永不更新策略）。
- 技能清单三态（full/sparse/delta）：首轮全量 → 无变化一行稀疏 → 有增删只列增量。
  清单完整内容已由首轮消息永久留在历史里，后续轮次引用即可，这是省 token 的关键。
"""
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

# persona 超过该天数未更新时，附加"可能过期、断言前先核实"提示
_PERSONA_STALE_DAYS = 3

_SKILL_INSTRUCTION = (
    "When a skill matches the user's request, this is a BLOCKING REQUIREMENT: "
    "invoke the Skill tool BEFORE generating any other response about the task."
)

# 计划模式的常驻指令（只读规划如何进行的引导）。由 TurnReminders 与
# tool_result_trailing_texts 两处共用，保证 turn 起点与工具循环每一步都贴出。
PLAN_MODE_REMINDER = (
    "【计划模式】你当前处于只读计划模式，禁止创建/修改/删除任何项目文件。\n"
    "1. 用 Read/Grep/Glob/run_cmd(只读) 深入探索代码库，理解现状与方案可行性。\n"
    "2. 用 tasks_update 把方案拆成有序子任务清单（每项一句话，同一时刻只一项 in_progress）。\n"
    "3. 有重大方向性选择时用 AskUser 向用户确认。\n"
    "4. 规划完成后调用 ExitPlanMode 向用户展示清单并确认；不要自行退出计划模式直接动手。"
)


class TurnReminders:
    """每轮便利贴收集器。state 是调用方跨 turn 持有的 dict。"""

    def __init__(self, state=None):
        # state 为 None 时用一次性 dict：本轮内部逻辑照常，跨 turn 记忆丢失（无状态退化）
        self.state = state if state is not None else {}

    # ── 环境信息 ────────────────────────────────────────────────

    def datetime_reminder(self):
        """当前日期时间。每轮都贴（per-turn 块允许变化），消除日期幻觉。"""
        now = datetime.now()
        return ("datetime", f"# currentDateTime\nToday is {now:%Y-%m-%d %H:%M} ({now:%A}).")

    def date_change_reminder(self):
        """跨天检测：首次只记录（bootstrap），日期变化才贴，且不回头改首条 context。"""
        today = date.today().isoformat()
        last = self.state.get("last_emitted_date")
        self.state["last_emitted_date"] = today
        if last is None or last == today:
            return None
        return (
            "date_change",
            f"The date has changed. Today's date is now {today}. "
            "Do not mention this to the user explicitly unless relevant.",
        )

    # ── 记忆系统状态 ────────────────────────────────────────────

    def memory_stats_reminder(self, stats):
        """记忆库规模 + persona 新鲜度，让模型知道"有多少记忆可查"，减少记忆幻觉。"""
        if not stats:
            return None
        lines = [f"L1 atoms: {stats.get('l1_atoms', 0)} · L2 scenes: {stats.get('l2_scenes', 0)}"]
        age = stats.get("persona_age_days")
        if age is None:
            lines.append("persona: not generated yet")
        else:
            lines.append(f"persona last updated {age:.0f} day(s) ago")
            if age >= _PERSONA_STALE_DAYS:
                lines.append(
                    "Persona may be stale — verify its claims against the current "
                    "conversation before asserting them."
                )
        lines.append(
            "If you need facts not present in the retrieved memories above, "
            "call memory_search with a keyword phrase before concluding they were never discussed."
        )
        return ("memory_stats", "# memory\n" + "\n".join(lines))

    # ── 计划模式指令 ────────────────────────────────────────────

    def plan_mode_reminder(self):
        """计划模式指令：state["plan_mode"] 为真时每轮贴出（只读规划引导）。"""
        if not self.state.get("plan_mode"):
            return None
        return ("plan_mode", PLAN_MODE_REMINDER)

    # ── 技能清单（full / sparse / delta 三态）──────────────────

    def skills_reminder(self, skill_items):
        """skill_items: [(name, xml), ...]。无技能返回 None。

        首轮全量（内容永久留在该消息上）；无变化一行稀疏（引用历史中的全量）；
        有增删只列增量。Blocking 指令每轮保留（轻量且关键）。
        """
        items = [(n, x) for n, x in (skill_items or []) if n and x]
        if not items:
            return None
        current = {n: x for n, x in items}
        announced = self.state.get("skills_announced")

        if announced is None:
            self.state["skills_announced"] = set(current)
            body = "\n".join(current.values())
            return (
                "skills",
                "The following skills are available for use with the Skill tool:\n\n"
                f"{body}\n\n{_SKILL_INSTRUCTION}",
            )

        announced = set(announced)
        if announced == set(current):
            return (
                "skills",
                f"Skill list unchanged ({len(current)} skills listed earlier in the "
                f"conversation). {_SKILL_INSTRUCTION}",
            )

        new = sorted(set(current) - announced)
        removed = sorted(announced - set(current))
        self.state["skills_announced"] = set(current)
        parts = []
        if new:
            parts.append(
                "New skills are now available:\n\n" + "\n".join(current[n] for n in new)
            )
        if removed:
            parts.append(
                "The following skills are no longer available:\n"
                + "\n".join(f"- {n}" for n in removed)
            )
        parts.append(_SKILL_INSTRUCTION)
        return ("skills", "\n\n".join(parts))
