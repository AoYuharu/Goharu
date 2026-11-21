from datetime import datetime

from Memory.MemoryDB import MemoryDB
from Memory.retrieval.BM25Encoder import BM25Encoder
from Memory import change_events


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
        salience=0.5,
        status="active",
        scene_id=None,
        atom_id=None,
    ):
        timestamp = self.now()
        audit = change_events.has_l1_listener()
        if atom_id is not None:
            old_row = self.get_atom(atom_id) if audit else None
            self.db.execute(
                """
                UPDATE l1_memory_atoms
                SET atom_type = ?, subject = ?, slot = ?, canonical_text = ?,
                    source_turn_id = ?, source_message_id = ?, salience = ?,
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
                    float(salience or 0.0),
                    str(status or "active"),
                    scene_id,
                    timestamp,
                    int(atom_id),
                ),
            )
            atom = self.get_atom(atom_id)
            if audit:
                change_events.emit_l1_change("update", int(atom_id), old_row, atom)
        else:
            cursor = self.db.execute(
                """
                INSERT INTO l1_memory_atoms(
                    atom_type, subject, slot, canonical_text,
                    source_turn_id, source_message_id,
                    salience, status, scene_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(atom_type),
                    str(subject or ""),
                    str(slot or ""),
                    str(canonical_text or ""),
                    source_turn_id,
                    source_message_id,
                    float(salience or 0.0),
                    str(status or "active"),
                    scene_id,
                    timestamp,
                    timestamp,
                ),
            )
            atom = self.get_atom(cursor.lastrowid)
            if audit and atom is not None:
                change_events.emit_l1_change("insert", int(atom["id"]), None, atom)
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
        audit = change_events.has_l1_listener()
        old_row = self.get_atom(atom_id) if audit else None
        self.db.execute(
            "UPDATE l1_memory_atoms SET status = ?, updated_at = ? WHERE id = ?",
            ("inactive", self.now(), int(atom_id)),
        )
        self.db.remove_fts_entry("l1", atom_id)
        self.db.remove_bm25_entry("l1", atom_id)
        if audit:
            change_events.emit_l1_change("deactivate", int(atom_id), old_row, None)

    def restore_atom(self, old_row, atom_id):
        """回退专用：把 atom 恢复到 old_row 状态（三表同步）。返回恢复前的行（redo 用）。

        old_row=None → 逆 insert：物理删除该 atom 及其 fts/bm25 侧表条目。
        与 upsert 一致的非原子三段写（行→fts→bm25 各自提交），失败处记日志。
        """
        import logging

        logger = logging.getLogger(__name__)
        atom_id = int(atom_id)
        prev = self.get_atom(atom_id)
        if old_row is None:
            self.db.execute("DELETE FROM l1_memory_atoms WHERE id = ?", (atom_id,))
            self.db.remove_fts_entry("l1", atom_id)
            self.db.remove_bm25_entry("l1", atom_id)
            logger.info("restore_atom: atom %s 已物理删除（逆 insert）", atom_id)
            return prev
        self.db.execute(
            """
            INSERT OR REPLACE INTO l1_memory_atoms(
                id, atom_type, subject, slot, canonical_text,
                source_turn_id, source_message_id,
                salience, status, scene_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atom_id,
                str(old_row.get("atom_type") or ""),
                str(old_row.get("subject") or ""),
                str(old_row.get("slot") or ""),
                str(old_row.get("canonical_text") or ""),
                old_row.get("source_turn_id"),
                old_row.get("source_message_id"),
                float(old_row.get("salience") or 0.0),
                str(old_row.get("status") or "active"),
                old_row.get("scene_id"),
                str(old_row.get("created_at") or self.now()),
                str(old_row.get("updated_at") or self.now()),
            ),
        )
        text = str(old_row.get("canonical_text") or "")
        self.db.replace_fts_entry("l1", atom_id, text)
        tf_vec = self.bm25.encode_document(text)
        if tf_vec:
            token_count = len(list(self.bm25._tokenize(text)))
            self.db.replace_bm25_entry("l1", atom_id, tf_vec, token_count)
        else:
            self.db.remove_bm25_entry("l1", atom_id)
        logger.info("restore_atom: atom %s 已恢复到历史行", atom_id)
        return prev
