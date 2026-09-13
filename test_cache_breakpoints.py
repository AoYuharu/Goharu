"""验证缓存断点收敛到 ≤4，且不丢缓存内容。"""
from Prompting.PromptAssembler import PromptAssembler


def system_sections_text(doc):
    return "\n".join(str(s.content) for s in doc.system_sections)


assembler = PromptAssembler()

# 构造一段含多条历史 + 多轮 tool 调用的对话
history = [
    {"role": "user", "content": "问题1", "id": "msg_1", "timestamp": "2026-08-31T00:00:00"},
    {"role": "assistant", "content": "回答1", "id": "msg_2", "timestamp": "2026-08-31T00:00:01"},
    {"role": "user", "content": "问题2", "id": "msg_3", "timestamp": "2026-08-31T00:00:02"},
    {"role": "assistant", "content": "回答2", "id": "msg_4", "timestamp": "2026-08-31T00:00:03"},
    {"role": "user", "content": "问题3（最新）", "id": "msg_5", "timestamp": "2026-08-31T00:00:04"},
]

retrieval_pack = {
    "profile_summary": "用户画像摘要",
    "memories": [{"title": "m1", "text": "记忆1", "source_id": "a", "score": 0.9}],
    "scenes": [{"title": "s1", "summary": "场景1", "source_id": "slug1", "score": 0.8, "keywords": ["k"]}],
}

doc = assembler.build_actor_document(
    history,
    retrieval_pack=retrieval_pack,
)

system_cc = [s for s in doc.system_sections if s.cache_control is not None]
conv_cc = [s for s in doc.conversation_sections if s.cache_control is not None]

print(f"system sections: {len(doc.system_sections)}, with cache_control: {len(system_cc)}")
print(f"conversation sections: {len(doc.conversation_sections)}, with cache_control: {len(conv_cc)}")
total = len(system_cc) + len(conv_cc)
print(f"总断点数: {total} (上限 4)")

assert total <= 4, f"断点超限: {total}"
assert total >= 1, "至少应缓存 system 前缀"
# 最后一个 system 块必须带缓存（覆盖整个 system 前缀）
assert doc.system_sections[-1].cache_control is not None
# 最后一个可缓存历史轮（msg_4，即 index 3）带缓存
assert doc.conversation_sections[-2].cache_control is not None  # 倒数第二是 assistant 回答2
# 最新一轮（msg_5 及其后的 assistant，此处无）不带缓存

# 检索内容必须贴在最新用户消息上（不在 system 前缀中），否则每 turn 检索
# 结果变化会使整个前缀缓存失效
sys_text = "\n".join(str(s.content) for s in doc.system_sections)
assert "<retrieved_memories" not in sys_text, "检索记忆不应出现在 system 前缀"
assert "<persona" not in sys_text, "L3 persona 不应出现在 system 前缀"
last_conv = doc.conversation_sections[-1]
assert last_conv.kind == "user", "最后一段会话应是最新用户消息"
assert "<system-reminder>" in last_conv.content, "记忆上下文应贴在最新用户消息上"
assert "<persona" in last_conv.content and "<retrieved_memories" in last_conv.content

# ── 跨 turn 记账：turn 2 渲染时 turn 1 的记忆块必须仍在原消息上 ──
ctx = {}
pack1 = {"profile_summary": "画像一", "memories": [{"title": "m1", "text": "记忆一", "source_id": "a", "score": 0.9}]}
hist_t1 = [
    {"role": "user", "content": "问题1", "id": "msg_1"},
    {"role": "assistant", "content": "回答1", "id": "msg_2"},
    {"role": "user", "content": "问题2（最新）", "id": "msg_3"},
]
doc_t1 = assembler.build_actor_document(hist_t1, retrieval_pack=pack1, memory_contexts=ctx)

pack2 = {"profile_summary": "画像二", "memories": [{"title": "m2", "text": "记忆二", "source_id": "b", "score": 0.8}]}
hist_t2 = hist_t1 + [
    {"role": "assistant", "content": "回答2", "id": "msg_4"},
    {"role": "user", "content": "问题3（最新）", "id": "msg_5"},
]
doc_t2 = assembler.build_actor_document(hist_t2, retrieval_pack=pack2, memory_contexts=ctx)

def _content_of(doc, msg_id):
    for s in doc.conversation_sections:
        if s.metadata and s.metadata.get("id") == msg_id:
            return s.content
    return ""

c1 = _content_of(doc_t2, "msg_1")
c3 = _content_of(doc_t2, "msg_3")
c5 = _content_of(doc_t2, "msg_5")
assert "画像一" in c3, "turn 1 的记忆块必须仍贴在 msg_3（turn 1 的最新用户消息）上"
assert "画像二" not in c3, "msg_3 不应携带 turn 2 的新记忆"
assert "画像一" not in c1 and "画像二" not in c1, "msg_1 不携带任何记忆块"
assert "画像二" in c5, "最新消息应携带本轮记忆"
# 前缀逐字节稳定：turn2 渲染中 [system + msg_1..msg_3] 与 turn1 渲染中同段完全一致
prefix_t1 = [system_sections_text(doc_t1)] + [str(s.content) for s in doc_t1.conversation_sections]
prefix_t2 = [system_sections_text(doc_t2)] + [str(s.content) for s in doc_t2.conversation_sections][:3]
assert prefix_t1 == prefix_t2, "turn 2 的前缀必须与 turn 1 已缓存前缀逐字节一致"
print("PASS: 断点收敛正确，检索内容跨 turn 稳定贴回原消息")
