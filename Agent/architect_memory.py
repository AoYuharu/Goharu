"""architect 子代理的独立记忆塔（L0+L1+L2，无 L3，无自动管线）。

与主会话记忆完全隔离：独立 root_dir（默认 runtime_memory/architect），
进程级懒加载单例，跨 execute 任务累积项目经验。
不接 PipelineManager——L1 原子提取与 L2 场景聚合在每次 execute 结束后
由 archive_transcript() 一次性驱动；L3 persona 永不生成。

检索缝：_ArchitectWorkingMemory.retrieve_prompt_memory() 委托本单例，
ActorAgent.act() 首轮自动注入历史经验（见 Agent/SubAgent.py）。
"""

import logging
import threading

import configurationLoader
from Memory.bridge import create_memory

logger = logging.getLogger(__name__)

_instance = None
_lock = threading.Lock()
_archive_lock = threading.Lock()


def get_architect_memory():
    """进程级懒加载单例。embedding 模型由 EmbeddingService 类级共享，不双载。"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                root = configurationLoader.config.get(
                    "agent_delegate.architect.memory_root", "runtime_memory/architect"
                )
                logger.info("architect_memory: creating tower at %s", root)
                _instance = create_memory(
                    overrides={"memory.root_dir": root},
                    session_id="architect",
                )
    return _instance


def retrieve_architect_memory(messages):
    """供 SubAgent 任务播种时调用：以当前任务为查询检索塔内经验。

    塔为空 / 检索失败时返回 None（调用方按无注入处理），绝不抛异常穿透。
    """
    try:
        pack = get_architect_memory().retrieve_prompt_memory(messages=messages)
        has_hits = bool(pack and (pack.get("memories") or pack.get("scenes")))
        logger.info(
            "architect_memory: retrieval done, hits=%s "
            "(memories=%d, scenes=%d)",
            has_hits,
            len(pack.get("memories") or []) if pack else 0,
            len(pack.get("scenes") or []) if pack else 0,
        )
        return pack
    except Exception as exc:
        logger.warning("architect_memory: retrieval failed, skip injection: %s", exc)
        return None


def format_experience_block(pack, max_items=8, max_text=300):
    """把检索 pack 渲染成注入任务消息的经验块；无命中返回空串。"""
    if not pack:
        return ""
    lines = []
    scenes = pack.get("scenes") or []
    memories = pack.get("memories") or []
    for sc in scenes[:max_items]:
        title = (sc.get("title") or "").strip()
        text = (sc.get("text") or "").strip().replace("\n", " ")[:max_text]
        if title or text:
            lines.append(f"- [场景] {title}: {text}" if title else f"- [场景] {text}")
    for mem in memories[:max_items]:
        subject = (mem.get("subject") or "").strip()
        text = (mem.get("text") or "").strip().replace("\n", " ")[:max_text]
        if subject or text:
            lines.append(f"- [经验] {subject}: {text}" if subject else f"- [经验] {text}")
    if not lines:
        return ""
    return (
        "## 你的记忆塔检索到的历史经验（来自以往任务，供参考）\n"
        + "\n".join(lines)
        + "\n\n"
    )


def archive_transcript(messages):
    """execute 结束后一次性归档：L0 追加 → L1 原子提取 → L2 场景聚合。

    涉及多次 LLM 调用（SummarizeAgent + SceneExtractor，数十秒级）——
    调用方应放后台线程执行，避免阻塞子代理结果返回。
    全流程 try/except 降级 + 归档锁串行——失败/并发只记日志，绝不影响主流程。
    """
    if not messages:
        logger.info("architect_memory: empty transcript, skip archive")
        return
    with _archive_lock:
        _archive_transcript_locked(messages)


def _archive_transcript_locked(messages):
    try:
        memory = get_architect_memory()
    except Exception as exc:
        logger.warning("architect_memory: tower unavailable, skip archive: %s", exc)
        return

    # L0：原始 transcript 入库（工作记忆双写 SQLite + daily JSON）
    try:
        for msg in messages:
            memory.append(msg)
        logger.info("architect_memory: archived %d messages to L0", len(messages))
    except Exception as exc:
        logger.warning("architect_memory: L0 append failed: %s", exc)
        return

    # L1：SummarizeAgent 从 transcript 提取经验原子（带去重上下文）
    atoms = []
    try:
        from Agent.SummarizerAgent import SummarizeAgent
        from Memory.repositories.L1Repository import L1Repository

        existing = [
            {
                "atom_type": a.get("atom_type"),
                "subject": a.get("subject"),
                "canonical_text": a.get("canonical_text"),
            }
            for a in L1Repository(memory.db).list_atoms(status="active", limit=80)
        ]
        summary = SummarizeAgent().summarize_day(messages, {}, existing_atoms=existing)
        atoms = summary.get("atoms") or []
        if atoms:
            ingested = memory.ingest_atoms(atoms)
            logger.info(
                "architect_memory: L1 extracted %d atoms, ingested %d",
                len(atoms), len(ingested or []),
            )
        else:
            logger.info("architect_memory: L1 produced no atoms")
    except Exception as exc:
        logger.warning("architect_memory: L1 extraction failed: %s", exc)
        return

    # L2：未分配原子聚合为场景块（塔内无 L3，到 L2 为止）
    if atoms:
        try:
            scenes = memory.extract_scenes()
            logger.info(
                "architect_memory: L2 done, scenes=%d", len(scenes or [])
            )
        except Exception as exc:
            logger.warning("architect_memory: L2 extraction failed: %s", exc)
