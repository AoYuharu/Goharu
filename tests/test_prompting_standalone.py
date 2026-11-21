"""Prompting 子系统独立接口测试 —— 外部 harness 视角。

验证 Prompting.api（ContextPipeline）的核心契约：
- system_prompt 字符串注入 / config 文件路径注入
- 缓存断点纪律：断点总数 ≤4、位置在末位 system 块 + 末位可缓存历史轮
- 跨 turn 前缀逐字节稳定（记忆钉账：旧消息内容永不变化）
- retrieval_pack 落位（贴在当轮用户消息上）
- estimate_tokens / micro_compact

运行：python test_prompting_standalone.py
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Prompting.api import create_context_pipeline

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


def _bp_count(msgs):
    return sum(1 for m in msgs if m.get("cache_control"))


def _is_system(m):
    return m.get("role") == "system"


def main():
    print("=" * 60)
    print("Prompting 独立接口测试（ContextPipeline）")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. system_prompt 字符串注入 + 断点位置
    # ------------------------------------------------------------------
    print("\n[1] system_prompt 注入与断点")
    pipe = create_context_pipeline(system_prompt="你是一个测试助手。")
    history = [
        {"role": "user", "content": "问题一", "id": "m1"},
        {"role": "assistant", "content": "回答一", "id": "m2"},
        {"role": "user", "content": "问题二", "id": "m3"},
    ]
    memctx = {}
    msgs = pipe.build_messages(history, memory_contexts=memctx)

    check("system 块含注入的提示词",
          "你是一个测试助手" in msgs[0]["content"], str(msgs[0])[:200])
    check("断点总数 <= 4", _bp_count(msgs) <= 4, str(_bp_count(msgs)))
    sys_bps = [i for i, m in enumerate(msgs) if _is_system(m) and m.get("cache_control")]
    sys_total = sum(1 for m in msgs if _is_system(m))
    check("system 断点只在末位 system 块",
          sys_bps and msgs[sys_bps[-1]] is [m for m in msgs if _is_system(m)][-1]
          and len(sys_bps) == 1,
          f"bps={sys_bps} total_sys={sys_total}")
    conv_bps = [i for i, m in enumerate(msgs)
                if not _is_system(m) and m.get("cache_control")]
    check("历史断点恰为 1 个（末位可缓存历史轮）", len(conv_bps) == 1, str(conv_bps))
    check("历史断点在 assistant m2 上",
          conv_bps and "回答一" in json.dumps(msgs[conv_bps[0]], ensure_ascii=False),
          str(msgs[conv_bps[0]])[:150] if conv_bps else "none")
    check("默认无 git_status / skills 注入",
          "git_status" not in json.dumps(msgs, ensure_ascii=False)
          and "skills" not in json.dumps(msgs, ensure_ascii=False).lower())

    # ------------------------------------------------------------------
    # 2. retrieval_pack 落位 + 记忆钉账
    # ------------------------------------------------------------------
    print("\n[2] retrieval_pack 落位")
    pack = {
        "profile_summary": "用户画像：喜欢咖啡",
        "memories": [{"title": "咖啡", "text": "喜欢冰美式", "source_id": 1, "score": 0.9}],
        "scenes": [{"title": "饮品", "summary": "日常饮品偏好", "source_id": "s1",
                    "keywords": ["咖啡"], "score": 0.8}],
    }
    msgs2 = pipe.build_messages(history, retrieval_pack=pack, memory_contexts=memctx)
    last_user = msgs2[-1]["content"]
    check("persona 贴在当轮用户消息", "用户画像：喜欢咖啡" in last_user)
    check("L1 记忆贴在当轮用户消息", "喜欢冰美式" in last_user)
    check("L2 场景贴在当轮用户消息", "日常饮品偏好" in last_user)
    check("钉账记录到 m3", "m3" in memctx)

    # ------------------------------------------------------------------
    # 3. 跨 turn 前缀逐字节稳定（缓存契约核心断言）
    # ------------------------------------------------------------------
    print("\n[3] 跨 turn 前缀稳定")
    # turn 1
    h1 = [{"role": "user", "content": "问题一", "id": "m1"}]
    ctx = {}
    t1 = pipe.build_messages(h1, retrieval_pack=pack, memory_contexts=ctx)
    # turn 2：历史增长，检索包内容变化（模拟每轮检索结果不同）
    h2 = h1 + [
        {"role": "assistant", "content": "回答一", "id": "m2"},
        {"role": "user", "content": "问题二", "id": "m3"},
    ]
    pack_v2 = {**pack, "memories": [{"title": "新", "text": "新检索结果", "source_id": 2, "score": 0.7}]}
    t2 = pipe.build_messages(h2, retrieval_pack=pack_v2, memory_contexts=ctx)
    prefix_stable = all(
        json.dumps(t1[i], ensure_ascii=False, sort_keys=True)
        == json.dumps(t2[i], ensure_ascii=False, sort_keys=True)
        for i in range(len(t1))
    )
    check("turn1 全部消息在 turn2 中逐字节一致", prefix_stable,
          f"t1={len(t1)} msgs")
    check("turn2 检索新结果只贴 m3", "新检索结果" in t2[-1]["content"])

    # ------------------------------------------------------------------
    # 4. 无 id 消息的退化路径
    # ------------------------------------------------------------------
    print("\n[4] 无 id 退化路径")
    h_no_id = [{"role": "user", "content": "没有 id 的消息"}]
    msgs4 = pipe.build_messages(h_no_id, retrieval_pack=pack, memory_contexts={})
    check("无 id 时检索块直接 prepend 到最后一条",
          "用户画像" in msgs4[-1]["content"], str(msgs4[-1])[:150])

    # ------------------------------------------------------------------
    # 5. config 文件路径注入
    # ------------------------------------------------------------------
    print("\n[5] config 文件路径注入")
    tmp = Path("runtime_memory/temp/prompting_test")
    tmp.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp / "actor.md"
    prompt_file.write_text("来自文件的系统提示词", encoding="utf-8")
    pipe2 = create_context_pipeline(
        config={"prompt": {"system": {"actor": [str(prompt_file)]}}},
        base_dir=Path.cwd(),
    )
    msgs5 = pipe2.build_messages([{"role": "user", "content": "hi", "id": "x"}],
                                 memory_contexts={})
    check("文件提示词注入成功",
          "来自文件的系统提示词" in msgs5[0]["content"], str(msgs5[0])[:200])

    # ------------------------------------------------------------------
    # 6. estimate_tokens / micro_compact
    # ------------------------------------------------------------------
    print("\n[6] estimate_tokens / micro_compact")
    n = pipe.estimate_tokens("hello world " * 100)
    check("estimate_tokens 返回正整数", isinstance(n, int) and n > 0, str(n))

    old_ts = (datetime.now() - timedelta(hours=3)).isoformat()
    new_ts = datetime.now().isoformat()
    tool_msgs = []
    for i in range(8):
        tool_msgs.append({
            "role": "user",
            "timestamp": old_ts,
            "content": [{"type": "tool_result", "tool_use_id": f"c{i}",
                         "content": "很长的工具结果 " * 50}],
        })
    tool_msgs.append({"role": "user", "timestamp": new_ts,
                      "content": [{"type": "tool_result", "tool_use_id": "new",
                                   "content": "新的 " * 50}]})
    compacted, folded = pipe.micro_compact(tool_msgs)
    check("micro_compact 折叠过期长工具结果", folded == 3,
          f"folded={folded}（8 条过期保留 5 条 → 折 3 条）")
    check("新工具结果不折叠", "新的" in json.dumps(compacted[-1], ensure_ascii=False))

    # ------------------------------------------------------------------
    # 7. ContextManager 压缩阶梯（L1 截断 / L2 阈值与压缩 / report）
    # ------------------------------------------------------------------
    print("\n[7] ContextManager 压缩阶梯")
    from Prompting.api import create_context_manager

    cm = create_context_manager(
        system_prompt="测试", max_messages=4, compact_threshold_tokens=100,
        llm=None,
    )
    # L1 窗口截断 + tool_result 孤儿保护
    hist = [
        {"role": "user", "content": "u0", "id": "h0"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                           "name": "X", "input": {}}], "id": "h1"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "r"}], "id": "h2"},
        {"role": "assistant", "content": "a3", "id": "h3"},
        {"role": "user", "content": "u4", "id": "h4"},
        {"role": "assistant", "content": "a5", "id": "h5"},
    ]
    trimmed = cm.truncate_history(hist)  # max 4：切点落在 tool_result(h2) → 孤儿应被丢
    check("L1 截断到窗口内", len(trimmed) <= 4, str(len(trimmed)))
    first_blocks = trimmed[0].get("content")
    check("截断后首条不是孤儿 tool_result",
          not (isinstance(first_blocks, list)
               and any(b.get("type") == "tool_result" for b in first_blocks
                       if isinstance(b, dict))),
          str(trimmed[0])[:150])
    check("max_messages<=0 不截断",
          len(cm.truncate_history(hist, max_messages=0)) == len(hist))

    # L2 阈值判定两侧
    many = [{"role": "user", "content": "长文本 " * 200, "id": f"b{i}"}
            for i in range(5)]
    check("超阈值 → should_llm_compact=True", cm.should_llm_compact(many) is True)
    check("未超阈值 → False",
          cm.should_llm_compact([{"role": "user", "content": "短", "id": "s"}]) is False)

    # L2 压缩：注入假 llm（返回字符串）
    class CompactLLM:
        def __init__(self):
            self.calls = 0

        def query(self, messages, on_retry=None, **kw):
            self.calls += 1
            return "[上下文摘要] 用户讨论了测试话题。"

    fake_compact_llm = CompactLLM()
    cm2 = create_context_manager(system_prompt="测试", llm=fake_compact_llm)
    summary = cm2.llm_compact(hist)
    check("llm_compact 返回摘要", "上下文摘要" in summary, summary[:100])
    check("假 llm 被调用 1 次", fake_compact_llm.calls == 1)

    # 惰性：创建时不构造 compactor
    cm3 = create_context_manager(system_prompt="测试")
    check("compactor 惰性构造", cm3._compactor is None)

    # context_report 结构
    report = cm.context_report(hist)
    check("context_report 结构完整",
          {"message_count", "estimated_tokens", "micro_compact", "window",
           "llm_compact"} <= set(report)
          and report["window"]["over"] is True
          and report["llm_compact"]["recommended"] in (True, False),
          str(report)[:200])

    # 工具结果预算：超限落盘 + 占位符（显式小阈值避免构造超长文本）
    budget_cache = tmp / "tool_cache"
    big_results = [
        {"tool_name": "Read", "result_text": "很长的结果 " * 300},
        {"tool_name": "Grep", "result_text": "短结果"},
    ]
    replaced = cm.apply_result_budget(
        big_results, cache_dir=str(budget_cache),
        max_single_tokens=100, max_batch_tokens=100000,
    )
    check("超限结果被替换", replaced is True)
    check("占位符含缓存路径", "tool_cache" in big_results[0]["result_text"]
          or "Read" in big_results[0]["result_text"], big_results[0]["result_text"][:150])
    check("小结果不动", "短结果" in big_results[1]["result_text"])
    check("缓存文件已落盘", any(budget_cache.glob("*.txt")))

    # Goharu 模式开关：git_status / skills 注入
    cm_go = create_context_manager(
        system_prompt="测试", include_git_status=True, include_skills=True,
    )
    msgs_go = cm_go.build_messages(
        [{"role": "user", "content": "hi", "id": "g1"}], memory_contexts={},
    )
    blob = json.dumps(msgs_go, ensure_ascii=False)
    check("include_git_status=True 注入 git_status 块", "git_status" in blob)
    check("include_skills=True 注入技能列表", "skills are available" in blob
          or "skill" in blob.lower())

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
