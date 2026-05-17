"""
SceneExtractor — LLM-driven L2 scene extraction.

替换 SceneAggregationService 的启发式分组。
使用 LLM 从 L1 未分配原子中提取场景，写入 scene_blocks/*.md，
同步更新 SQLite FTS/embedding/BM25 索引。
"""

import logging

from Agent.LargeLanguageModel import LargeLanguageModel
from configurationLoader import config

from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.retrieval.EmbeddingService import EmbeddingService
from Memory.scene_blocks.SceneFileManager import SceneFileManager

logger = logging.getLogger(__name__)


class SceneExtractor:
    """LLM-driven scene extraction from unassigned L1 atoms."""

    def __init__(self, scene_file_manager=None, l1_repo=None, l2_repo=None, embedding_service=None):
        self.scene_file_mgr = scene_file_manager or SceneFileManager()
        self.l1_repo = l1_repo or L1Repository()
        self.l2_repo = l2_repo or L2Repository(self.l1_repo.db)
        self.embedding_service = embedding_service or EmbeddingService(self.l1_repo.db)
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = LargeLanguageModel()
        return self._llm

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def extract_scenes(self, unassigned_atom_ids, existing_scenes=None):
        """LLM 从未分配的 L1 原子中提取场景。

        Args:
            unassigned_atom_ids: 尚未分配场景的 L1 原子 ID 列表。
            existing_scenes: 已知场景列表 (list of dict with slug, title, summary, keywords)。

        Returns:
            list[str]: 新创建的场景 slug 列表。
        """
        if not unassigned_atom_ids:
            logger.info("SceneExtractor: no unassigned atoms, skipping.")
            return []

        # 加载原子
        atoms = []
        for aid in unassigned_atom_ids:
            atom = self.l1_repo.get_atom(aid)
            if atom is None:
                continue
            atoms.append(atom)
        if not atoms:
            logger.info("SceneExtractor: no valid atoms to process, skipping.")
            return []

        # 加载已有场景
        if existing_scenes is None:
            existing_scenes = self.scene_file_mgr.list_scenes()

        logger.info(
            "SceneExtractor: processing %d atoms against %d existing scenes",
            len(atoms), len(existing_scenes),
        )

        # 构建 prompt
        prompt = self._build_extraction_prompt(atoms, existing_scenes)

        try:
            system_prompt = self._load_base_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            result = self.llm.query(messages)
            if not result or not result.strip():
                logger.warning("SceneExtractor: LLM returned empty result")
                return []

            # 解析 LLM 返回的场景定义并写入文件
            created_slugs = self._parse_and_write_scenes(result, atoms, existing_scenes)
            logger.info(
                "SceneExtractor: created %d scenes: %s",
                len(created_slugs), ", ".join(created_slugs),
            )
            return created_slugs
        except Exception as e:
            logger.warning("SceneExtractor: LLM call failed: %s", e)
            return []

    def _sync_sqlite_from_disk(self):
        """将所有 scene_blocks/*.md 的元数据同步到 SQLite l2_scenes 表作为检索索引。"""
        all_scenes = self.scene_file_mgr.list_scenes()
        synced = 0
        for sc in all_scenes:
            slug = sc.get("slug")
            if not slug:
                continue
            _, body = self.scene_file_mgr.read_scene(slug)
            summary = (body or "").strip()
            # 取第一段作为 summary
            if summary and "\n\n" in summary:
                summary = summary.split("\n\n")[0]
            summary = summary[:500]

            scene_row = self.l2_repo.upsert_scene(
                slug=slug,
                title=sc.get("title") or slug,
                summary=summary,
                keywords=sc.get("keywords") or [],
                importance=float(sc.get("importance", 0.5)),
                status=sc.get("status", "active"),
            )
            # 成员同步
            atom_ids = sc.get("atom_ids") or []
            members = [{"atom_id": aid, "weight": 1.0, "reason": "sync_from_scene_file"} for aid in atom_ids]
            if members:
                self.l2_repo.replace_members(scene_row["id"], members)
                # 更新 L1 原子的 scene_id
                for aid in atom_ids:
                    self.l1_repo.set_scene(aid, scene_row["id"])
            # 索引
            search_text = SceneFileManager.extract_search_text(slug)
            self.l2_repo.db.replace_fts_entry("l2", scene_row["id"], search_text)
            tf_vec = self.l2_repo.bm25.encode_document(search_text)
            if tf_vec:
                self.l2_repo.db.replace_bm25_entry("l2", scene_row["id"], tf_vec, len(tf_vec))
            self.embedding_service.store_embedding(
                "l2", scene_row["id"],
                sc.get("summary") or sc.get("title") or slug,
            )
            synced += 1
        logger.info("SceneExtractor: synced %d scenes from disk to SQLite", synced)
        return synced

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_extraction_prompt(self, atoms, existing_scenes):
        atoms_text = self._format_atoms_for_prompt(atoms)
        scenes_text = self._format_existing_scenes_for_prompt(existing_scenes)
        return (
            "请阅读以下对话中提取的记忆原子和已有场景，分析并输出场景分类结果：\n\n"
            "=== 记忆原子 ===\n"
            f"{atoms_text}\n\n"
            "=== 已有场景 ===\n"
            f"{scenes_text}\n\n"
            "请按照系统指令中定义的 JSON 格式输出场景提取结果。"
        )

    @staticmethod
    def _format_atoms_for_prompt(atoms):
        lines = []
        for atom in atoms:
            lines.append(
                f"- [ID:{atom['id']}] ({atom.get('atom_type', 'fact')}) "
                f"{atom.get('subject', '')}: {atom.get('canonical_text', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_existing_scenes_for_prompt(scenes):
        if not scenes:
            return "(no existing scenes)"
        lines = []
        for sc in scenes:
            slug = sc.get("slug", "?")
            title = sc.get("title", slug)
            keywords = sc.get("keywords", [])
            lines.append(f"- {slug}: {title}" + (f" (keywords: {', '.join(keywords)})" if keywords else ""))
        return "\n".join(lines)

    def _load_base_prompt(self):
        prompts = config.get("prompt.system.scene_extractor", [])
        if prompts:
            try:
                from Prompting.PromptLoader import PromptLoader
                loader = PromptLoader()
                sections = loader.load_system_sections("scene_extractor")
                if sections:
                    return sections[0].content
            except Exception as e:
                logger.debug("PromptLoader failed for scene_extractor: %s", e)
        return self._default_extraction_prompt()

    def _parse_and_write_scenes(self, llm_output, atoms, existing_scenes):
        """解析 LLM JSON 输出并写入 scene_blocks/*.md 文件。"""
        import json
        from Agent.json_parser import JSONParser

        existing_slugs = {s.get("slug") for s in existing_scenes}
        created = []

        try:
            parsed = JSONParser.parse_with_retry(llm_output)
        except Exception:
            try:
                parsed = json.loads(llm_output)
            except json.JSONDecodeError:
                # 尝试从文本中提取 JSON
                import re
                json_match = re.search(r'\[[\s\S]*\]', llm_output)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        logger.warning("SceneExtractor: failed to parse LLM output as JSON")
                        return []
                else:
                    logger.warning("SceneExtractor: no JSON found in LLM output")
                    return []

        # Normalize to list
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            logger.warning("SceneExtractor: expected list of scenes, got %s", type(parsed))
            return []

        for item in parsed:
            if not isinstance(item, dict):
                continue
            slug = item.get("slug", "").strip().lower().replace(" ", "-")
            if not slug:
                continue

            # 确定操作：new 还是 update
            action = item.get("action", "new")
            if action == "update" and slug in existing_slugs:
                meta, body = self.scene_file_mgr.read_scene(slug)
                if meta is None:
                    action = "new"
                else:
                    # 合并更新
                    if item.get("keywords"):
                        meta["keywords"] = list(set(meta.get("keywords", []) + item["keywords"]))
                    if item.get("importance"):
                        meta["importance"] = max(meta.get("importance", 0.5), item.get("importance", 0.5))
                    meta["title"] = item.get("title") or meta.get("title", slug)
                    old_atom_ids = set(meta.get("atom_ids", []))
                    new_atom_ids = old_atom_ids | set(item.get("atom_ids", []))
                    meta["atom_ids"] = list(new_atom_ids)
                    meta["member_count"] = len(meta["atom_ids"])
                    body = self._merge_scene_body(body or "", item)
                    self.scene_file_mgr.write_scene(meta, body)
                    created.append(slug)
                    continue

            # New scene
            meta = {
                "slug": slug,
                "title": item.get("title") or slug.replace("-", " ").title(),
                "keywords": item.get("keywords") or [],
                "importance": float(item.get("importance", 0.5)),
                "status": "active",
                "atom_ids": item.get("atom_ids") or [],
            }
            meta["member_count"] = len(meta["atom_ids"])
            body = item.get("body") or self._generate_default_body(item)
            self.scene_file_mgr.write_scene(meta, body)
            created.append(slug)

        # 同步 SQLite 索引
        for slug in created:
            sc = self.scene_file_mgr.read_scene(slug)
            if sc[0] is None:
                continue
            self._index_scene_to_sqlite(slug)

        return created

    def _index_scene_to_sqlite(self, slug):
        """将单个场景文件索引到 SQLite。"""
        meta, body = self.scene_file_mgr.read_scene(slug)
        if meta is None:
            return
        summary = (body or "").split("\n\n")[0][:500] if body else ""
        scene_row = self.l2_repo.upsert_scene(
            slug=slug,
            title=meta.get("title") or slug,
            summary=summary,
            keywords=meta.get("keywords") or [],
            importance=float(meta.get("importance", 0.5)),
            status=meta.get("status", "active"),
        )
        # 成员
        atom_ids = meta.get("atom_ids") or []
        members = [{"atom_id": aid, "weight": 1.0, "reason": "scene_extractor"} for aid in atom_ids]
        if members:
            self.l2_repo.replace_members(scene_row["id"], members)
            for aid in atom_ids:
                self.l1_repo.set_scene(aid, scene_row["id"])
        # 索引
        search_text = SceneFileManager.extract_search_text(slug)
        self.l2_repo.db.replace_fts_entry("l2", scene_row["id"], search_text)
        tf_vec = self.l2_repo.bm25.encode_document(search_text)
        if tf_vec:
            self.l2_repo.db.replace_bm25_entry("l2", scene_row["id"], tf_vec, len(tf_vec))
        self.embedding_service.store_embedding("l2", scene_row["id"], summary or meta.get("title", slug))

    def _merge_scene_body(self, existing_body, item):
        additions = item.get("body_additions") or ""
        if additions:
            return existing_body.rstrip() + "\n\n" + additions.strip()
        new_body = item.get("body") or ""
        if new_body:
            return new_body
        return existing_body

    @staticmethod
    def _generate_default_body(item):
        title = item.get("title", "Untitled")
        summary = item.get("summary", "")
        facts = item.get("key_facts") or []
        facts_text = "\n".join(f"- {f}" for f in facts) if facts else "- (to be expanded)"
        return (
            f"# Summary\n\n{summary if summary else '_(new scene)_'}\n\n"
            f"## Key Facts\n\n{facts_text}\n\n"
            f"## Timeline\n\n- (to be expanded)\n\n"
            f"## Open Questions\n\n- (to be expanded)\n"
        )

    @staticmethod
    def _default_extraction_prompt():
        return (
            "你是一个记忆巩固架构师（Memory Consolidation Architect）。\n"
            "你的任务是将离散的记忆原子（L1）组织为连贯的场景上下文（L2），"
            "每个场景代表一个话题、项目、用户或长期上下文。\n\n"
            "## 批量场景处理规则\n"
            "- **创建新场景**: 当记忆原子无法归入已有场景时\n"
            "- **更新已有场景**: 当记忆原子与已有场景相关时，"
            "在已有场景增量补充，不要重复已有内容\n"
            "- **软删除场景**: 当场景不再活跃或已被合并时，标记为 archived\n"
            "- **合并场景**: 当多个场景高度相关时，合并为单一场景\n\n"
            "## 输出格式\n"
            "请输出 JSON 数组，每个元素表示一个操作：\n"
            "```json\n"
            "[\n"
            "  {\n"
            '    "action": "new",\n'
            '    "slug": "topic-slug",\n'
            '    "title": "场景标题",\n'
            '    "summary": "场景摘要 1-2 句",\n'
            '    "keywords": ["关键词1", "关键词2"],\n'
            '    "importance": 0.8,\n'
            '    "atom_ids": [101, 102],\n'
            '    "key_facts": ["事实1", "事实2"]\n'
            "  }\n"
            "]\n"
            "```\n\n"
            "## 约束\n"
            "- atom_ids 必须是输入中实际存在的原子 ID\n"
            "- slug 必须简短、有意义、仅包含小写字母数字和连字符\n"
            "- 不要创建信息冗余的场景（先检查已有场景）\n"
            "- 重要性评分 0.0-1.0，常规场景 0.5，重要场景 >0.7\n"
        )
