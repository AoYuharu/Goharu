from datetime import datetime

from Memory.MemoryDB import MemoryDB
from Memory.retrieval.BM25Encoder import BM25Encoder


class L2Repository:
    def __init__(self, db=None, bm25_encoder=None):
        self.db = db or MemoryDB()
        self.bm25 = bm25_encoder or BM25Encoder()

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat()

    def upsert_scene(
        self,
        title,
        summary="",
        scene_type="topic",
        slug=None,
        keywords=None,
        aliases=None,
        importance=0.5,
        status="active",
        last_mentioned_at="",
        scene_id=None,
    ):
        timestamp = self.now()
        slug = str(slug or title or "scene").strip().lower().replace(" ", "-")
        keywords_json = self.db.dumps_json(list(keywords or []))
        aliases_json = self.db.dumps_json(list(aliases or []))
        last_mentioned_at = str(last_mentioned_at or timestamp)

        existing = None
        if scene_id is not None:
            existing = self.get_scene(scene_id)
        if existing is None:
            existing = self.get_scene_by_slug(slug)

        if existing is not None:
            self.db.execute(
                """
                UPDATE l2_scenes
                SET slug = ?, scene_type = ?, title = ?, summary = ?,
                    keywords_json = ?, aliases_json = ?, importance = ?, status = ?,
                    last_mentioned_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    slug,
                    str(scene_type),
                    str(title or slug),
                    str(summary or ""),
                    keywords_json,
                    aliases_json,
                    float(importance or 0.0),
                    str(status or "active"),
                    last_mentioned_at,
                    timestamp,
                    int(existing["id"]),
                ),
            )
            scene = self.get_scene(existing["id"])
        else:
            cursor = self.db.execute(
                """
                INSERT INTO l2_scenes(
                    slug, scene_type, title, summary,
                    keywords_json, aliases_json, importance, status,
                    last_mentioned_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    str(scene_type),
                    str(title or slug),
                    str(summary or ""),
                    keywords_json,
                    aliases_json,
                    float(importance or 0.0),
                    str(status or "active"),
                    last_mentioned_at,
                    timestamp,
                    timestamp,
                ),
            )
            scene = self.get_scene(cursor.lastrowid)
        if scene is not None:
            search_text = "\n".join(
                [
                    scene.get("title") or "",
                    scene.get("summary") or "",
                    " ".join(scene.get("keywords", [])),
                    " ".join(scene.get("aliases", [])),
                ]
            )
            self.db.replace_fts_entry("l2", scene["id"], search_text)
            # BM25 sparse vector
            tf_vec = self.bm25.encode_document(search_text)
            if tf_vec:
                token_count = len(list(self.bm25._tokenize(search_text)))
                self.db.replace_bm25_entry("l2", scene["id"], tf_vec, token_count)
        return scene

    def get_scene(self, scene_id):
        row = self.db.query_one("SELECT * FROM l2_scenes WHERE id = ?", (int(scene_id),))
        return self._row_to_scene(row) if row is not None else None

    def get_scene_by_slug(self, slug):
        row = self.db.query_one("SELECT * FROM l2_scenes WHERE slug = ?", (str(slug),))
        return self._row_to_scene(row) if row is not None else None

    def list_scenes(self, status="active", limit=None):
        sql = "SELECT * FROM l2_scenes"
        params = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(str(status))
        sql += " ORDER BY importance DESC, updated_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.db.query_all(sql, tuple(params))
        return [self._row_to_scene(row) for row in rows]

    def add_member(self, scene_id, atom_id, source_turn_id=None, weight=1.0, reason=""):
        self.db.execute(
            """
            INSERT OR REPLACE INTO l2_scene_members(scene_id, atom_id, source_turn_id, weight, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(scene_id), int(atom_id), source_turn_id, float(weight or 0.0), str(reason or "")),
        )

    def list_members(self, scene_id):
        rows = self.db.query_all(
            "SELECT * FROM l2_scene_members WHERE scene_id = ? ORDER BY weight DESC, atom_id ASC",
            (int(scene_id),),
        )
        return [dict(row) for row in rows]

    def replace_members(self, scene_id, members):
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM l2_scene_members WHERE scene_id = ?", (int(scene_id),))
            for member in members or []:
                connection.execute(
                    """
                    INSERT INTO l2_scene_members(scene_id, atom_id, source_turn_id, weight, reason)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(scene_id),
                        int(member.get("atom_id")),
                        member.get("source_turn_id"),
                        float(member.get("weight") or 0.0),
                        str(member.get("reason") or ""),
                    ),
                )

    def delete_scene(self, scene_id):
        self.db.execute("DELETE FROM l2_scenes WHERE id = ?", (int(scene_id),))
        self.db.remove_fts_entry("l2", scene_id)
        self.db.remove_bm25_entry("l2", scene_id)

    def _row_to_scene(self, row):
        data = dict(row)
        data["keywords"] = self.db.loads_json(data.get("keywords_json"), default=[]) or []
        data["aliases"] = self.db.loads_json(data.get("aliases_json"), default=[]) or []
        return data
