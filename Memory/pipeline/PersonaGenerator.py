"""
PersonaGenerator — LLM-driven L3 user persona generation.

LLM 深读变更的场景文件（scene_blocks/*.md），生成完整 persona.md。
替换 ProfileAbstractionService 的旧路径。
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from Agent.LargeLanguageModel import LargeLanguageModel
from Memory.scene_blocks.SceneFileManager import SceneFileManager
from configurationLoader import config

logger = logging.getLogger(__name__)

DEFAULT_PERSONA = """# User Persona

## Core Identity
_(to be generated)_

## Skills & Knowledge
_(to be generated)_

## Preferences & Constraints
_(to be generated)_

## Goals & Motivations
_(to be generated)_

## Relationships & Context
_(to be generated)_
"""


class PersonaGenerator:
    """LLM-driven persona generation from scene blocks."""

    def __init__(self, scene_file_manager=None, persona_path=None):
        self.scene_file_mgr = scene_file_manager or SceneFileManager()
        root = config.get("memory.root_dir", "./runtime_memory")
        default_path = os.path.join(root, "persona.md")
        self.persona_path = Path(persona_path or config.get("memory.persona.path", default_path))
        self.persona_path.parent.mkdir(parents=True, exist_ok=True)
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = LargeLanguageModel()
        return self._llm

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def read_persona(self):
        """读取 persona.md 全文，不存在则返回 None。"""
        if self.persona_path.exists():
            return self.persona_path.read_text(encoding="utf-8")
        return None

    def write_persona(self, content):
        """写入 persona.md 文件。"""
        self.persona_path.write_text(content, encoding="utf-8")
        logger.info("PersonaGenerator: wrote persona.md (%d chars)", len(content))

    def get_persona_summary(self):
        """获取 persona.md 的精简摘要（提取 ## 标题行）。"""
        content = self.read_persona() or DEFAULT_PERSONA
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                lines.append(stripped[3:])
        return "; ".join(lines) if lines else "(empty persona)"

    def generate_persona(self, changed_scene_slugs, existing_persona=None):
        """LLM 深读变更的场景文件，生成完整 persona.md。

        Args:
            changed_scene_slugs: 自上次生成以来有变化的场景 slug 列表。
            existing_persona: 当前 persona.md 内容（可通过 read_persona() 获得）。

        Returns:
            str: 生成的 persona.md 内容。
        """
        if existing_persona is None:
            existing_persona = self.read_persona() or ""

        # 收集变更场景的内容
        scene_contents = []
        for slug in changed_scene_slugs:
            meta, body = self.scene_file_mgr.read_scene(slug)
            if meta is not None:
                scene_contents.append({
                    "slug": slug,
                    "title": meta.get("title", slug),
                    "keywords": meta.get("keywords", []),
                    "importance": meta.get("importance", 0.5),
                    "body": body or "",
                })
            else:
                scene_contents.append({
                    "slug": slug, "title": slug,
                    "keywords": [], "importance": 0.5, "body": "(scene file not found)"
                })

        if not scene_contents:
            logger.info("PersonaGenerator: no scene content, skipping.")
            return existing_persona or DEFAULT_PERSONA

        logger.info(
            "PersonaGenerator: generating for %d changed scenes: %s",
            len(changed_scene_slugs), ", ".join(changed_scene_slugs[:5]),
        )

        system_prompt = self._load_base_prompt()
        user_prompt = self._build_user_prompt(scene_contents, existing_persona)

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            result = self.llm.query(messages)
            if result and result.strip():
                self.write_persona(result.strip())
                return result.strip()
        except Exception as e:
            logger.warning("PersonaGenerator: LLM call failed: %s", e)

        return existing_persona or DEFAULT_PERSONA

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    def _load_base_prompt(self):
        """从配置文件加载 persona_generator 系统 prompt。"""
        prompts = config.get("prompt.system.persona_generator", [])
        if prompts:
            try:
                from Prompting.PromptLoader import PromptLoader
                loader = PromptLoader()
                sections = loader.load_system_sections("persona_generator")
                if sections:
                    return sections[0].content
            except Exception as e:
                logger.debug("PromptLoader failed for persona_generator: %s", e)
        return self._default_persona_prompt()

    def _build_user_prompt(self, scenes, existing_persona):
        scenes_text = self._format_scenes_for_prompt(scenes)
        return (
            "请阅读以下变更的场景记忆和当前用户画像，"
            "按照系统指令中定义的格式生成更新后的完整画像：\n\n"
            "=== 当前用户画像 ===\n"
            f"{existing_persona if existing_persona else '(none — first generation)'}\n\n"
            "=== 变更的场景记忆 ===\n"
            f"{scenes_text}\n\n"
            "请输出完整的更新后用户画像（不要省略任何已有信息，只做增量更新和修订）。"
        )

    @staticmethod
    def _format_scenes_for_prompt(scenes):
        lines = []
        for i, sc in enumerate(scenes, 1):
            lines.append(f"### Scene {i}: {sc.get('title', sc.get('slug', '?'))}")
            lines.append(f"Slug: {sc.get('slug', '?')}")
            keywords = sc.get('keywords', [])
            if keywords:
                lines.append(f"Keywords: {', '.join(keywords)}")
            lines.append(f"Importance: {sc.get('importance', 0.5)}")
            lines.append("")
            lines.append(sc.get("body", "").strip())
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _default_persona_prompt():
        return (
            "你是一个用户画像生成专家。你的任务是深度分析用户对话中提取的场景记忆，"
            "生成一个全面、准确的用户画像文档。\n\n"
            "请严格按照以下结构输出（markdown 格式）：\n\n"
            "# User Persona\n\n"
            "## Core Identity\n"
            "用户的身份、角色、背景信息。从多个场景中交叉验证，标注信息来源。\n\n"
            "## Skills & Knowledge\n"
            "用户的技能、专业知识领域。只记录有明确证据支持的技能。\n\n"
            "## Preferences & Constraints\n"
            "用户的偏好、工作习惯、禁忌和限制。区分显式声明和隐式推断。\n\n"
            "## Goals & Motivations\n"
            "用户的长期目标和动力。只记录反复出现或明确表达的目标。\n\n"
            "## Relationships & Context\n"
            "用户与其他实体（人、项目、工具）的关系。\n\n"
            "## Change Log\n"
            "- {{DATE}}: 基于 [场景名] 的场景更新，新增/修订了 [字段]\n"
        )
