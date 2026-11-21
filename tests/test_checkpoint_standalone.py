"""Checkpoint 子系统独立契约测试。

覆盖方案测试用例 1-12、14（用例 13 Memory 集成在 Phase 3 后补入本文件）：
factory 装配 / hook emit / Edit 语义 / 单 checkpoint 回退 / 链式倒带 / redo /
git 兜底对账 / 排除规则 / 冲突检测 / 无 git 降级 / retention GC / 并发烟测 / pending_ops。

运行：python tests/test_checkpoint_standalone.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Checkpoint.api import create_checkpoint_manager

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


def make_manager(root: Path, **cfg):
    config = {
        "checkpoint.dir": str(root / "checkpoints"),
        "checkpoint.git_baseline_enabled": cfg.pop("git", True),
        "checkpoint.max_count": cfg.pop("max_count", 200),
        "checkpoint.retention_days": cfg.pop("retention_days", 30),
        "checkpoint.max_total_mb": cfg.pop("max_total_mb", 500),
        "checkpoint.rollback_on_conflict": cfg.pop("on_conflict", "warn"),
        "checkpoint.exclude": ["runtime_memory/**", "**/__pycache__/**", "*.pyc", ".git/**"],
    }
    return create_checkpoint_manager(config=config, memory=cfg.pop("memory", None),
                                     repo_root=str(root))


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(root: Path, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, env=GIT_ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_manifest(mgr, seq):
    for m in mgr._storage.list_manifests():
        if m.get("seq") == seq:
            return m
    return None


def nkey(p) -> str:
    """与 Checkpoint 内部 _norm 一致的路径键（Windows normcase 小写化）。"""
    return os.path.normcase(str(Path(p).expanduser().resolve()))


def main():
    root = Path(tempfile.mkdtemp(prefix="ckpt_test_"))
    try:
        # ---------------------------------------------------------- 1. 装配
        print("\n[1] factory 装配")
        mgr = make_manager(root, git=False)
        check("manager 创建", mgr is not None)
        check("dir 派生", (root / "checkpoints").is_dir())

        # ------------------------------------- 2/3. hook emit + Edit 语义
        print("\n[2/3] hook emit 与 Edit 语义")
        f_mod = root / "a.txt"
        f_mod.write_bytes(b"v1-content")
        f_new = root / "b.txt"
        f_del = root / "c.txt"
        f_del.write_bytes(b"to-be-deleted")

        seq1 = mgr.begin_turn("s1", 1)
        check("begin_turn 返回 seq", seq1 == 1, f"seq={seq1}")
        # Edit a.txt: v1 -> v2
        mgr.on_file_event(str(f_mod), "modify", b"v1-content", b"v2-content", "hook:Edit")
        f_mod.write_bytes(b"v2-content")
        # Write b.txt（create）
        mgr.on_file_event(str(f_new), "create", None, b"new-file", "hook:Write")
        f_new.write_bytes(b"new-file")
        # delete c.txt
        mgr.on_file_event(str(f_del), "delete", b"to-be-deleted", None, "hook:scene")
        f_del.unlink()
        summary = mgr.end_turn("answer")
        check("end_turn 摘要", summary and summary["files"] == 3 and summary["l1_ops"] == 0,
              str(summary))
        m1 = read_manifest(mgr, seq1)
        ops = {f["path"]: f for f in m1["files"]}
        check("modify op 旧内容完整", ops[nkey(f_mod)]["old"]["size"] == len(b"v1-content"))
        check("create op 无 old", ops[nkey(f_new)]["old"]["present"] is False)
        check("delete op 有 old", ops[nkey(f_del)]["old"]["present"] is True)
        check("outcome 记录", m1["outcome"] == "answer")

        # ------------------------------------------ 4. 单 checkpoint 回退
        print("\n[4] 单 checkpoint 回退")
        res = mgr.rollback_to(0)  # 撤销 cp1 全部
        check("回退成功", res["ok"], str(res))
        check("modify 恢复原文", f_mod.read_bytes() == b"v1-content")
        check("create 被删除", not f_new.exists())
        check("delete 被复原", f_del.read_bytes() == b"to-be-deleted")

        # --------------------------------------------------- 5. 链式倒带
        print("\n[5] 三 checkpoint 链式倒带")
        mgr2root = root / "chain"
        mgr2root.mkdir()
        mgr2 = make_manager(mgr2root, git=False)
        f1 = mgr2root / "f1.txt"
        f2 = mgr2root / "f2.txt"
        # turn1: create f1=v1
        mgr2.begin_turn("s", 1)
        mgr2.on_file_event(str(f1), "create", None, b"v1", "hook:Write")
        f1.write_bytes(b"v1")
        mgr2.end_turn("answer")
        # turn2: modify f1 v1->v2, create f2
        mgr2.begin_turn("s", 2)
        mgr2.on_file_event(str(f1), "modify", b"v1", b"v2", "hook:Edit")
        f1.write_bytes(b"v2")
        mgr2.on_file_event(str(f2), "create", None, b"f2", "hook:Write")
        f2.write_bytes(b"f2")
        mgr2.end_turn("answer")
        # turn3: modify f1 v2->v3
        mgr2.begin_turn("s", 3)
        mgr2.on_file_event(str(f1), "modify", b"v2", b"v3", "hook:Edit")
        f1.write_bytes(b"v3")
        mgr2.end_turn("answer")
        res = mgr2.rollback_to(1)  # 回到 turn1 结束时刻
        check("链式回退 ok", res["ok"], str(res))
        check("f1 回到 v1", f1.read_bytes() == b"v1", f1.read_bytes())
        check("f2 被删除", not f2.exists())

        # ------------------------------------------------- 6. redo
        print("\n[6] 回退自身可回退（redo）")
        # [5] 的回退生成了 rollback checkpoint（seq=4）；撤销它 = rollback_to(3)
        res2 = mgr2.rollback_to(3)
        check("redo 回退 ok", res2["ok"], str(res2))
        check("redo 后 f1 回到 v3", f1.read_bytes() == b"v3", f1.read_bytes())
        check("redo 后 f2 恢复", f2.exists() and f2.read_bytes() == b"f2")

        # ------------------------------------------------- 7. git 兜底
        print("\n[7] git 对账兜底（绕过挂钩的写入）")
        groot = root / "gitrepo"
        groot.mkdir()
        git(groot, "init")
        tracked = groot / "tracked.txt"
        tracked.write_text("base", encoding="utf-8")
        git(groot, "add", ".")
        git(groot, "commit", "-m", "init")
        mgr3 = make_manager(groot, git=True)
        mgr3.begin_turn("s", 1)
        # 绕过挂钩直接写盘（模拟 run_cmd python 写文件）
        tracked.write_text("modified", encoding="utf-8")
        (groot / "sneaky.txt").write_text("new", encoding="utf-8")
        summary3 = mgr3.end_turn("answer")
        check("对账捕获数", summary3["git_caught"] == 2, str(summary3))
        m3 = read_manifest(mgr3, 1)
        git_ops = {f["rel_path"]: f for f in m3["files"] if f["source"] == "git"}
        check("tracked 为 modify", git_ops["tracked.txt"]["op"] == "modify")
        check("新建为 create", git_ops["sneaky.txt"]["op"] == "create")
        res3 = mgr3.rollback_to(0)
        check("git 兜底回退 ok", res3["ok"], str(res3))
        check("tracked 恢复 base", tracked.read_text(encoding="utf-8") == "base")
        check("sneaky 被删", not (groot / "sneaky.txt").exists())

        # ------------------------------------------------- 8. 排除规则
        print("\n[8] 排除规则")
        pycache = groot / "__pycache__"
        pycache.mkdir(exist_ok=True)
        mgr3.begin_turn("s", 2)
        (pycache / "x.pyc").write_bytes(b"pyc")
        summary4 = mgr3.end_turn("answer")
        check("__pycache__ 不入账", summary4["files"] == 0, str(summary4))

        # ------------------------------------------------- 9. 冲突检测
        print("\n[9] 冲突检测（checkpoint 后外部修改）")
        croot = root / "conflict"
        croot.mkdir()
        cf = croot / "f.txt"
        cf.write_bytes(b"v1")
        mgr4 = make_manager(croot, git=False, on_conflict="abort")
        mgr4.begin_turn("s", 1)
        mgr4.on_file_event(str(cf), "modify", b"v1", b"v2", "hook:Edit")
        cf.write_bytes(b"v2")
        mgr4.end_turn("answer")
        cf.write_bytes(b"HACK")  # 外部修改
        res4 = mgr4.rollback_to(0)
        check("abort 策略中止", res4["ok"] is False and res4.get("conflicts"), str(res4))
        check("abort 未动文件", cf.read_bytes() == b"HACK")
        mgr5 = make_manager(croot, git=False, on_conflict="warn")
        res5 = mgr5.rollback_to(0)
        check("warn 策略继续", res5["ok"] and res5.get("conflicts"), str(res5))
        check("warn 后文件被恢复", cf.read_bytes() == b"v1")

        # ------------------------------------------------- 10. 无 git 降级
        print("\n[10] 无 git 降级")
        ngroot = root / "nogit"
        ngroot.mkdir()
        mgr6 = make_manager(ngroot, git=True)  # git 开启但目录非仓库
        mgr6.begin_turn("s", 1)
        (ngroot / "x.txt").write_bytes(b"x")
        mgr6.on_file_event(str(ngroot / "x.txt"), "create", None, b"x", "hook:Write")
        m6sum = mgr6.end_turn("answer")
        check("降级不炸", m6sum is not None)
        m6 = read_manifest(mgr6, 1)
        check("degraded 标记", m6["git"]["degraded"] is True, str(m6["git"]))

        # ------------------------------------------------- 11. retention
        print("\n[11] retention 裁剪 + blob GC")
        rroot = root / "retention"
        rroot.mkdir()
        mgr7 = make_manager(rroot, git=False, max_count=2)
        for i in range(1, 5):
            mgr7.begin_turn("s", i)
            fp = rroot / f"f{i}.txt"
            fp.write_bytes(f"content-{i}".encode())
            mgr7.on_file_event(str(fp), "create", None, f"content-{i}".encode(), "hook:Write")
            mgr7.end_turn("answer")
        remaining = [m["seq"] for m in mgr7._storage.list_manifests()]
        check("只保留最新 2 个", remaining == [3, 4], str(remaining))

        # ------------------------------------------------- 12. 并发烟测
        print("\n[12] 并发烟测")
        (root / "conc").mkdir()
        mgr8 = make_manager(root / "conc", git=False)
        errors = []

        def emitter(n):
            try:
                for i in range(20):
                    p = root / "conc" / f"t{n}_{i}.txt"
                    mgr8.on_file_event(str(p), "create", None, b"x", "hook:Write")
            except Exception as e:
                errors.append(e)

        mgr8.begin_turn("s", 1)
        threads = [threading.Thread(target=emitter, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        rb_errors = []

        def lister():
            try:
                for _ in range(10):
                    mgr8.list()
            except Exception as e:
                rb_errors.append(e)

        lt = threading.Thread(target=lister)
        lt.start()
        for t in threads:
            t.join()
        lt.join()
        mgr8.end_turn("answer")
        check("并发无异常", not errors and not rb_errors, f"{errors} {rb_errors}")
        m8 = read_manifest(mgr8, 1)
        check("160 条 op 完整", len(m8["files"]) == 160, str(len(m8["files"])))

        # ------------------------------------------------- 14. pending_ops
        print("\n[14] turn 间隙事件并入下一 checkpoint")
        mgr9 = make_manager(root / "pending", git=False)
        (root / "pending_target.txt").write_bytes(b"p")
        mgr9.on_file_event(str(root / "pending_target.txt"), "create", None, b"p", "hook:scene")
        mgr9.begin_turn("s", 1)
        mgr9.end_turn("answer")
        m9 = read_manifest(mgr9, 1)
        check("间隙事件并入", len(m9["files"]) == 1 and m9["files"][0]["source"] == "hook:scene",
              str(m9["files"]))

        # ------------------------------------------------- 13. Memory 集成
        print("\n[13] Memory 集成（L1 原子 + 三表同步回退）")
        from Memory.bridge import create_memory
        from Memory import change_events

        mem_root = root / "mem"
        bridge = create_memory(overrides={
            "memory.root_dir": str(mem_root),
            # 空目录让 embedding/reranker 快速降级，不加载模型
            "memory.retrieval.embedding_model_path": str(mem_root / "no_model"),
            "memory.retrieval.reranker_model_path": str(mem_root / "no_reranker"),
        })
        try:
            mgr10 = make_manager(mem_root, git=False, memory=bridge)
            change_events.set_l1_listener(
                lambda op, aid, old, new: mgr10.on_memory_event("l1", op, aid, old, new))

            from Memory.repositories.L1Repository import L1Repository
            repo = L1Repository(bridge.db)
            mgr10.begin_turn("s", 1)
            atom = repo.upsert_atom("fact", "用户喜欢咖啡", subject="user")
            aid = atom["id"]
            repo.upsert_atom("fact", "用户喜欢黑咖啡", subject="user", atom_id=aid)
            m10sum = mgr10.end_turn("answer")
            check("L1 变更入账", m10sum["l1_ops"] == 2, str(m10sum))
            m10 = read_manifest(mgr10, 1)
            ops10 = m10["memory"]["l1"]
            check("insert 无 old_row", ops10[0]["op"] == "insert" and ops10[0]["old_row"] is None)
            check("update 带旧值", ops10[1]["op"] == "update"
                  and ops10[1]["old_row"]["canonical_text"] == "用户喜欢咖啡")

            # FTS 检索验证（回退前）
            hits = bridge.db.query_all(
                "SELECT source_id FROM fts_memory WHERE record_type='l1' AND content MATCH ?",
                ("咖啡",))
            check("FTS 命中新文本", len(hits) == 1)

            res13 = mgr10.rollback_to(0)
            check("L1 回退 ok", res13["ok"], str(res13))
            check("insert 逆操作物理删除", repo.get_atom(aid) is None)
            hits = bridge.db.query_all(
                "SELECT source_id FROM fts_memory WHERE record_type='l1' AND content MATCH ?",
                ("咖啡",))
            check("FTS 侧表同步清除", len(hits) == 0)

            # redo：撤销 rollback checkpoint → atom 恢复为 update 后状态
            res13b = mgr10.rollback_to(1)
            check("L1 redo ok", res13b["ok"], str(res13b))
            restored = repo.get_atom(aid)
            check("redo 恢复 atom", restored is not None
                  and restored["canonical_text"] == "用户喜欢黑咖啡", str(restored))
        finally:
            change_events.set_l1_listener(None)
            bridge.close()

        # dry_run
        print("\n[附加] dry_run")
        res_dr = mgr2.rollback_to(0, dry_run=True)
        check("dry_run 不动文件", res_dr.get("dry_run") and f1.read_bytes() == b"v3", str(res_dr))

    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
