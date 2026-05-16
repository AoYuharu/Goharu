from datetime import datetime

from Memory.MemoryDB import MemoryDB


class L3Repository:
    def __init__(self, db=None):
        self.db = db or MemoryDB()

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat()

    def set_profile_value(
        self,
        section,
        field,
        value,
        profile_type="user",
        confidence=1.0,
        source_atom_id=None,
        source_scene_id=None,
        status="active",
    ):
        section = str(section or "Objective Facts")
        field = str(field or "value")
        value = str(value or "").strip()
        if not value:
            return None
        self.db.execute(
            """
            UPDATE l3_profiles
            SET status = 'inactive'
            WHERE profile_type = ? AND section = ? AND field = ? AND status = 'active'
            """,
            (str(profile_type), section, field),
        )
        cursor = self.db.execute(
            """
            INSERT INTO l3_profiles(
                profile_type, section, field, value,
                confidence, source_atom_id, source_scene_id, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(profile_type),
                section,
                field,
                value,
                float(confidence or 0.0),
                source_atom_id,
                source_scene_id,
                str(status or "active"),
                self.now(),
            ),
        )
        row = self.db.query_one("SELECT * FROM l3_profiles WHERE id = ?", (cursor.lastrowid,))
        profile_row = dict(row) if row is not None else None
        if profile_row is not None:
            self.db.replace_fts_entry("l3", profile_row["id"], f"{field}: {value}")
        return profile_row

    def add_list_fact(
        self,
        section,
        value,
        profile_type="user",
        confidence=1.0,
        source_atom_id=None,
        source_scene_id=None,
        status="active",
    ):
        value = str(value or "").strip()
        if not value:
            return None
        existing = self.db.query_one(
            """
            SELECT * FROM l3_profiles
            WHERE profile_type = ? AND section = ? AND field = ? AND value = ? AND status = ?
            ORDER BY id DESC LIMIT 1
            """,
            (str(profile_type), str(section), "item", value, str(status or "active")),
        )
        if existing is not None:
            return dict(existing)
        cursor = self.db.execute(
            """
            INSERT INTO l3_profiles(
                profile_type, section, field, value,
                confidence, source_atom_id, source_scene_id, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(profile_type),
                str(section),
                "item",
                value,
                float(confidence or 0.0),
                source_atom_id,
                source_scene_id,
                str(status or "active"),
                self.now(),
            ),
        )
        row = self.db.query_one("SELECT * FROM l3_profiles WHERE id = ?", (cursor.lastrowid,))
        profile_row = dict(row) if row is not None else None
        if profile_row is not None:
            self.db.replace_fts_entry("l3", profile_row["id"], value)
        return profile_row

    def retract_field(self, section, field, profile_type="user"):
        self.db.execute(
            """
            UPDATE l3_profiles
            SET status = 'inactive', updated_at = ?
            WHERE profile_type = ? AND section = ? AND field = ? AND status = 'active'
            """,
            (self.now(), str(profile_type), str(section), str(field)),
        )

    def list_profile_rows(self, profile_type="user", status="active"):
        sql = "SELECT * FROM l3_profiles WHERE profile_type = ?"
        params = [str(profile_type)]
        if status is not None:
            sql += " AND status = ?"
            params.append(str(status))
        sql += " ORDER BY section ASC, field ASC, id ASC"
        rows = self.db.query_all(sql, tuple(params))
        return [dict(row) for row in rows]

    def build_sectioned_profile(self, profile_type="user", status="active"):
        rows = self.list_profile_rows(profile_type=profile_type, status=status)
        sections = {
            "Identity": {},
            "Work": {},
            "Location": {},
            "Preferences": [],
            "Objective Facts": [],
        }
        for row in rows:
            section = row.get("section") or "Objective Facts"
            field = row.get("field") or "item"
            value = row.get("value") or ""
            if section in {"Preferences", "Objective Facts"}:
                sections.setdefault(section, [])
                sections[section].append(value)
            elif field == "item":
                sections.setdefault(section, [])
                if isinstance(sections[section], list):
                    sections[section].append(value)
            else:
                sections.setdefault(section, {})
                if isinstance(sections[section], dict):
                    sections[section][field] = value
        return sections

    def build_compact_summary(self, profile_type="user"):
        sections = self.build_sectioned_profile(profile_type=profile_type, status="active")
        lines = []
        for section_name in ["Identity", "Work", "Location"]:
            values = sections.get(section_name) or {}
            if isinstance(values, dict) and values:
                rendered = "; ".join(f"{key}: {value}" for key, value in values.items())
                lines.append(f"{section_name}: {rendered}")
        preferences = sections.get("Preferences") or []
        if preferences:
            lines.append("Preferences: " + "; ".join(preferences[:5]))
        facts = sections.get("Objective Facts") or []
        if facts:
            lines.append("Facts: " + "; ".join(facts[:5]))
        return "\n".join(lines)
