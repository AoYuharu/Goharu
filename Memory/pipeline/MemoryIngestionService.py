from Memory.projection.MarkdownProjector import MarkdownProjector
from Memory.repositories.L0Repository import L0Repository
from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.repositories.L3Repository import L3Repository
from Memory.retrieval.EmbeddingService import EmbeddingService


class MemoryIngestionService:
    def __init__(self, l0_repo=None, l1_repo=None, embedding_service=None, projector=None):
        self.l0_repo = l0_repo or L0Repository()
        self.l1_repo = l1_repo or L1Repository(self.l0_repo.db)
        self.l2_repo = L2Repository(self.l0_repo.db)
        self.l3_repo = L3Repository(self.l0_repo.db)
        self.embedding_service = embedding_service or EmbeddingService(self.l0_repo.db)
        self.projector = projector or MarkdownProjector(self.l1_repo, self.l2_repo, self.l3_repo)

    def ingest_turn_atoms(self, turn_id, atom_payload=None):
        turn_id = int(turn_id)
        turn_messages = self.l0_repo.list_turn_messages(turn_id)
        if not turn_messages:
            turn = self.l0_repo.get_turn(turn_id)
            if turn is None:
                return []
            turn_messages = self.l0_repo.list_day_messages(
                turn.get("day_key"),
                session_id=turn.get("session_id", "default"),
            )
        atoms = []
        for atom in atom_payload or []:
            atoms.append(self._store_atom(turn_id, atom))
        if atoms:
            self.projector.project_all()
            self.l0_repo.mark_turn_pipeline_state(turn_id, "atoms_ready")
            return [atom for atom in atoms if atom is not None]
        heuristic_atoms = self._extract_heuristic_atoms(turn_id, turn_messages)
        if heuristic_atoms:
            self.projector.project_all()
            self.l0_repo.mark_turn_pipeline_state(turn_id, "atoms_ready")
        return heuristic_atoms

    def _store_atom(self, turn_id, atom):
        if not isinstance(atom, dict):
            return None
        stored = self.l1_repo.upsert_atom(
            atom_type=atom.get("atom_type") or atom.get("type") or "fact",
            subject=atom.get("subject") or "",
            slot=atom.get("slot") or "",
            canonical_text=atom.get("canonical_text") or atom.get("text") or "",
            source_turn_id=turn_id,
            source_message_id=atom.get("source_message_id"),
            confidence=atom.get("confidence", 1.0),
            salience=atom.get("salience", 0.5),
            status=atom.get("status", "active"),
        )
        if stored is not None:
            self.embedding_service.store_embedding("l1", stored["id"], stored.get("canonical_text") or "")
        return stored

    def _extract_heuristic_atoms(self, turn_id, messages):
        atoms = []
        for message in messages or []:
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                continue
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            for line in lines[:6]:
                atom_type = "fact" if role == "assistant" else "task_state"
                if role == "user" and any(token in line for token in ["喜欢", "偏好", "prefer", "please"]):
                    atom_type = "preference"
                elif any(token in line.lower() for token in ["must", "should", "不能", "不要"]):
                    atom_type = "constraint"
                atom = self.l1_repo.upsert_atom(
                    atom_type=atom_type,
                    subject=role,
                    slot="message_line",
                    canonical_text=line,
                    source_turn_id=turn_id,
                    confidence=0.55,
                    salience=0.4,
                )
                if atom is not None:
                    self.embedding_service.store_embedding("l1", atom["id"], atom.get("canonical_text") or "")
                    atoms.append(atom)
        return atoms
