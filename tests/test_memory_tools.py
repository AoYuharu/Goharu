"""memory_tools 独立测试：直接调 handler 验证 memory_search / memory_add。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Memory.MemoryManager import MemoryManager
from Tools.builtin import memory_tools

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mem_tools_test_"))
    empty_model_dir = tmp / "no_model"
    empty_model_dir.mkdir()
    try:
        cfg = {
            "memory.root_dir": str(tmp / "mem"),
            "memory.sqlite.path": str(tmp / "mem" / "memory.db"),
            "memory.daily.dir": str(tmp / "mem" / "daily"),
            "memory.scene_blocks.dir": str(tmp / "mem" / "scene_blocks"),
            "memory.persona.path": str(tmp / "mem" / "persona.md"),
            # 空目录：embedding 立即降级，避免加载模型
            "memory.retrieval.embedding_model_path": str(empty_model_dir),
        }
        mgr = MemoryManager(cfg=cfg)
        memory_tools.set_memory_manager(mgr)

        print("[1] memory_add")
        r = json.loads(memory_tools.memory_add(
            atom_type="preference", subject="user_coffee",
            canonical_text="用户喜欢冰美式咖啡",
        ))
        check("写入成功", r.get("status") == "success" and r.get("atom_id"), str(r))

        r_dup = json.loads(memory_tools.memory_add(
            atom_type="preference", subject="user_coffee",
            canonical_text="用户喜欢冰美式咖啡",
        ))
        check("重复内容被去重", r_dup.get("status") == "duplicate", str(r_dup))

        r_long = json.loads(memory_tools.memory_add(
            atom_type="fact", subject="x", canonical_text="长" * 201,
        ))
        check("超长被拒（>200字）", r_long.get("status") == "error", str(r_long))

        r_empty = json.loads(memory_tools.memory_add(
            atom_type="fact", subject="x", canonical_text="",
        ))
        check("空内容被拒", r_empty.get("status") == "error", str(r_empty))

        print("[2] memory_search")
        r_search = json.loads(memory_tools.memory_search("冰美式 咖啡"))
        check("检索成功", r_search.get("status") == "success", str(r_search))
        texts = [m.get("text", "") for m in r_search.get("memories", [])]
        check("命中刚写入的原子", any("冰美式" in t for t in texts), str(texts))

        r_noq = json.loads(memory_tools.memory_search(""))
        check("空 query 被拒", r_noq.get("status") == "error", str(r_noq))

        print("[3] 未注入时的降级")
        memory_tools.set_memory_manager(None)
        r_nomgr = json.loads(memory_tools.memory_search("咖啡"))
        check("无管理器时返回 error 而非崩溃", r_nomgr.get("status") == "error")

        mgr.hybrid_retriever.db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
