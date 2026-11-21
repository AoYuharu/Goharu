"""test_verify_verdict.py - verify 子代理 VERDICT 解析与报告完整性校验的单元测试

直接运行: python test_verify_verdict.py
覆盖:
  - Agent.SubAgent.SubAgent.parse_verdict（VERDICT 机器解析）
  - Tools.builtin.agent_delegate._audit_verify_report（报告机械完整性校验）
"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
from Agent.SubAgent import SubAgent
from Tools.builtin.agent_delegate import _audit_verify_report

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name: str, actual, expected):
    global PASS_COUNT, FAIL_COUNT
    if actual == expected:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}\n        expected={expected!r}\n        actual  ={actual!r}")


# ══════ parse_verdict ══════
print("== parse_verdict ==")

check("标准 PASS", SubAgent.parse_verdict("一些报告内容\nVERDICT: PASS"), "PASS")
check("标准 FAIL", SubAgent.parse_verdict("报告\nVERDICT: FAIL\n"), "FAIL")
check("标准 PARTIAL", SubAgent.parse_verdict("报告\nVERDICT: PARTIAL"), "PARTIAL")
check("缺失返回 None", SubAgent.parse_verdict("报告里没有判定行"), None)
check("空内容返回 None", SubAgent.parse_verdict(""), None)
check("None 输入返回 None", SubAgent.parse_verdict(None), None)
check("容忍 markdown 加粗", SubAgent.parse_verdict("**VERDICT: PASS**"), "PASS")
check("小写容忍", SubAgent.parse_verdict("verdict: fail"), "FAIL")
check("行尾空格容忍", SubAgent.parse_verdict("VERDICT: PARTIAL   "), "PARTIAL")
check(
    "多个匹配取最后一个（正文引用示例行不应覆盖真实判定）",
    SubAgent.parse_verdict("格式说明：VERDICT: PASS\n\n实际结论：\nVERDICT: FAIL"),
    "FAIL",
)
check(
    "冒号后紧跟文字不匹配（VERDICT: PASSED 不算）",
    SubAgent.parse_verdict("VERDICT: PASSED"),
    None,
)

# ══════ _audit_verify_report ══════
print("== _audit_verify_report ==")

GOOD_REPORT = """
### Check: 短密码被拒绝
**Command run:**
  curl -s -X POST localhost:8000/api/register -d '{"password":"short"}'
**Output observed:**
  {"error": "password must be at least 8 characters"}
**Result: PASS**

### Check: 并发创建不重复
**Command run:**
  python test_concurrent.py
**Output observed:**
  10 requests -> 1 created
**Result: PASS**

VERDICT: PASS
"""

check("完整报告无问题", _audit_verify_report(GOOD_REPORT), [])

NO_COMMAND = """
### Check: 路由校验逻辑
**Result: PASS**
Evidence: 阅读了 routes/auth.py，逻辑正确。

### Check: 边界值空字符串
**Command run:**
  curl -s -X POST localhost:8000/api -d ''
**Output observed:**
  400
**Result: PASS**

VERDICT: PASS
"""

problems = _audit_verify_report(NO_COMMAND)
check(
    "PASS 无 Command run 被检出",
    any("Command run" in p for p in problems),
    True,
)

NO_VERDICT = """
### Check: 接口返回 200
**Command run:**
  curl x
**Output observed:**
  ok
**Result: PASS**
"""
problems = _audit_verify_report(NO_VERDICT)
check("缺 VERDICT 被检出", any("VERDICT" in p for p in problems), True)
check("缺对抗性探测被检出", any("对抗" in p for p in problems), True)

check("空报告被检出", len(_audit_verify_report("")) > 0, True)

# FAIL 判定不要求 Command run 以外的额外结构，但仍需对抗性探测
FAIL_REPORT = """
### Check: 服务启动
**Command run:**
  python server.py
**Output observed:**
  Traceback: ImportError
**Result: FAIL**
Expected: 服务启动
Actual: ImportError

边界值探测：输入 0 正常拒绝。

VERDICT: FAIL
"""
check("FAIL 报告（含边界探测）无结构问题", _audit_verify_report(FAIL_REPORT), [])

print(f"\n结果: {PASS_COUNT} passed, {FAIL_COUNT} failed")
raise SystemExit(1 if FAIL_COUNT else 0)
