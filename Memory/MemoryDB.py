import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from configurationLoader import config


class MemoryDB:
    """SQLite-backed memory store for L0-L3 pyramid memory."""

    # ------------------------------------------------------------------
    # jieba lazy-init (module-level, shared across instances)
    # ------------------------------------------------------------------
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

    def tokenize_for_fts(self, text):
        """Tokenize text with jieba for FTS5 indexing.
        Falls back to the original text when jieba is unavailable."""
        jieba_mod = self._get_jieba()
        if jieba_mod:
            try:
                tokens = jieba_mod.cut_for_search(str(text or ""))
                return " ".join(tokens)
            except Exception:
                pass
        return str(text or "")

    def __init__(self, db_path=None):
        root_dir = Path(config.get("memory.root_dir", "./runtime_memory"))
        default_path = root_dir / "memory.db"
        self.path = Path(db_path or config.get("memory.sqlite.path", str(default_path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.fts_enabled = False
        self.bm25_enabled = False
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

    def executemany(self, sql, rows):
        connection = self.connect()
        cursor = connection.executemany(sql, rows)
        connection.commit()
        return cursor

    def query_all(self, sql, params=()):
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.connect().execute(sql, params).fetchone()

    def scalar(self, sql, params=(), default=None):
        row = self.query_one(sql, params)
        if row is None:
            return default
        if isinstance(row, sqlite3.Row):
            values = tuple(row)
            return values[0] if values else default
        return row[0] if row else default

    def bootstrap(self):
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS l0_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    day_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    pipeline_state TEXT NOT NULL DEFAULT 'pending'
                );

                CREATE INDEX IF NOT EXISTS idx_l0_turns_day_key ON l0_turns(day_key);
                CREATE INDEX IF NOT EXISTS idx_l0_turns_session_day ON l0_turns(session_id, day_key);

                CREATE TABLE IF NOT EXISTS l0_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    content_json TEXT,
                    tool_name TEXT,
                    message_type TEXT,
                    timestamp TEXT NOT NULL,
                    token_estimate INTEGER,
                    metadata_json TEXT,
                    FOREIGN KEY(turn_id) REFERENCES l0_turns(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_l0_messages_turn_id ON l0_messages(turn_id);
                CREATE INDEX IF NOT EXISTS idx_l0_messages_timestamp ON l0_messages(timestamp);

                CREATE TABLE IF NOT EXISTS l1_memory_atoms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    atom_type TEXT NOT NULL,
                    subject TEXT,
                    slot TEXT,
                    canonical_text TEXT NOT NULL,
                    source_turn_id INTEGER,
                    source_message_id INTEGER,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    salience REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    scene_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_l1_atom_type ON l1_memory_atoms(atom_type);
                CREATE INDEX IF NOT EXISTS idx_l1_scene_id ON l1_memory_atoms(scene_id);
                CREATE INDEX IF NOT EXISTS idx_l1_updated_at ON l1_memory_atoms(updated_at);

                CREATE TABLE IF NOT EXISTS l2_scenes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE,
                    scene_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    keywords_json TEXT,
                    aliases_json TEXT,
                    importance REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_mentioned_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_l2_scene_slug ON l2_scenes(slug);
                CREATE INDEX IF NOT EXISTS idx_l2_scene_updated_at ON l2_scenes(updated_at);

                CREATE TABLE IF NOT EXISTS l2_scene_members (
                    scene_id INTEGER NOT NULL,
                    atom_id INTEGER NOT NULL,
                    source_turn_id INTEGER,
                    weight REAL NOT NULL DEFAULT 1.0,
                    reason TEXT,
                    PRIMARY KEY(scene_id, atom_id),
                    FOREIGN KEY(scene_id) REFERENCES l2_scenes(id) ON DELETE CASCADE,
                    FOREIGN KEY(atom_id) REFERENCES l1_memory_atoms(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS l3_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_type TEXT NOT NULL,
                    section TEXT NOT NULL,
                    field TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source_atom_id INTEGER,
                    source_scene_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_l3_profile_identity
                ON l3_profiles(profile_type, section, field, value, status);

                CREATE INDEX IF NOT EXISTS idx_l3_section ON l3_profiles(section);

                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    dim INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_source_model
                ON embeddings(source_table, source_id, model_name);

                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    target_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bm25_sparse (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    term_freq_json TEXT NOT NULL,
                    doc_length INTEGER NOT NULL DEFAULT 0
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_bm25_sparse_source
                ON bm25_sparse(source_table, source_id);
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts_memory
                    USING fts5(record_type, source_id UNINDEXED, content)
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False

    def replace_fts_entry(self, record_type, source_id, content):
        if not self.fts_enabled:
            return
        tokenized = self.tokenize_for_fts(content)
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM fts_memory WHERE record_type = ? AND source_id = ?",
                (str(record_type), str(source_id)),
            )
            connection.execute(
                "INSERT INTO fts_memory(record_type, source_id, content) VALUES (?, ?, ?)",
                (str(record_type), str(source_id), tokenized),
            )

    def remove_fts_entry(self, record_type, source_id):
        if not self.fts_enabled:
            return
        self.execute(
            "DELETE FROM fts_memory WHERE record_type = ? AND source_id = ?",
            (str(record_type), str(source_id)),
        )

    # ------------------------------------------------------------------
    # BM25 sparse vector storage
    # ------------------------------------------------------------------

    def replace_bm25_entry(self, source_table, source_id, term_freq_dict, doc_length=0):
        """Upsert a BM25 sparse vector for a document.

        Parameters
        ----------
        source_table : str
            "l1" or "l2"
        source_id : int
            Primary key in the source table.
        term_freq_dict : dict[str, float]
            TF sparse vector from BM25Encoder.encode_document().
        doc_length : int
            Number of tokens in the original document.
        """
        tf_json = self.dumps_json(term_freq_dict)
        self.execute(
            "DELETE FROM bm25_sparse WHERE source_table = ? AND source_id = ?",
            (str(source_table), int(source_id)),
        )
        self.execute(
            "INSERT INTO bm25_sparse(source_table, source_id, term_freq_json, doc_length) "
            "VALUES (?, ?, ?, ?)",
            (str(source_table), int(source_id), tf_json, int(doc_length or 0)),
        )

    def remove_bm25_entry(self, source_table, source_id):
        """Remove a BM25 sparse vector from the index."""
        self.execute(
            "DELETE FROM bm25_sparse WHERE source_table = ? AND source_id = ?",
            (str(source_table), int(source_id)),
        )

    def get_all_bm25_entries(self, source_table=None):
        """Return all BM25 sparse entries, optionally filtered by source_table.

        Returns
        -------
        list[dict]
            Each dict has keys: source_table, source_id, term_freq_json, doc_length.
        """
        if source_table is not None:
            rows = self.query_all(
                "SELECT source_table, source_id, term_freq_json, doc_length "
                "FROM bm25_sparse WHERE source_table = ?",
                (str(source_table),),
            )
        else:
            rows = self.query_all(
                "SELECT source_table, source_id, term_freq_json, doc_length "
                "FROM bm25_sparse"
            )
        return [dict(row) for row in rows]

    @staticmethod
    def dumps_json(value):
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def loads_json(value, default=None):
        if value in (None, ""):
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return default
