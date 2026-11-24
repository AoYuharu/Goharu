"""Architect RepairGuard：快照 / 自检 / 精确回滚（临时真实 git 仓库）。"""

import subprocess
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


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    from Architect.repair import RepairGuard

    tmp = Path(tempfile.mkdtemp(prefix="repair_guard_test_"))
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@local")
    git(repo, "config", "user.name", "test")
    (repo / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")

    # 用户既有的脏修改 + 既有 untracked（快照必须保留它们）
    (repo / "a.py").write_text("VALUE = 2  # user dirty\n", encoding="utf-8")
    (repo / "pre.py").write_text("PRE = True\n", encoding="utf-8")

    guard = RepairGuard(repo)
    print("\n[1] 快照")
    snap = guard.snapshot()
    check("快照成功", snap.get("ok"), str(snap))
    check("含既有变更", "a.py" in (snap.get("before") or set()), str(snap.get("before")))

    print("\n[2] 修复后的变更检测与自检")
    (repo / "a.py").write_text("VALUE = 2  # repaired\n", encoding="utf-8")
    (repo / "b.py").write_text("B = 3\n", encoding="utf-8")
    changed = guard.changed_files()
    check("检测 a.py 与 b.py 变更", "a.py" in changed and "b.py" in changed, str(changed))
    check("合法代码自检通过", guard.verify().get("ok"), str(guard.verify()))

    print("\n[3] 失败回滚：恢复既有脏修改 + 删除修复新增文件")
    (repo / "a.py").write_text("VALUE = = broken\n", encoding="utf-8")
    (repo / "c.py").write_text("C = 4\n", encoding="utf-8")
    verify = guard.verify()
    check("坏代码自检失败", not verify.get("ok"), str(verify))
    restored = guard.restore(snap)
    check("回滚成功", restored.get("ok"), str(restored))
    check("恢复用户脏修改", (repo / "a.py").read_text(encoding="utf-8")
          == "VALUE = 2  # user dirty\n")
    check("保留既有 untracked", (repo / "pre.py").exists())
    check("删除修复新增 b.py", not (repo / "b.py").exists())
    check("删除修复新增 c.py", not (repo / "c.py").exists())
    status = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    check("工作区状态回到快照", "a.py" in status and "pre.py" in status
          and "b.py" not in status and "c.py" not in status, status)

    print("\n[4] 自检命令")
    guard2 = RepairGuard(repo, self_check_commands=["python -c \"raise SystemExit(1)\""])
    snap2 = guard2.snapshot()
    check("第二快照", snap2.get("ok"), str(snap2))
    (repo / "pre.py").write_text("PRE = False\n", encoding="utf-8")
    verify2 = guard2.verify()
    check("自定义自检失败被发现", not verify2.get("ok")
          and any("自检失败" in e for e in [verify2.get("error", "")]), str(verify2))
    guard2.restore(snap2)

    print("\n[5] 非 git 目录降级")
    plain = tmp / "plain"
    plain.mkdir()
    guard3 = RepairGuard(plain)
    check("非 git 返回失败", not guard3.snapshot().get("ok"))

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
