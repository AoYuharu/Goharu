from pathlib import Path

import yaml

from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.repositories.L3Repository import L3Repository
from configurationLoader import config


class MarkdownProjector:
    SECTION_ORDER = ["Identity", "Work", "Location", "Preferences", "Objective Facts"]

    def __init__(self, l1_repo=None, l2_repo=None, l3_repo=None):
        self.root_dir = Path(config.get("memory.root_dir", "./runtime_memory"))
        self.index_path = Path(config.get("memory.index.path", str(self.root_dir / "MEMORY.md")))
        self.user_path = Path(config.get("memory.user.path", str(self.root_dir / "USER.md")))
        self.topic_dir = Path(config.get("memory.topic.dir", str(self.root_dir / "topic")))
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        self.topic_dir.mkdir(parents=True, exist_ok=True)
        self.l1_repo = l1_repo or L1Repository()
        self.l2_repo = l2_repo or L2Repository(self.l1_repo.db)
        self.l3_repo = l3_repo or L3Repository(self.l1_repo.db)

    def project_all(self):
        user_md = self.render_user_markdown()
        memory_md = self.render_memory_markdown()
        topics = self.render_topic_documents()
        self.user_path.write_text(user_md, encoding="utf-8")
        self.index_path.write_text(memory_md, encoding="utf-8")
        self._write_topics(topics)
        return {
            "user_path": str(self.user_path),
            "memory_path": str(self.index_path),
            "topic_count": len(topics),
        }

    def render_user_markdown(self):
        metadata = {
            "updated_at": self._latest_profile_update(),
            "last_reviewed_at": self._latest_profile_update(),
            "source_turn": "db_projection",
        }
        front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        sections = self.l3_repo.build_sectioned_profile()
        lines = ["---", front_matter, "---", "", "# USER", ""]
        for section_name in self.SECTION_ORDER:
            lines.append(f"## {section_name}")
            section_value = sections.get(section_name) or ([] if section_name in {"Preferences", "Objective Facts"} else {})
            if isinstance(section_value, dict):
                for key, value in section_value.items():
                    lines.append(f"- {key}: {value}")
            else:
                for item in self._dedupe_strings(section_value):
                    lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_memory_markdown(self):
        scenes = self.l2_repo.list_scenes(status="active")
        important_facts = [
            atom.get("canonical_text")
            for atom in self.l1_repo.list_atoms(atom_types=["fact", "constraint", "topic_fact"], limit=20)
        ]
        profile = self.l3_repo.build_sectioned_profile()
        profile_flat = {}
        for section_name in ["Identity", "Work", "Location"]:
            values = profile.get(section_name) or {}
            if isinstance(values, dict):
                profile_flat.update(values)
        metadata = {
            "updated_at": self._latest_memory_update(),
            "topic_count": len(scenes),
            "last_topic_merge_at": self._latest_scene_update(),
            "summaries_since_merge": 0,
        }
        front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        lines = ["---", front_matter, "---", "", "# MEMORY", "", "## User Profile"]
        for key, value in profile_flat.items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## Important Facts"])
        for fact in self._dedupe_strings(important_facts):
            lines.append(f"- {fact}")
        lines.extend(["", "## Topics"])
        for scene in scenes:
            lines.extend(
                [
                    f"- slug: {scene.get('slug', '')}",
                    f"  title: {scene.get('title', '')}",
                    f"  file: topic/{scene.get('slug', '')}.md",
                    f"  summary: {self._single_line(scene.get('summary', ''))}",
                    f"  keywords: {', '.join(scene.get('keywords') or [])}",
                    f"  last_updated: {self._day_only(scene.get('updated_at'))}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def render_topic_documents(self):
        documents = []
        for scene in self.l2_repo.list_scenes(status="active"):
            members = self.l2_repo.list_members(scene["id"])
            atoms = [self.l1_repo.get_atom(member["atom_id"]) for member in members]
            facts = [atom.get("canonical_text") for atom in atoms if atom and atom.get("atom_type") in {"fact", "topic_fact", "constraint"}]
            questions = [atom.get("canonical_text") for atom in atoms if atom and atom.get("atom_type") == "plan"]
            timeline = []
            for atom in atoms:
                if atom and atom.get("source_turn_id"):
                    timeline.append(f"turn {atom['source_turn_id']}: {atom.get('canonical_text', '')}")
            metadata = {
                "slug": scene.get("slug"),
                "title": scene.get("title"),
                "created_at": scene.get("created_at"),
                "updated_at": scene.get("updated_at"),
                "last_mentioned": self._day_only(scene.get("last_mentioned_at")),
                "keywords": self._dedupe_strings(scene.get("keywords") or []),
                "aliases": self._dedupe_strings(scene.get("aliases") or []),
                "source_days": self._dedupe_strings(self._day_only(atom.get("created_at")) for atom in atoms if atom),
                "merged_from": [],
            }
            front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
            lines = [
                "---",
                front_matter,
                "---",
                "",
                "# Summary",
                str(scene.get("summary") or "").strip(),
                "",
                "## Key Facts",
            ]
            lines.extend(f"- {fact}" for fact in self._dedupe_strings(facts))
            lines.extend(["", "## Open Questions"])
            lines.extend(f"- {question}" for question in self._dedupe_strings(questions))
            lines.extend(["", "## Timeline"])
            lines.extend(f"- {event}" for event in self._dedupe_strings(timeline))
            lines.append("")
            documents.append({
                "slug": scene.get("slug"),
                "content": "\n".join(lines),
            })
        return documents

    def _write_topics(self, documents):
        expected = set()
        for doc in documents:
            slug = doc.get("slug") or "untitled-topic"
            expected.add(f"{slug}.md")
            (self.topic_dir / f"{slug}.md").write_text(doc.get("content", ""), encoding="utf-8")
        for path in self.topic_dir.glob("*.md"):
            if path.name not in expected:
                path.unlink()

    def _latest_profile_update(self):
        rows = self.l3_repo.list_profile_rows(status="active")
        if not rows:
            return ""
        return max(str(row.get("updated_at") or "") for row in rows)

    def _latest_scene_update(self):
        scenes = self.l2_repo.list_scenes(status="active")
        if not scenes:
            return ""
        return max(str(scene.get("updated_at") or "") for scene in scenes)

    def _latest_memory_update(self):
        values = [self._latest_profile_update(), self._latest_scene_update()]
        atoms = self.l1_repo.list_atoms(limit=1)
        if atoms:
            values.append(str(atoms[0].get("updated_at") or ""))
        values = [value for value in values if value]
        return max(values) if values else ""

    @staticmethod
    def _day_only(value):
        text = str(value or "")
        return text.split("T", 1)[0] if "T" in text else text

    @staticmethod
    def _single_line(text):
        return " ".join(str(text or "").split())

    @staticmethod
    def _dedupe_strings(items):
        seen = set()
        results = []
        for item in items or []:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            results.append(value)
        return results
