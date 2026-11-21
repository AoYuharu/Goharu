"""Memory 子系统独立接口测试 —— 外部 harness 视角。

不经 Gateway/MemoryManager，直接用 Memory.bridge 的 create_memory 门面验证：
- create_memory(overrides=dict) 装配与 root_dir 子路径派生
- append / get_context / clear（L0 工作记忆，双写 SQLite + daily JSON）
- ingest_atoms：显式原子 + 启发式原子（均无需 LLM）
- retrieve：混合检索返回结构（embedding 走快速降级，纯 FTS5+BM25）
- extract_scenes / generate_persona：无 LLM / 注入假 LLM 时的优雅行为
- 落盘验证：memory.db、daily JSON

运行：python test_memory_standalone.py
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Memory.bridge import create_memory

TMP = Path("runtime_memory/temp/memory_standalone_test")
MEM_ROOT = TMP / "mem"
EMPTY_MODEL_DIR = TMP / "empty_model"  # 存在但非法的模型目录 → embedding 快速降级

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


class FakeLLM:
    """最小 llm 注入契约：query(messages) -> str；replies 队列依次返回。"""

    def __init__(self, reply="", replies=None):
        self.reply = reply
        self.replies = list(replies or [])
        self.calls = 0

    def query(self, messages):
        self.calls += 1
        if self.replies:
            return self.replies.pop(0)
        return self.reply


def main():
    print("=" * 60)
    print("Memory 独立接口测试（MemoryBridge / create_memory）")
    print("=" * 60)

    shutil.rmtree(TMP, ignore_errors=True)
    EMPTY_MODEL_DIR.mkdir(parents=True)

    # ------------------------------------------------------------------
    # 1. 装配与 root_dir 派生
    # ------------------------------------------------------------------
    print("\n[1] create_memory 装配")
    memory = create_memory(
        overrides={
            "memory.root_dir": str(MEM_ROOT),
            "memory.retrieval.embedding_model_path": str(EMPTY_MODEL_DIR),
            "memory.retrieval.use_reranker": False,
        },
        llm=FakeLLM(reply=""),  # 注入假 LLM：避免仓库内回退到真实 Agent 层
        session_id="standalone-test",
    )
    check("create_memory 返回桥接对象", memory is not None)
    check("root_dir 派生 sqlite 路径", str(MEM_ROOT) in str(memory.db.path))
    check("root_dir 派生 daily 目录", str(MEM_ROOT) in str(memory.working.daily_dir))

    # ------------------------------------------------------------------
    # 2. L0 写路径：append / get_context
    # ------------------------------------------------------------------
    print("\n[2] append / get_context")
    memory.append({"role": "user", "content": "我喜欢喝美式咖啡，尤其是冰的"})
    memory.append({"role": "assistant", "content": "好的，记住了：冰美式。"})
    memory.append({"role": "user", "content": "我周五下午要做一个关于时间序列的报告"})
    ctx = memory.get_context()
    check("get_context 返回 3 条", len(ctx) == 3, f"got {len(ctx)}")
    check("消息带 id 与时间戳",
          all(m.get("id") and m.get("timestamp") for m in ctx), str(ctx[:1]))
    check("内容完整", ctx[0]["content"] == "我喜欢喝美式咖啡，尤其是冰的")

    # ------------------------------------------------------------------
    # 3. 落盘验证
    # ------------------------------------------------------------------
    print("\n[3] 落盘")
    check("SQLite memory.db 存在", (MEM_ROOT / "memory.db").exists())
    daily_files = list((MEM_ROOT / "daily").glob("*.json"))
    check("daily JSON 落盘", len(daily_files) == 1, str(daily_files))

    # ------------------------------------------------------------------
    # 4. L1 原子入库（显式 payload + 启发式），无需 LLM
    # ------------------------------------------------------------------
    print("\n[4] ingest_atoms（无 LLM）")
    ingested = memory.ingest_atoms([
        {"atom_type": "fact", "subject": "用户", "canonical_text": "用户喜欢喝冰美式咖啡"},
        {"atom_type": "event", "subject": "用户", "canonical_text": "用户周五下午要做时间序列报告"},
    ])
    check("显式原子入库 2 条", len(ingested) == 2, str(ingested))

    heuristic = memory.ingest_atoms(None)  # None → 启发式提取当日消息
    check("启发式原子提取不报错", isinstance(heuristic, list), str(heuristic))

    # ------------------------------------------------------------------
    # 5. 检索读路径（纯 FTS5+BM25，embedding 已降级）
    # ------------------------------------------------------------------
    print("\n[5] retrieve")
    pack = memory.retrieve("用户喜欢什么咖啡")
    check("返回结构完整",
          isinstance(pack, dict)
          and {"profile_summary", "memories", "scenes"} <= set(pack),
          str(pack)[:200])
    hits = pack["memories"]
    check("FTS5 命中咖啡原子",
          any("咖啡" in (m.get("text") or "") for m in hits), str(hits)[:300])

    pack2 = memory.retrieve()  # query=None → 从最近工作记忆自动构建查询
    check("自动构建查询不报错", isinstance(pack2, dict))

    pack_alias = memory.retrieve_prompt_memory()  # ActorAgent 兼容别名
    check("retrieve_prompt_memory 别名结构一致",
          isinstance(pack_alias, dict)
          and {"profile_summary", "memories", "scenes"} <= set(pack_alias))

    # ------------------------------------------------------------------
    # 6. L2/L3：注入假 LLM 的优雅行为（假 LLM 返回空串 → 优雅空结果）
    # ------------------------------------------------------------------
    print("\n[6] L2/L3 优雅行为")
    scenes = memory.extract_scenes()  # 有未分配原子 → 会调用注入的 llm
    check("假 LLM 空回复 → extract_scenes 返回 []", scenes == [], str(scenes))

    persona = memory.generate_persona()
    check("无活跃场景时 generate_persona 返回 None（不调 LLM）", persona is None,
          str(persona)[:200])
    check("get_persona 无文件返回 None", memory.get_persona() is None)

    # ------------------------------------------------------------------
    # 6b. L2/L3 成功路径：假 LLM 返回合法输出，验证完整写入链
    # ------------------------------------------------------------------
    print("\n[6b] L2/L3 成功路径（假 LLM 合法输出）")
    MEM2_ROOT = TMP / "mem2"
    persona_md = "# 用户画像\n\n- 喜欢冰美式咖啡\n- 周五下午有时间序列报告"
    fake = FakeLLM()
    memory2 = create_memory(
        overrides={
            "memory.root_dir": str(MEM2_ROOT),
            "memory.retrieval.embedding_model_path": str(EMPTY_MODEL_DIR),
            "memory.retrieval.use_reranker": False,
        },
        llm=fake,
        session_id="standalone-test-2",
    )
    memory2.append({"role": "user", "content": "我喜欢喝冰美式咖啡"})
    atoms2 = memory2.ingest_atoms([
        {"atom_type": "fact", "subject": "用户", "canonical_text": "用户喜欢喝冰美式咖啡"}
    ])
    atom_id = atoms2[0].get("id") if atoms2 and isinstance(atoms2[0], dict) else None
    check("L1 原子带 id 返回", atom_id is not None, str(atoms2)[:200])

    # 假 LLM 队列：第一次 query 返回场景 JSON，第二次返回画像 markdown
    scene_json = json.dumps([{
        "slug": "coffee-preference", "title": "咖啡偏好",
        "keywords": ["咖啡", "美式"], "importance": 0.7,
        "atom_ids": [atom_id],
        "body": "用户喜欢冰美式咖啡，尤其偏爱冰的。",
    }], ensure_ascii=False)
    fake.replies = [scene_json, persona_md]

    slugs = memory2.extract_scenes()
    check("extract_scenes 成功创建场景", slugs == ["coffee-preference"], str(slugs))
    check("场景文件落盘",
          (MEM2_ROOT / "scene_blocks" / "coffee-preference.md").exists())

    pack3 = memory2.retrieve("咖啡")
    check("L2 场景可被检索到",
          any("咖啡" in (s.get("text") or "") or "咖啡" in (s.get("title") or "")
              for s in pack3["scenes"]),
          str(pack3["scenes"])[:200])

    persona2 = memory2.generate_persona()
    check("generate_persona 成功路径返回画像", persona2 == persona_md,
          str(persona2)[:200])
    check("get_persona 读回 persona.md", memory2.get_persona() == persona_md)
    check("persona.md 落盘", (MEM2_ROOT / "persona.md").exists())
    memory2.close()

    # ------------------------------------------------------------------
    # 7. clear
    # ------------------------------------------------------------------
    print("\n[7] clear")
    memory.clear()
    check("clear 后 get_context 为空", memory.get_context() == [])

    # ------------------------------------------------------------------
    # 清理 + 汇总
    # ------------------------------------------------------------------
    memory.close()  # 释放 SQLite 文件锁（Windows 上不关连接删不掉目录）
    shutil.rmtree(TMP, ignore_errors=True)
    check("close 后临时目录可清理", not TMP.exists())

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
