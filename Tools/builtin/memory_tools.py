"""
记忆工具 — 模型主动检索 / 写入长期记忆。

- memory_search：用自由构造的 query 主动检索 L1 原子 + L2 场景
  （每轮自动检索只覆盖最近用户消息，模型可用本工具补充查找历史话题）。
- memory_add：把值得长期记住的事实/偏好/决定写入 L1 原子库
  （含 n-gram 去重 + FTS/BM25/embedding 索引，后续检索即可命中）。

MemoryManager 引用由 Gateway 初始化后通过 set_memory_manager 注入
（依赖注入模式；未注入时工具返回明确错误，不抛异常）。
"""
import json
import logging

from Tools.registry import registry

logger = logging.getLogger(__name__)

_memory_manager = None


def set_memory_manager(mgr):
    """由外部（gateway_entry）在 MemoryManager 创建后调用，注入引用。"""
    global _memory_manager
    _memory_manager = mgr
    logger.info("Memory tools: MemoryManager reference set")


def _mgr_unavailable():
    return json.dumps(
        {"status": "error", "error": "memory system not available (no MemoryManager injected)"},
        ensure_ascii=False,
    )


def memory_search(query, top_k=6):
    if _memory_manager is None:
        return _mgr_unavailable()
    query = str(query or "").strip()
    if not query:
        return json.dumps({"status": "error", "error": "query is empty"}, ensure_ascii=False)
    try:
        pack = _memory_manager.hybrid_retriever.retrieve(query, top_k=int(top_k or 6))
    except Exception as e:
        logger.warning("memory_search failed: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

    memories = [
        {
            "id": m.get("source_id"),
            "title": m.get("title"),
            "text": m.get("text"),
            "score": round(float(m.get("score") or 0.0), 4),
        }
        for m in (pack.get("memories") or [])
    ]
    scenes = [
        {
            "slug": s.get("source_id"),
            "title": s.get("title"),
            "summary": s.get("summary"),
            "score": round(float(s.get("score") or 0.0), 4),
        }
        for s in (pack.get("scenes") or [])
    ]
    logger.info("memory_search: query=%.60s → %d atoms, %d scenes", query, len(memories), len(scenes))
    return json.dumps(
        {"status": "success", "query": query, "memories": memories, "scenes": scenes},
        ensure_ascii=False,
    )


def memory_add(atom_type, subject, canonical_text, salience=0.6):
    if _memory_manager is None:
        return _mgr_unavailable()
    canonical_text = str(canonical_text or "").strip()
    if not canonical_text:
        return json.dumps({"status": "error", "error": "canonical_text is empty"}, ensure_ascii=False)
    if len(canonical_text) > 200:
        return json.dumps(
            {"status": "error", "error": "canonical_text 超过 200 字——记忆原子必须简明扼要，请压缩为一句话事实后重试"},
            ensure_ascii=False,
        )
    add_atom = getattr(_memory_manager, "add_atom", None)
    if not callable(add_atom):
        return _mgr_unavailable()
    try:
        atom = add_atom(
            atom_type=str(atom_type or "fact"),
            subject=str(subject or ""),
            canonical_text=canonical_text,
            salience=float(salience or 0.6),
        )
    except Exception as e:
        logger.warning("memory_add failed: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    if atom is None:
        return json.dumps(
            {"status": "duplicate", "message": "已存在高度相似的记忆原子，未重复写入"},
            ensure_ascii=False,
        )
    logger.info("memory_add: stored atom id=%s type=%s subject=%s",
                atom.get("id"), atom.get("atom_type"), atom.get("subject"))
    return json.dumps(
        {"status": "success", "atom_id": atom.get("id"), "atom_type": atom.get("atom_type")},
        ensure_ascii=False,
    )


registry.register(
    name="memory_search",
    description=(
        "主动检索长期记忆（L1 记忆原子 + L2 场景块）。当需要回忆历史对话中提到的事实、"
        "偏好、项目细节，而当前上下文里没有时，用本工具搜索。query 用关键词短语"
        "（如「坦克大战 碰撞检测」），不要写完整问句。"
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词短语"},
            "top_k": {"type": "integer", "description": "返回条数上限，默认 6", "default": 6},
        },
        "required": ["query"],
    },
    handler=memory_search,
    group="memory",
)

registry.register(
    name="memory_add",
    description=(
        "主动写入一条长期记忆原子。当用户透露了值得长期记住的信息（身份、偏好、"
        "项目事实、明确决定）时使用。canonical_text 必须简明扼要：一句话事实，"
        "不超过 200 字，禁止整段复制对话原文。重复内容会被自动去重跳过。"
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "atom_type": {
                "type": "string",
                "description": "原子类型：fact（事实）/ preference（偏好）/ decision（决定）/ constraint（约束）",
                "default": "fact",
            },
            "subject": {"type": "string", "description": "主题标识（短英文或拼音，如 user_profile、tank_battle）"},
            "canonical_text": {
                "type": "string",
                "description": "记忆内容：简明扼要的一句话事实（≤200 字）",
            },
            "salience": {"type": "number", "description": "重要度 0-1，默认 0.6", "default": 0.6},
        },
        "required": ["canonical_text"],
    },
    handler=memory_add,
    group="memory",
)
