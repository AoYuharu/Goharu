"""loop_guard 重复调用检测测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Tools.loop_guard import LoopGuard

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


def fresh_guard():
    g = LoopGuard()
    g._reset()
    return g


def main():
    print("[1] 同一调用连续重复")
    g = fresh_guard()
    r1 = g.after_tool_call("browser_navigate", {"url": "http://x"}, "ok")
    r2 = g.after_tool_call("browser_navigate", {"url": "http://x"}, "ok")
    r3 = g.after_tool_call("browser_navigate", {"url": "http://x"}, "ok")
    check("前 2 次不警告", r1 is None and r2 is None)
    check("第 3 次警告", r3 is not None and "循环警告" in r3, str(r3)[:60])
    r4 = g.after_tool_call("browser_navigate", {"url": "http://x"}, "ok")
    check("第 4 次冷却不重复警告", r4 is None, str(r4)[:60])

    print("[2] 参数不同不算重复")
    g = fresh_guard()
    warns = [
        g.after_tool_call("browser_navigate", {"url": f"http://x/{i}"}, "ok")
        for i in range(4)
    ]
    check("不同 URL 不警告", all(w is None for w in warns))

    print("[3] 序列模式重复（A→B→C × 3）")
    g = fresh_guard()
    warn = None
    for _ in range(3):
        for name, args in (("navigate", {"url": "u"}), ("Sleep", {"ms": 3000}), ("get_text", {})):
            warn = g.after_tool_call(name, args, "ok") or warn
            if warn and "操作流程" in warn:
                break
    check("3 轮模式后警告", warn is not None and "重复了 3 轮" in warn, str(warn)[:80])

    print("[4] 正常多样序列不警告")
    g = fresh_guard()
    warns = []
    for i in range(10):
        warns.append(g.after_tool_call(f"tool_{i % 3}", {"i": i}, "ok"))
    check("无警告", all(w is None for w in warns))

    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
