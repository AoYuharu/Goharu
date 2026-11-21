"""TurnReminders 便利贴引擎测试：datetime / date_change / memory_stats / skills 三态。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Prompting.reminders import TurnReminders

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


def main():
    print("[1] datetime 便利贴")
    eng = TurnReminders({})
    kind, text = eng.datetime_reminder()
    check("含日期与星期", date.today().isoformat() in text and "currentDateTime" in text, text)

    print("[2] date_change 节流")
    state = {}
    eng = TurnReminders(state)
    check("首次只 bootstrap 不贴", eng.date_change_reminder() is None)
    check("同一天不再贴", eng.date_change_reminder() is None)
    state["last_emitted_date"] = "2020-01-01"
    r = eng.date_change_reminder()
    check("跨天才贴", r is not None and "date has changed" in r[1], str(r))
    check("贴后更新状态", state["last_emitted_date"] == date.today().isoformat())

    print("[3] memory_stats 便利贴")
    eng = TurnReminders({})
    check("无 stats 不贴", eng.memory_stats_reminder(None) is None)
    kind, text = eng.memory_stats_reminder({"l1_atoms": 12, "l2_scenes": 3, "persona_age_days": 0.5})
    check("含规模与 persona 年龄", "L1 atoms: 12" in text and "0 day(s) ago" in text, text)
    check("新鲜 persona 无过期提醒", "stale" not in text)
    check("含 memory_search 引导", "memory_search" in text)
    kind, text = eng.memory_stats_reminder({"l1_atoms": 1, "l2_scenes": 0, "persona_age_days": 5.2})
    check("过期 persona 有核实提醒", "stale" in text and "verify" in text.lower(), text)
    kind, text = eng.memory_stats_reminder({"l1_atoms": 0, "l2_scenes": 0, "persona_age_days": None})
    check("persona 未生成", "not generated" in text, text)

    print("[4] 技能清单三态")
    items = [("alpha", '<skill name="alpha">\nA\n</skill>'), ("beta", '<skill name="beta">\nB\n</skill>')]
    state = {}
    eng = TurnReminders(state)
    kind, text = eng.skills_reminder(items)
    check("首轮全量", "available for use" in text and "alpha" in text and "beta" in text, text[:80])
    kind, text = eng.skills_reminder(items)
    check("无变化→稀疏", "unchanged" in text and "alpha" not in text, text[:120])
    check("稀疏保留 blocking 指令", "BLOCKING REQUIREMENT" in text)
    items2 = items + [("gamma", '<skill name="gamma">\nG\n</skill>')]
    kind, text = eng.skills_reminder(items2)
    check("新增→只列增量", "New skills" in text and "gamma" in text and "alpha" not in text, text[:120])
    kind, text = eng.skills_reminder(items)
    check("移除→列移除名单", "no longer available" in text and "- gamma" in text, text[:200])
    check("空列表不贴", eng.skills_reminder([]) is None)

    print("[5] 无状态退化（state=None）")
    # 无状态的语义：每轮新建引擎（assembler 每轮 new TurnReminders(reminder_state)），
    # state=None 时内部 dict 随引擎销毁 → 跨轮无记忆 → 每轮全量
    TurnReminders(None).skills_reminder(items)
    kind, text = TurnReminders(None).skills_reminder(items)
    check("无状态时每轮全量", "available for use" in text, text[:80])
    check("无状态时 date_change 不触发", TurnReminders(None).date_change_reminder() is None)

    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
