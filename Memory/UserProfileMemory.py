import re
from datetime import datetime
from pathlib import Path

import yaml

from configurationLoader import config


class UserProfileMemory:
    FIELD_SECTION_MAP = {
        "name": "Identity",
        "age": "Identity",
        "gender": "Identity",
        "pronouns": "Identity",
        "title": "Identity",
        "employer": "Work",
        "occupation": "Work",
        "role": "Work",
        "job_title": "Work",
        "company": "Work",
        "location": "Location",
        "city": "Location",
        "home_address": "Location",
        "address": "Location",
    }
    SECTION_ORDER = ["Identity", "Work", "Location", "Preferences", "Objective Facts"]

    def __init__(self):
        root_dir = Path(config.get("memory.root_dir", "./runtime_memory"))
        self.path = Path(config.get("memory.user.path", str(root_dir / "USER.md")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_profile(self._default_profile())
        self.turns_since_review = 0

    @staticmethod
    def current_timestamp():
        return datetime.now().replace(microsecond=0).isoformat()

    def _default_profile(self):
        now = self.current_timestamp()
        return {
            "metadata": {
                "updated_at": now,
                "last_reviewed_at": "",
                "source_turn": "",
            },
            "sections": {
                "Identity": {},
                "Work": {},
                "Location": {},
                "Preferences": [],
                "Objective Facts": [],
            },
        }

    @staticmethod
    def _split_front_matter(text):
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("\n---\n", 1)
        if len(parts) != 2:
            return {}, text
        metadata = yaml.safe_load(parts[0][4:]) or {}
        return metadata, parts[1]

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
    def _extract_section(body, heading):
        pattern = rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
        match = re.search(pattern, body)
        if not match:
            return ""
        return match.group(1).strip()

    def _parse_key_value_section(self, section_text):
        values = {}
        for line in section_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            content = stripped[2:].strip()
            if ":" not in content:
                continue
            key, value = content.split(":", 1)
            key_text = str(key).strip()
            value_text = str(value).strip()
            if key_text and value_text:
                values[key_text] = value_text
        return values

    def _parse_list_section(self, section_text):
        return self._dedupe_strings(
            line[2:].strip()
            for line in section_text.splitlines()
            if line.strip().startswith("- ") and line[2:].strip()
        )

    def read_profile(self):
        if not self.path.exists():
            self._write_profile(self._default_profile())
        text = self.path.read_text(encoding="utf-8")
        metadata, body = self._split_front_matter(text)
        sections = {
            "Identity": self._parse_key_value_section(self._extract_section(body, "Identity")),
            "Work": self._parse_key_value_section(self._extract_section(body, "Work")),
            "Location": self._parse_key_value_section(self._extract_section(body, "Location")),
            "Preferences": self._parse_list_section(self._extract_section(body, "Preferences")),
            "Objective Facts": self._parse_list_section(self._extract_section(body, "Objective Facts")),
        }
        return {
            "metadata": {
                "updated_at": metadata.get("updated_at", ""),
                "last_reviewed_at": metadata.get("last_reviewed_at", ""),
                "source_turn": metadata.get("source_turn", ""),
            },
            "sections": sections,
        }

    def read_markdown(self):
        if not self.path.exists():
            self._write_profile(self._default_profile())
        return self.path.read_text(encoding="utf-8")

    def _write_profile(self, profile):
        metadata = {
            "updated_at": profile.get("metadata", {}).get("updated_at", ""),
            "last_reviewed_at": profile.get("metadata", {}).get("last_reviewed_at", ""),
            "source_turn": profile.get("metadata", {}).get("source_turn", ""),
        }
        front_matter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        sections = profile.get("sections", {})
        lines = [
            "---",
            front_matter,
            "---",
            "",
            "# USER",
            "",
        ]

        for section_name in self.SECTION_ORDER:
            lines.append(f"## {section_name}")
            section_value = sections.get(section_name)
            if isinstance(section_value, dict):
                for key, value in section_value.items():
                    key_text = str(key).strip()
                    value_text = str(value).strip()
                    if key_text and value_text:
                        lines.append(f"- {key_text}: {value_text}")
            else:
                for item in self._dedupe_strings(section_value or []):
                    lines.append(f"- {item}")
            lines.append("")

        self.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _canonical_field_name(self, key):
        return str(key or "").strip()

    def _section_for_field(self, field_name):
        if field_name.startswith("preferences."):
            return "Preferences"
        return self.FIELD_SECTION_MAP.get(field_name, "Objective Facts")

    def _apply_scalar_update(self, sections, field_name, value, conflicts):
        value_text = str(value or "").strip()
        if not value_text:
            return False

        section_name = self._section_for_field(field_name)
        if section_name in {"Preferences", "Objective Facts"}:
            target_list = sections[section_name]
            candidate = value_text if section_name == "Objective Facts" else f"{field_name.split('.', 1)[-1]}: {value_text}"
            before = len(target_list)
            sections[section_name] = self._dedupe_strings(list(target_list) + [candidate])
            return len(sections[section_name]) != before

        existing_value = sections[section_name].get(field_name, "")
        if not existing_value or existing_value == value_text:
            sections[section_name][field_name] = value_text
            return existing_value != value_text

        for conflict in conflicts or []:
            if str(conflict.get("field") or "").strip() != field_name:
                continue
            reason = str(conflict.get("reason") or "").strip()
            candidate_value = str(conflict.get("candidate_value") or "").strip()
            if candidate_value == value_text and reason == "explicit_correction":
                sections[section_name][field_name] = value_text
                return True
        return False

    def _apply_retraction(self, sections, field_name):
        section_name = self._section_for_field(field_name)
        if section_name in {"Preferences", "Objective Facts"}:
            target_list = sections[section_name]
            before = len(target_list)
            suffix = field_name.split(".", 1)[-1]
            sections[section_name] = [
                item for item in target_list
                if item != field_name and not item.startswith(f"{suffix}:")
            ]
            return len(sections[section_name]) != before

        if field_name in sections[section_name]:
            sections[section_name].pop(field_name, None)
            return True
        return False

    def apply(self, review_payload, source_turn=""):
        profile = self.read_profile()
        sections = profile["sections"]
        changed = False

        profile_updates = review_payload.get("profile_updates") or {}
        conflicts = review_payload.get("conflicts") or []
        retractions = review_payload.get("retractions") or []
        important_facts = review_payload.get("important_facts") or []

        if isinstance(profile_updates, dict):
            for raw_key, raw_value in profile_updates.items():
                field_name = self._canonical_field_name(raw_key)
                if not field_name:
                    continue
                if self._apply_scalar_update(sections, field_name, raw_value, conflicts):
                    changed = True

        if important_facts:
            before = len(sections["Objective Facts"])
            sections["Objective Facts"] = self._dedupe_strings(
                list(sections["Objective Facts"]) + [str(fact).strip() for fact in important_facts if str(fact).strip()]
            )
            if len(sections["Objective Facts"]) != before:
                changed = True

        for field_name in retractions:
            if self._apply_retraction(sections, self._canonical_field_name(field_name)):
                changed = True

        now = self.current_timestamp()
        profile["metadata"]["last_reviewed_at"] = now
        profile["metadata"]["source_turn"] = str(source_turn or "").strip()
        if changed:
            profile["metadata"]["updated_at"] = now
        else:
            profile["metadata"].setdefault("updated_at", now)

        self._write_profile(profile)
        self.turns_since_review = 0
        return {
            "updated": changed,
            "path": str(self.path),
            "profile": profile,
        }

    def should_review(self):
        review_interval = max(1, int(config.get("memory.user.review_interval", 10)))
        self.turns_since_review += 1
        return self.turns_since_review >= review_interval
