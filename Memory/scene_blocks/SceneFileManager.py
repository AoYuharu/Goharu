"""
SceneFileManager — CRUD for scene_blocks/*.md files.

scene_blocks/*.md 是 L2 场景数据的 source of truth，SQLite 仅为检索索引。
每个场景文件包含 YAML frontmatter 元数据和 markdown 正文。
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from configurationLoader import config

logger = logging.getLogger(__name__)

_META_DELIMITER = re.compile(r"^---\s*$", re.MULTILINE)

DEFAULT_SCENE_BODY = """# Summary

_(new scene — to be filled by SceneExtractor)_

## Key Facts

-

## Timeline

-

## Open Questions

-
"""


class SceneFileManager:
    """管理 scene_blocks/ 目录下的 Markdown 场景文件。"""

    def __init__(self, base_dir=None):
        root = config.get("memory.root_dir", "./runtime_memory")
        default_dir = os.path.join(root, "scene_blocks")
        self.base_dir = Path(base_dir or config.get("memory.scene_blocks.dir", default_dir))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_scenes(self):
        """列出所有场景文件的 slug（按更新时间降序）。"""
        scenes = []
        for md_path in sorted(self.base_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            slug = md_path.stem
            try:
                meta = self.parse_meta(md_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            if meta:
                scenes.append({**meta, "slug": slug, "file_path": str(md_path)})
            else:
                scenes.append({"slug": slug, "file_path": str(md_path)})
        return scenes

    def read_scene(self, slug):
        """读取指定 slug 的场景文件，返回 (meta dict, body text) 或 (None, None)。"""
        path = self._path_for(slug)
        if not path.exists():
            return None, None
        text = path.read_text(encoding="utf-8")
        meta = self.parse_meta(text)
        body = self._strip_meta(text)
        return meta, body

    def write_scene(self, meta, body):
        """写入或更新一个场景文件。meta 中必须包含 'slug' 字段。"""
        slug = meta.get("slug")
        if not slug:
            raise ValueError("Scene meta must contain 'slug'")
        path = self._path_for(slug)
        now = datetime.now().replace(microsecond=0).isoformat()
        if not path.exists():
            meta.setdefault("created_at", now)
        meta["updated_at"] = now
        meta.setdefault("scene_id", slug)
        meta.setdefault("importance", 0.5)
        meta.setdefault("status", "active")
        meta.setdefault("atom_ids", [])
        meta.setdefault("member_count", 0)
        meta.setdefault("keywords", [])

        content = self._format_scene_file(meta, body or DEFAULT_SCENE_BODY)
        path.write_text(content, encoding="utf-8")
        logger.info("SceneFileManager: wrote scene slug=%s path=%s", slug, path)
        return slug

    def delete_scene(self, slug):
        """删除场景文件。返回 True 表示删除了，False 表示不存在。"""
        path = self._path_for(slug)
        if not path.exists():
            return False
        path.unlink()
        logger.info("SceneFileManager: deleted scene slug=%s", slug)
        return True

    # ------------------------------------------------------------------
    # Parsing / Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def parse_meta(text):
        """从 markdown 文本中解析 YAML frontmatter 元数据。"""
        if not text or not text.startswith("---"):
            return None
        parts = re.split(r"\n---\s*\n", text, maxsplit=1)
        if len(parts) < 2:
            return None
        try:
            meta = yaml.safe_load(parts[0])
            return meta if isinstance(meta, dict) else None
        except yaml.YAMLError:
            return None

    @staticmethod
    def parse_sections(text):
        """解析 markdown body 的 # 标题段落。

        Returns:
            dict[str, str]: section_name -> section_content 的映射。
        """
        body = SceneFileManager._strip_meta(text)
        sections = {}
        current_section = "_preamble"
        current_lines = []
        for line in body.splitlines():
            if line.startswith("# ") and not line.startswith("## "):
                if current_lines:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = line[2:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            sections[current_section] = "\n".join(current_lines).strip()
        return sections

    @staticmethod
    def extract_search_text(slug):
        """从场景文件中提取用于 FTS/BM25 索引的搜索文本。"""
        mgr = SceneFileManager()
        meta, body = mgr.read_scene(slug)
        if meta is None:
            return ""
        parts = [
            meta.get("title") or slug,
            meta.get("summary") or "",
            " ".join(meta.get("keywords") or []),
            body or "",
        ]
        return "\n".join(part for part in parts if part)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _path_for(self, slug):
        return self.base_dir / f"{slug}.md"

    @staticmethod
    def _strip_meta(text):
        """去掉 YAML frontmatter，只返回 body 部分。"""
        if not text or not text.startswith("---"):
            return text or ""
        parts = re.split(r"\n---\s*\n", text, maxsplit=1)
        return parts[1] if len(parts) >= 2 else text

    @staticmethod
    def _format_scene_file(meta, body):
        """组装完整的场景 markdown 文件内容。"""
        header = yaml.dump(
            {k: v for k, v in meta.items() if k != "slug" and k != "file_path"},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()
        body = (body or "").strip()
        return f"---\n{header}\n---\n\n{body}\n"
