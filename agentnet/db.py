import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config


class AgentNetDB:
    """SQLite store for the agent registry.

    Tables:
      - ``agents``:                 stable identity (+ optional freeform summary)
      - ``agent_capabilities``:     capability atoms, one row per discovered ability,
                                    soft-deletable via ``status`` (active/retired)
      - ``capability_events``:      audit log of every capability request + curator decision
      - ``agent_capability_fts``:   FTS5 index over name + capability (keyword search)
      - ``agent_capability_embeddings``: vector index over capability atoms
    """

    _jieba = None

    @classmethod
    def _get_jieba(cls):
        if cls._jieba is None:
            try:
                import jieba
                cls._jieba = jieba
            except ImportError:
                cls._jieba = False
        return cls._jieba if cls._jieba else None

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat()

    def tokenize_for_fts(self, text):
        jieba_mod = self._get_jieba()
        if jieba_mod:
            try:
                return " ".join(jieba_mod.cut_for_search(str(text or "")))
            except Exception:
                pass
        return str(text or "")

    def __init__(self, db_path=None):
        self.path = Path(db_path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.fts_enabled = False
        self.bootstrap()

    def connect(self):
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self.path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def execute(self, sql, params=()):
        connection = self.connect()
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor

    def query_all(self, sql, params=()):
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.connect().execute(sql, params).fetchone()

    def bootstrap(self):
        with self.transaction() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id      TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    summary       TEXT,
                    device_id     TEXT,
                    status        TEXT NOT NULL DEFAULT 'registering',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_capabilities (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
                    capability_id TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    description   TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'active',
                    version       INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    UNIQUE(agent_id, capability_id)
                );

                CREATE INDEX IF NOT EXISTS idx_cap_agent ON agent_capabilities(agent_id);

                CREATE TABLE IF NOT EXISTS capability_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id      TEXT NOT NULL,
                    capability_id TEXT,
                    action        TEXT NOT NULL,
                    decision_json TEXT,
                    created_at    TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_agent ON capability_events(agent_id);

                CREATE TABLE IF NOT EXISTS agent_capability_embeddings (
                    agent_id      TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    model_name    TEXT NOT NULL,
                    vector_blob   BLOB NOT NULL,
                    dim           INTEGER NOT NULL,
                    updated_at    TEXT NOT NULL,
                    PRIMARY KEY(agent_id, capability_id)
                );
                """
            )
            try:
                c.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS agent_capability_fts
                    USING fts5(
                        agent_id UNINDEXED,
                        agent_name,
                        capability_id UNINDEXED,
                        capability_name,
                        description
                    )
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def agent_exists(self, agent_id):
        return (
            self.query_one("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,))
            is not None
        )

    def register_agent(self, agent_id, name, summary=None, device_id=None):
        if self.agent_exists(agent_id):
            raise KeyError(agent_id)
        now = self.now()
        self.execute(
            "INSERT INTO agents(agent_id, name, summary, device_id, status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, 'registering', ?, ?)",
            (agent_id, name, summary, device_id, now, now),
        )
        return {"agent_id": agent_id, "name": name}

    def get_agent(self, agent_id):
        row = self.query_one("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
        return dict(row) if row else None

    def search_agents_by_name(self, query):
        q = str(query or "").strip()
        if not q:
            return []
        rows = self.query_all(
            "SELECT agent_id, name, summary, status FROM agents WHERE name LIKE ?",
            (f"%{q}%",),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Capability atoms
    # ------------------------------------------------------------------

    def get_capability(self, agent_id, capability_id):
        row = self.query_one(
            "SELECT * FROM agent_capabilities WHERE agent_id = ? AND capability_id = ?",
            (agent_id, capability_id),
        )
        return dict(row) if row else None

    def list_capabilities(self, agent_id, include_retired=False):
        sql = (
            "SELECT * FROM agent_capabilities WHERE agent_id = ?"
            + ("" if include_retired else " AND status = 'active'")
            + " ORDER BY id"
        )
        rows = self.query_all(sql, (agent_id,))
        return [dict(r) for r in rows]

    def upsert_capability(self, agent_id, capability_id, name, description):
        """Insert a new atom, or overwrite an existing one (version+1)."""
        now = self.now()
        existing = self.get_capability(agent_id, capability_id)
        if existing is None:
            self.execute(
                "INSERT INTO agent_capabilities(agent_id, capability_id, name, description, status, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'active', 1, ?, ?)",
                (agent_id, capability_id, name, description, now, now),
            )
        else:
            self.execute(
                "UPDATE agent_capabilities SET name = ?, description = ?, status = 'active', version = version + 1, updated_at = ? "
                "WHERE agent_id = ? AND capability_id = ?",
                (name, description, now, agent_id, capability_id),
            )

    def retire_capability(self, agent_id, capability_id):
        self.execute(
            "UPDATE agent_capabilities SET status = 'retired', updated_at = ? "
            "WHERE agent_id = ? AND capability_id = ?",
            (self.now(), agent_id, capability_id),
        )
        self.remove_fts(agent_id, capability_id)
        self.remove_embedding(agent_id, capability_id)

    def log_event(self, agent_id, capability_id, action, decision=None):
        self.execute(
            "INSERT INTO capability_events(agent_id, capability_id, action, decision_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                agent_id,
                capability_id,
                action,
                json.dumps(decision, ensure_ascii=False) if decision is not None else None,
                self.now(),
            ),
        )

    # ------------------------------------------------------------------
    # Search index maintenance (FTS5 + embeddings, keyed on capability atom)
    # ------------------------------------------------------------------

    def replace_fts(self, agent_id, agent_name, capability_id, capability_name, description):
        if not self.fts_enabled:
            return
        tokenized_name = self.tokenize_for_fts(capability_name)
        tokenized_desc = self.tokenize_for_fts(description)
        tokenized_agent = self.tokenize_for_fts(agent_name)
        with self.transaction() as c:
            c.execute(
                "DELETE FROM agent_capability_fts WHERE agent_id = ? AND capability_id = ?",
                (agent_id, capability_id),
            )
            c.execute(
                "INSERT INTO agent_capability_fts(agent_id, agent_name, capability_id, capability_name, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (agent_id, tokenized_agent, capability_id, tokenized_name, tokenized_desc),
            )

    def remove_fts(self, agent_id, capability_id):
        if not self.fts_enabled:
            return
        self.execute(
            "DELETE FROM agent_capability_fts WHERE agent_id = ? AND capability_id = ?",
            (agent_id, capability_id),
        )

    def replace_embedding(self, agent_id, capability_id, vector, model_name):
        payload = json.dumps(vector).encode("utf-8")
        self.execute(
            "DELETE FROM agent_capability_embeddings WHERE agent_id = ? AND capability_id = ?",
            (agent_id, capability_id),
        )
        self.execute(
            "INSERT INTO agent_capability_embeddings(agent_id, capability_id, model_name, vector_blob, dim, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, capability_id, model_name, payload, len(vector), self.now()),
        )

    def remove_embedding(self, agent_id, capability_id):
        self.execute(
            "DELETE FROM agent_capability_embeddings WHERE agent_id = ? AND capability_id = ?",
            (agent_id, capability_id),
        )

    def list_embeddings(self, model_name, agent_id=None):
        if agent_id is not None:
            rows = self.query_all(
                "SELECT agent_id, capability_id, vector_blob FROM agent_capability_embeddings "
                "WHERE model_name = ? AND agent_id = ?",
                (model_name, agent_id),
            )
        else:
            rows = self.query_all(
                "SELECT agent_id, capability_id, vector_blob FROM agent_capability_embeddings "
                "WHERE model_name = ?",
                (model_name,),
            )
        result = []
        for r in rows:
            try:
                vector = json.loads(bytes(r["vector_blob"]).decode("utf-8"))
            except Exception:
                continue
            result.append((r["agent_id"], r["capability_id"], vector))
        return result
