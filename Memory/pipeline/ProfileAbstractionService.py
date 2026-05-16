from Memory.projection.MarkdownProjector import MarkdownProjector
from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.repositories.L3Repository import L3Repository


class ProfileAbstractionService:
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

    def __init__(self, l1_repo=None, l2_repo=None, l3_repo=None, projector=None):
        self.l1_repo = l1_repo or L1Repository()
        self.l2_repo = l2_repo or L2Repository(self.l1_repo.db)
        self.l3_repo = l3_repo or L3Repository(self.l1_repo.db)
        self.projector = projector or MarkdownProjector(self.l1_repo, self.l2_repo, self.l3_repo)

    def apply_review_payload(self, review_payload, source_atom_id=None, source_scene_id=None):
        updated = False
        profile_updates = (review_payload or {}).get("profile_updates") or {}
        important_facts = (review_payload or {}).get("important_facts") or []
        retractions = (review_payload or {}).get("retractions") or []

        for field, value in profile_updates.items():
            field_name = str(field or "").strip()
            if not field_name:
                continue
            section = self._section_for_field(field_name)
            if section == "Preferences":
                if self.l3_repo.add_list_fact(section, f"{field_name.split('.', 1)[-1]}: {value}", source_atom_id=source_atom_id, source_scene_id=source_scene_id):
                    updated = True
            else:
                if self.l3_repo.set_profile_value(section, field_name, value, source_atom_id=source_atom_id, source_scene_id=source_scene_id):
                    updated = True

        for fact in important_facts:
            if self.l3_repo.add_list_fact("Objective Facts", fact, source_atom_id=source_atom_id, source_scene_id=source_scene_id):
                updated = True

        for field in retractions:
            field_name = str(field or "").strip()
            if not field_name:
                continue
            section = self._section_for_field(field_name)
            self.l3_repo.retract_field(section, field_name)
            updated = True

        self.projector.project_all()
        return {
            "updated": updated,
            "profile": self.l3_repo.build_sectioned_profile(),
        }

    def rebuild_from_atoms(self, limit=100):
        atoms = self.l1_repo.list_atoms(atom_types=["user_profile", "preference", "fact"], limit=limit)
        updated = False
        for atom in atoms:
            atom_type = atom.get("atom_type")
            text = atom.get("canonical_text") or ""
            if atom_type == "preference":
                if self.l3_repo.add_list_fact("Preferences", text, source_atom_id=atom.get("id"), source_scene_id=atom.get("scene_id")):
                    updated = True
            elif atom_type == "user_profile" and ":" in text:
                field, value = text.split(":", 1)
                section = self._section_for_field(field)
                if self.l3_repo.set_profile_value(section, field.strip(), value.strip(), source_atom_id=atom.get("id"), source_scene_id=atom.get("scene_id")):
                    updated = True
            elif atom_type == "fact":
                if self.l3_repo.add_list_fact("Objective Facts", text, source_atom_id=atom.get("id"), source_scene_id=atom.get("scene_id")):
                    updated = True
        self.projector.project_all()
        return updated

    def _section_for_field(self, field_name):
        if str(field_name).startswith("preferences."):
            return "Preferences"
        return self.FIELD_SECTION_MAP.get(str(field_name), "Objective Facts")
