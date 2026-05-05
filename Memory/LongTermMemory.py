import json
import re
from datetime import date, datetime
from pathlib import Path

import yaml

from configurationLoader import config


class LongTermMemory:
    def __init__(self):
        self.root_dir = Path(config.get("memory.root_dir", "./runtime_memory"))
        self.index_path = Path(config.get("memory.index.path", str(self.root_dir / "MEMORY.md")))
        self.topic_dir = Path(config.get("memory.topic.dir", str(self.root_dir / "topic")))
        self.soul_path = self.index_path.with_name("SOUL.md")
        self.soul_created_this_run = False

        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.topic_dir.mkdir(parents=True, exist_ok=True)

        if not self.index_path.exists():
            self.rebuild_memory_index(profile={}, important_facts=[])
        self._ensure_soul_file_exists()

    def current_timestamp(self):
        return datetime.now().replace(microsecond=0).isoformat()

    def _default_soul_markdown(self):
        return """# SOUL

## Identity
- You are a thoughtful local AI assistant.
- You aim to be clear, grounded, and helpful.

## Style
- Prefer concise, direct answers.
- Be honest about uncertainty.
- Stay calm, practical, and readable.

## Values
- Prioritize accuracy over performance.
- Respect the user's goals and context.
- Avoid making up facts, sources, or outcomes.

## Boundaries
- Follow system and runtime instructions.
- Do not invent tool results or memories.
- Ask for clarification when the request is ambiguous.
"""

    def _ensure_soul_file_exists(self):
        if self.soul_path.exists():
            return False
        self.soul_path.write_text(self._default_soul_markdown().rstrip() + "\n", encoding="utf-8")
        self.soul_created_this_run = True
        return True

    @staticmethod
    def _today():
        return date.today().isoformat()

    @staticmethod
    def _dedupe_strings(items):
        seen = set()
        deduped = []
        for item in items:
            if item is None:
                continue
            value = str(item).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    @staticmethod
    def _single_line(text):
        return " ".join(str(text).split())

    @staticmethod
    def _slugify(text):
        slug = re.sub(r"[\s_]+", "-", str(text).strip().lower())
        slug = re.sub(r"[^\w\-]+", "-", slug, flags=re.UNICODE)
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or "untitled-topic"

    def _topic_path(self, slug):
        return self.topic_dir / f"{slug}.md"

    def _split_front_matter(self, text):
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("\n---\n", 1)
        if len(parts) != 2:
            return {}, text
        metadata = yaml.safe_load(parts[0][4:]) or {}
        return metadata, parts[1]

    def _extract_section(self, body, heading):
        pattern = rf"(?ms)^{re.escape(heading)}\n(.*?)(?=^#|\Z)"
        match = re.search(pattern, body)
        if not match:
            return ""
        return match.group(1).strip()

    def _parse_bullet_list(self, section_text):
        return self._dedupe_strings(
            line[2:].strip()
            for line in section_text.splitlines()
            if line.strip().startswith("- ") and line[2:].strip()
        )

    def _parse_profile_section(self, section_text):
        profile = {}
        for line in section_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            content = stripped[2:].strip()
            if ":" not in content:
                continue
            key, value = content.split(":", 1)
            profile[key.strip()] = value.strip()
        return profile

    def _parse_topics_section(self, section_text):
        topics = []
        current = None
        for raw_line in section_text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("- slug:"):
                if current:
                    topics.append(current)
                current = {"slug": stripped.split(":", 1)[1].strip()}
                continue
            if current and line.startswith("  ") and ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip()
        if current:
            topics.append(current)

        for topic in topics:
            topic["keywords"] = self._dedupe_strings(
                keyword.strip()
                for keyword in topic.get("keywords", "").split(",")
                if keyword.strip()
            )
        return topics

    def _render_topic(self, topic_doc):
        metadata = {
            "slug": topic_doc["metadata"].get("slug"),
            "title": topic_doc["metadata"].get("title"),
            "created_at": topic_doc["metadata"].get("created_at"),
            "updated_at": topic_doc["metadata"].get("updated_at"),
            "last_mentioned": topic_doc["metadata"].get("last_mentioned"),
            "keywords": self._dedupe_strings(topic_doc["metadata"].get("keywords", [])),
            "aliases": self._dedupe_strings(topic_doc["metadata"].get("aliases", [])),
            "source_days": self._dedupe_strings(topic_doc["metadata"].get("source_days", [])),
            "merged_from": self._dedupe_strings(topic_doc["metadata"].get("merged_from", [])),
        }
        front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        summary = topic_doc.get("summary", "").strip()
        facts = self._dedupe_strings(topic_doc.get("facts", []))
        open_questions = self._dedupe_strings(topic_doc.get("open_questions", []))
        timeline = self._dedupe_strings(topic_doc.get("timeline", []))

        lines = [
            "---",
            front_matter,
            "---",
            "",
            "# Summary",
            summary,
            "",
            "## Key Facts",
        ]
        lines.extend(f"- {fact}" for fact in facts)
        lines.extend([
            "",
            "## Open Questions",
        ])
        lines.extend(f"- {question}" for question in open_questions)
        lines.extend([
            "",
            "## Timeline",
        ])
        lines.extend(f"- {event}" for event in timeline)
        lines.append("")
        return "\n".join(lines)

    def _write_topic(self, topic_doc):
        slug = topic_doc["metadata"]["slug"]
        self._topic_path(slug).write_text(self._render_topic(topic_doc), encoding="utf-8")

    def read_memory_markdown(self):
        if not self.index_path.exists():
            self.rebuild_memory_index(profile={}, important_facts=[])
        return self.index_path.read_text(encoding="utf-8")

    def read_soul_markdown(self):
        self._ensure_soul_file_exists()
        return self.soul_path.read_text(encoding="utf-8")

    def read_index(self):
        memory_markdown = self.read_memory_markdown()
        metadata, body = self._split_front_matter(memory_markdown)
        return {
            "metadata": metadata,
            "profile": self._parse_profile_section(self._extract_section(body, "## User Profile")),
            "important_facts": self._parse_bullet_list(self._extract_section(body, "## Important Facts")),
            "topics": self._parse_topics_section(self._extract_section(body, "## Topics")),
        }

    def read_topic(self, slug):
        path = self._topic_path(slug)
        if not path.exists():
            return None
        metadata, body = self._split_front_matter(path.read_text(encoding="utf-8"))
        metadata.setdefault("slug", slug)
        metadata.setdefault("title", slug)
        metadata.setdefault("keywords", [])
        metadata.setdefault("aliases", [])
        metadata.setdefault("source_days", [])
        metadata.setdefault("merged_from", [])
        return {
            "metadata": metadata,
            "summary": self._extract_section(body, "# Summary"),
            "facts": self._parse_bullet_list(self._extract_section(body, "## Key Facts")),
            "open_questions": self._parse_bullet_list(self._extract_section(body, "## Open Questions")),
            "timeline": self._parse_bullet_list(self._extract_section(body, "## Timeline")),
        }

    def read_topics_metadata(self):
        topics = []
        for path in sorted(self.topic_dir.glob("*.md"), key=lambda item: item.stem):
            topic_doc = self.read_topic(path.stem)
            if topic_doc is None:
                continue
            metadata = topic_doc["metadata"]
            updated_at = metadata.get("updated_at") or metadata.get("last_mentioned") or ""
            last_updated = updated_at.split("T", 1)[0] if "T" in updated_at else updated_at
            topics.append({
                "slug": metadata.get("slug", path.stem),
                "title": metadata.get("title", path.stem),
                "file": f"topic/{path.name}",
                "summary": self._single_line(topic_doc.get("summary", "")),
                "keywords": self._dedupe_strings(metadata.get("keywords", [])),
                "last_updated": last_updated,
            })

        return sorted(
            topics,
            key=lambda topic: (topic.get("last_updated", ""), topic.get("slug", "")),
            reverse=True,
        )

    def upsert_topics(self, summary_payload):
        source_day = summary_payload.get("source_day") or self._today()
        topic_summaries = summary_payload.get("topics") or []
        for topic_summary in topic_summaries:
            if not isinstance(topic_summary, dict):
                continue

            action = str(topic_summary.get("action", "update")).lower()
            if action == "ignore":
                continue

            title = str(topic_summary.get("title") or topic_summary.get("slug") or "Untitled topic").strip()
            slug = self._slugify(topic_summary.get("slug") or title)
            existing_topic = self.read_topic(slug)
            existing_metadata = existing_topic.get("metadata", {}) if existing_topic else {}
            now = self.current_timestamp()

            timeline = self._dedupe_strings(existing_topic.get("timeline", []) if existing_topic else [])
            timeline_entry = topic_summary.get("timeline_entry")
            if timeline_entry:
                timeline.append(str(timeline_entry).strip())
            elif existing_topic is None:
                timeline.append(f"{source_day}: initial topic created from daily summary.")
            else:
                timeline.append(f"{source_day}: topic updated from daily summary.")

            topic_doc = {
                "metadata": {
                    "slug": slug,
                    "title": title,
                    "created_at": existing_metadata.get("created_at", now),
                    "updated_at": now,
                    "last_mentioned": source_day,
                    "keywords": self._dedupe_strings(
                        list(existing_metadata.get("keywords", []))
                        + list(topic_summary.get("keywords") or [])
                    ),
                    "aliases": self._dedupe_strings(
                        list(existing_metadata.get("aliases", []))
                        + list(topic_summary.get("aliases") or [])
                    ),
                    "source_days": self._dedupe_strings(
                        list(existing_metadata.get("source_days", []))
                        + [source_day]
                        + list(topic_summary.get("source_days") or [])
                    ),
                    "merged_from": self._dedupe_strings(
                        list(existing_metadata.get("merged_from", []))
                        + list(topic_summary.get("merged_from") or [])
                    ),
                },
                "summary": str(
                    topic_summary.get("summary")
                    or (existing_topic.get("summary") if existing_topic else "")
                    or summary_payload.get("conversation_summary")
                    or ""
                ).strip(),
                "facts": self._dedupe_strings(
                    (existing_topic.get("facts", []) if existing_topic else [])
                    + list(topic_summary.get("facts") or [])
                ),
                "open_questions": self._dedupe_strings(
                    (existing_topic.get("open_questions", []) if existing_topic else [])
                    + list(topic_summary.get("open_questions") or [])
                ),
                "timeline": self._dedupe_strings(timeline),
            }
            self._write_topic(topic_doc)

    def rebuild_memory_index(
        self,
        profile=None,
        important_facts=None,
        last_topic_merge_at=None,
        summaries_since_merge=None,
    ):
        existing_index = {"metadata": {}, "profile": {}, "important_facts": []}
        if self.index_path.exists():
            existing_index = self.read_index()

        profile = profile if profile is not None else dict(existing_index.get("profile", {}))
        important_facts = (
            important_facts
            if important_facts is not None
            else list(existing_index.get("important_facts", []))
        )
        topics = self.read_topics_metadata()
        metadata = {
            "updated_at": self.current_timestamp(),
            "topic_count": len(topics),
            "last_topic_merge_at": (
                last_topic_merge_at
                if last_topic_merge_at is not None
                else existing_index.get("metadata", {}).get("last_topic_merge_at")
            ),
            "summaries_since_merge": (
                summaries_since_merge
                if summaries_since_merge is not None
                else existing_index.get("metadata", {}).get("summaries_since_merge", 0)
            ),
        }
        front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()

        lines = [
            "---",
            front_matter,
            "---",
            "",
            "# MEMORY",
            "",
            "## User Profile",
        ]
        lines.extend(f"- {key}: {value}" for key, value in profile.items())
        lines.extend([
            "",
            "## Important Facts",
        ])
        lines.extend(f"- {fact}" for fact in self._dedupe_strings(important_facts))
        lines.extend([
            "",
            "## Topics",
        ])

        for topic in topics:
            lines.extend([
                f"- slug: {topic['slug']}",
                f"  title: {topic['title']}",
                f"  file: {topic['file']}",
                f"  summary: {self._single_line(topic['summary'])}",
                f"  keywords: {', '.join(topic.get('keywords', []))}",
                f"  last_updated: {topic.get('last_updated', '')}",
                "",
            ])

        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def merge_topics(self, merge_payload):
        merge_groups = merge_payload.get("merge_groups") or []
        changed = False

        for group in merge_groups:
            if not isinstance(group, dict):
                continue

            canonical_slug = self._slugify(group.get("canonical_slug") or group.get("title") or "")
            merged_slugs = self._dedupe_strings(
                self._slugify(slug)
                for slug in group.get("merged_slugs", [])
                if self._slugify(slug)
            )
            merged_slugs = [slug for slug in merged_slugs if slug != canonical_slug]
            if not merged_slugs:
                continue

            canonical_topic = self.read_topic(canonical_slug) if canonical_slug else None
            topic_docs = []
            if canonical_topic is not None:
                topic_docs.append(canonical_topic)
            for slug in merged_slugs:
                topic_doc = self.read_topic(slug)
                if topic_doc is not None:
                    topic_docs.append(topic_doc)

            if canonical_topic is None and topic_docs:
                canonical_topic = topic_docs[0]
                canonical_slug = canonical_topic["metadata"].get("slug", canonical_slug)

            if canonical_topic is None or len(topic_docs) < 2:
                continue

            keywords = []
            aliases = []
            source_days = []
            merged_from = []
            facts = []
            open_questions = []
            timeline = []
            summary_candidates = [group.get("summary")]

            for topic_doc in topic_docs:
                metadata = topic_doc.get("metadata", {})
                keywords.extend(metadata.get("keywords", []))
                aliases.extend(metadata.get("aliases", []))
                aliases.append(metadata.get("slug", ""))
                source_days.extend(metadata.get("source_days", []))
                merged_from.extend(metadata.get("merged_from", []))
                facts.extend(topic_doc.get("facts", []))
                open_questions.extend(topic_doc.get("open_questions", []))
                timeline.extend(topic_doc.get("timeline", []))
                summary_candidates.append(topic_doc.get("summary", ""))

            timeline.append(
                f"{self._today()}: merged topics {', '.join(merged_slugs)} into {canonical_slug}."
            )
            now = self.current_timestamp()
            merged_from.extend(merged_slugs)

            merged_doc = {
                "metadata": {
                    "slug": canonical_slug,
                    "title": str(
                        group.get("title")
                        or canonical_topic["metadata"].get("title")
                        or canonical_slug
                    ).strip(),
                    "created_at": canonical_topic["metadata"].get("created_at", now),
                    "updated_at": now,
                    "last_mentioned": self._today(),
                    "keywords": self._dedupe_strings(
                        keywords + list(group.get("keywords") or [])
                    ),
                    "aliases": self._dedupe_strings(aliases),
                    "source_days": self._dedupe_strings(source_days),
                    "merged_from": self._dedupe_strings(merged_from),
                },
                "summary": self._single_line(next((item for item in summary_candidates if item), "")),
                "facts": self._dedupe_strings(facts + list(group.get("facts") or [])),
                "open_questions": self._dedupe_strings(
                    open_questions + list(group.get("open_questions") or [])
                ),
                "timeline": self._dedupe_strings(timeline),
            }
            self._write_topic(merged_doc)

            for slug in merged_slugs:
                path = self._topic_path(slug)
                if path.exists():
                    path.unlink()
            changed = True

        self.rebuild_memory_index(
            last_topic_merge_at=self.current_timestamp(),
            summaries_since_merge=0,
        )
        return changed

    def update(self, summary, source_day=None):
        current_index = self.read_index()
        profile = dict(current_index.get("profile", {}))
        profile_updates = summary.get("profile_updates") or {}
        if isinstance(profile_updates, dict):
            for key, value in profile_updates.items():
                key = str(key).strip()
                if not key:
                    continue
                value_text = str(value).strip()
                if value_text:
                    profile[key] = value_text
                else:
                    profile.pop(key, None)

        important_facts = self._dedupe_strings(
            list(current_index.get("important_facts", []))
            + list(summary.get("important_facts") or [])
        )

        payload = dict(summary)
        if source_day is not None:
            payload["source_day"] = source_day
        self.upsert_topics(payload)

        summary_runs = int(current_index.get("metadata", {}).get("summaries_since_merge", 0) or 0) + 1
        self.rebuild_memory_index(
            profile=profile,
            important_facts=important_facts,
            summaries_since_merge=summary_runs,
        )

    def read(self):
        index = self.read_index()
        return {
            "metadata": index.get("metadata", {}),
            "profile": index.get("profile", {}),
            "important_facts": index.get("important_facts", []),
            "topics": self.read_topics_metadata(),
            "memory_markdown": self.read_memory_markdown(),
        }
