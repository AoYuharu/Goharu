"""Core/error_report 统一错误记录契约测试。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="error_report_test_"))
    os.environ["GOHARU_ERRORS_PATH"] = str(tmp / "errors.jsonl")
    import Core.error_report as er

    print("\n[1] 记录与 fingerprint")
    r1 = er.report_error(
        "LLM 429 quota exceeded request_id=abc12345",
        scope="llm", blocking="all_turns", cause="config",
        source="Agent.LLMCore", session="default", retryable=True)
    check("记录字段齐全",
          r1.get("id") and r1.get("fingerprint") and r1.get("blocking") == "all_turns"
          and r1.get("cause") == "config" and r1.get("status") == "new"
          and r1.get("wake_status") == "none", str(r1))
    check("JSONL 已落盘", er.errors_path().is_file()
          and er.errors_path().read_text(encoding="utf-8").strip().count("\n") == 0)
    r2 = er.report_error(
        "LLM 402 quota exceeded request_id=def67890",
        scope="llm", blocking="all_turns", cause="config", source="Agent.LLMCore")
    check("归一化连接同一 fingerprint", r1["fingerprint"] == r2["fingerprint"],
          f"{r1['fingerprint']} vs {r2['fingerprint']}")

    print("\n[2] 更新与归并")
    er.update_error(r1["id"], status="woken", wake_status="repairing", attempts=1)
    latest = er.load_error(r1["id"])
    check("更新字段生效", latest["status"] == "woken"
          and latest["wake_status"] == "repairing" and latest["attempts"] == 1, str(latest))
    all_records = er.iter_errors()
    check("同 id 归并最新态", len([x for x in all_records if x["id"] == r1["id"]]) == 1)

    print("\n[3] listener 隔离与通知")
    seen = []
    unsub = er.register_error_listener(lambda rec: seen.append((rec["id"], rec["wake_status"])))
    er.register_error_listener(lambda rec: (_ for _ in ()).throw(RuntimeError("bad listener")))
    r3 = er.report_error("tool failed", scope="tool", blocking="request")
    check("报告通知 listener", any(i == r3["id"] for i, _ in seen), str(seen))
    er.update_error(r3["id"], wake_status="woken")
    check("更新也通知 listener", any(i == r3["id"] and s == "woken" for i, s in seen), str(seen))
    unsub()
    count = len(seen)
    er.report_error("after unsubscribe", scope="tool")
    check("注销后不再通知", len(seen) == count, f"{count}/{len(seen)}")

    print("\n[4] 早期致命与 pending 扫描")
    fatal = er.report_fatal_early("startup failed", cause="config", source="gateway")
    check("致命记录标记", fatal["blocking"] == "fatal" and fatal["severity"] == "fatal")
    fatal2 = er.report_fatal_early("startup failed", cause="config", source="gateway")
    check("致命错误窗口内去重", fatal2["id"] == fatal["id"]
          and int(fatal2.get("attempts") or 0) == 1, str(fatal2))
    pend = er.pending_fatals(max_attempts=3)
    check("pending 含致命记录", any(x["id"] == fatal["id"] for x in pend))
    er.update_error(fatal["id"], wake_status="wake_requested", attempts=3)
    check("超次数后不再 pending",
          not any(x["id"] == fatal["id"] for x in er.pending_fatals(max_attempts=3)))

    print("\n[5] 边界：Core 不依赖宿主")
    import ast

    tree = ast.parse((ROOT / "Core" / "error_report.py").read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = [m for m in imported
                 if m.split(".")[0] in ("Gateway", "AgentNet", "Architect", "Tools")]
    check("无 Gateway/AgentNet/Architect/Tools import", not forbidden, str(forbidden))

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    os.environ.pop("GOHARU_ERRORS_PATH", None)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
