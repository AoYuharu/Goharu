from datetime import datetime

from Memory.MemoryDB import MemoryDB
from Memory.retrieval.BM25Encoder import BM25Encoder


class L1Repository:
    def __init__(self, db=None, bm25_encoder=None):
        self.db = db or MemoryDB()
        self.bm25 = bm25_encoder or BM25Encoder()

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat()

    def upsert_atom(
        self,
        atom_type,
        canonical_text,
        subject="",
        slot="",
        source_turn_id=None,
        source_message_id=None,
        confidence=1.0,
        salience=0.5,
        status="active",
        scene_id=None,
        atom_id=None,
    ):
        timestamp = self.now()
        if atom_id is not None:
            self.db.execute(
                """
                UPDATE l1_memory_atoms
                SET atom_type = ?, subject = ?, slot = ?, canonical_text = ?,
                    source_turn_id = ?, source_message_id = ?, confidence = ?, salience = ?,
                    status = ?, scene_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(atom_type),
                    str(subject or ""),
                    str(slot or ""),
                    str(canonical_text or ""),
                    source_turn_id,
                    source_message_id,
                    float(confidence or 0.0),
                    float(salience or 0.0),
                    str(status or "active"),
                    scene_id,
                    timestamp,
                    int(atom_id),
                ),
            )
            atom = self.get_atom(atom_id)
        else:
            cursor = self.db.execute(
                """
                INSERT INTO l1_memory_atoms(
                    atom_type, subject, slot, canonical_text,
                    source_turn_id, source_message_id,
                    confidence, salience, status, scene_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(atom_type),
                    str(subject or ""),
                    str(slot or ""),
                    str(canonical_text or ""),
                    source_turn_id,
                    source_message_id,
                    float(confidence or 0.0),
                    float(salience or 0.0),
                    str(status or "active"),
                    scene_id,
                    timestamp,
                    timestamp,
                ),
            )
            atom = self.get_atom(cursor.lastrowid)
        if atom is not None:
            text = atom.get("canonical_text") or ""
            self.db.replace_fts_entry("l1", atom["id"], text)
            # BM25 sparse vector
            tf_vec = self.bm25.encode_document(text)
            if tf_vec:
                token_count = len(list(self.bm25._tokenize(text)))
                self.db.replace_bm25_entry("l1", atom["id"], tf_vec, token_count)
        return atom

    def get_atom(self, atom_id):
        row = self.db.query_one("SELECT * FROM l1_memory_atoms WHERE id = ?", (int(atom_id),))
        return dict(row) if row is not None else None

    def list_atoms(self, atom_types=None, status="active", limit=None, scene_id=None, only_unassigned=False):
        sql = ["SELECT * FROM l1_memory_atoms WHERE 1=1"]
        params = []
        if status is not None:
            sql.append("AND status = ?")
            params.append(str(status))
        if atom_types:
            placeholders = ",".join("?" for _ in atom_types)
            sql.append(f"AND atom_type IN ({placeholders})")
            params.extend(str(item) for item in atom_types)
        if scene_id is not None:
            sql.append("AND scene_id = ?")
            params.append(int(scene_id))
        if only_unassigned:
            sql.append("AND (scene_id IS NULL OR scene_id = 0)")
        sql.append("ORDER BY salience DESC, updated_at DESC, id DESC")
        if limit is not None:
            sql.append("LIMIT ?")
            params.append(int(limit))
        rows = self.db.query_all("\n".join(sql), tuple(params))
        return [dict(row) for row in rows]

    def list_atoms_for_turn(self, turn_id, status=None):
        sql = "SELECT * FROM l1_memory_atoms WHERE source_turn_id = ?"
        params = [int(turn_id)]
        if status is not None:
            sql += " AND status = ?"
            params.append(str(status))
        sql += " ORDER BY id ASC"
        rows = self.db.query_all(sql, tuple(params))
        return [dict(row) for row in rows]

    def set_scene(self, atom_id, scene_id):
        self.db.execute(
            "UPDATE l1_memory_atoms SET scene_id = ?, updated_at = ? WHERE id = ?",
            (scene_id, self.now(), int(atom_id)),
        )

    def deactivate_atom(self, atom_id):
        self.db.execute(
            "UPDATE l1_memory_atoms SET status = ?, updated_at = ? WHERE id = ?",
            ("inactive", self.now(), int(atom_id)),
        )
        self.db.remove_fts_entry("l1", atom_id)
        self.db.remove_bm25_entry("l1", atom_id)
