from collections import defaultdict

from Memory.projection.MarkdownProjector import MarkdownProjector
from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.repositories.L3Repository import L3Repository
from Memory.retrieval.EmbeddingService import EmbeddingService


class SceneAggregationService:
    def __init__(self, l1_repo=None, l2_repo=None, embedding_service=None, projector=None):
        self.l1_repo = l1_repo or L1Repository()
        self.l2_repo = l2_repo or L2Repository(self.l1_repo.db)
        self.l3_repo = L3Repository(self.l1_repo.db)
        self.embedding_service = embedding_service or EmbeddingService(self.l1_repo.db)
        self.projector = projector or MarkdownProjector(self.l1_repo, self.l2_repo, self.l3_repo)

    def consolidate(self, scene_payload=None):
        scenes = []
        if scene_payload:
            for candidate in scene_payload:
                scene = self._store_scene(candidate)
                if scene is not None:
                    scenes.append(scene)
        else:
            scenes = self._build_scenes_from_atoms()
        if scenes:
            self.projector.project_all()
        return scenes

    def _store_scene(self, payload):
        if not isinstance(payload, dict):
            return None
        scene = self.l2_repo.upsert_scene(
            slug=payload.get("slug"),
            title=payload.get("title") or payload.get("slug") or "Untitled topic",
            summary=payload.get("summary") or "",
            scene_type=payload.get("scene_type") or "topic",
            keywords=payload.get("keywords") or [],
            aliases=payload.get("aliases") or [],
            importance=payload.get("importance", 0.5),
            last_mentioned_at=payload.get("last_mentioned_at") or "",
        )
        members = []
        for atom_id in payload.get("atom_ids") or []:
            self.l2_repo.add_member(scene["id"], atom_id, source_turn_id=payload.get("source_turn_id"), weight=1.0, reason="payload")
            self.l1_repo.set_scene(atom_id, scene["id"])
            members.append(atom_id)
        self.embedding_service.store_embedding("l2", scene["id"], scene.get("summary") or scene.get("title") or "")
        return scene

    def _build_scenes_from_atoms(self):
        grouped = defaultdict(list)
        atoms = self.l1_repo.list_atoms(limit=200, only_unassigned=True)
        for atom in atoms:
            token = atom.get("subject") or atom.get("slot") or atom.get("atom_type") or "general"
            grouped[str(token).strip().lower()].append(atom)
        scenes = []
        for key, items in grouped.items():
            if not items:
                continue
            title = items[0].get("subject") or items[0].get("slot") or key.title()
            summary = " ".join(item.get("canonical_text") or "" for item in items[:4]).strip()
            keywords = []
            for item in items[:6]:
                keywords.extend((item.get("canonical_text") or "").split()[:3])
            scene = self.l2_repo.upsert_scene(
                slug=key.replace(" ", "-"),
                title=title,
                summary=summary,
                keywords=keywords[:8],
                importance=max(float(item.get("salience") or 0.0) for item in items),
            )
            members = []
            for atom in items:
                self.l2_repo.add_member(scene["id"], atom["id"], source_turn_id=atom.get("source_turn_id"), weight=atom.get("salience") or 0.5, reason="heuristic_group")
                self.l1_repo.set_scene(atom["id"], scene["id"])
                members.append(atom["id"])
            self.embedding_service.store_embedding("l2", scene["id"], scene.get("summary") or scene.get("title") or "")
            scenes.append(scene)
        return scenes
