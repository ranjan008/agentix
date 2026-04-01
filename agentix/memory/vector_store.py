"""
Vector Memory — tiered semantic memory with embedding + similarity search.

Lite tier:   sqlite-vec (zero external dependencies, embedded in SQLite)
Standard:    pgvector (PostgreSQL extension)
Fallback:    Pure-Python cosine similarity over SQLite BLOB (no extra deps)

Usage:
    store = VectorStore.from_config(cfg)
    store.upsert(agent_id, scope, doc_id, text, metadata)
    results = store.search(agent_id, scope, query_text, top_k=5)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding client
# ---------------------------------------------------------------------------

class EmbeddingClient:
    """
    Generates text embeddings via the Anthropic Voyage API or a local fallback.

    Priority:
      1. voyage-3 (via voyageai package or Anthropic API)
      2. Simple TF-IDF hash embedding (fallback, no external deps)
    """

    def __init__(self, api_key: str = "", model: str = "voyage-3") -> None:
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client = None
        self._dim = 1024  # voyage-3 dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.api_key:
            try:
                return self._voyage_embed(texts)
            except Exception as e:
                logger.warning("Voyage embedding failed (%s), using fallback", e)
        return [self._hash_embed(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _voyage_embed(self, texts: list[str]) -> list[list[float]]:
        try:
            import voyageai
            client = voyageai.Client(api_key=self.api_key)
            result = client.embed(texts, model=self.model)
            return result.embeddings
        except ImportError:
            pass
        # Fallback: call via httpx directly
        import httpx
        resp = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"input": texts, "model": self.model},
            timeout=30,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def _hash_embed(self, text: str, dim: int = 128) -> list[float]:
        """
        Deterministic hash-based pseudo-embedding (fallback when no API key).
        NOT suitable for semantic similarity — only for structural testing.
        """
        import hashlib
        vec = []
        for i in range(dim):
            h = hashlib.md5(f"{i}:{text}".encode()).digest()
            val = struct.unpack("f", h[:4])[0]
            vec.append(val)
        norm = (sum(v * v for v in vec) ** 0.5) or 1.0
        return [v / norm for v in vec]

    @property
    def dim(self) -> int:
        return self._dim


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class VectorBackend(ABC):
    @abstractmethod
    def upsert(self, agent_id: str, scope: str, doc_id: str,
               text: str, embedding: list[float], metadata: dict) -> None: ...

    @abstractmethod
    def search(self, agent_id: str, scope: str,
               query_embedding: list[float], top_k: int) -> list[dict]: ...

    @abstractmethod
    def delete(self, agent_id: str, scope: str, doc_id: str) -> None: ...

    @abstractmethod
    def count(self, agent_id: str, scope: str) -> int: ...


# ---------------------------------------------------------------------------
# SQLite fallback backend (pure Python cosine — no extra deps)
# ---------------------------------------------------------------------------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vector_docs (
    id          TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    text        TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    PRIMARY KEY (agent_id, scope, id)
);
CREATE INDEX IF NOT EXISTS idx_vec_agent ON vector_docs(agent_id, scope);
"""


class SQLiteVectorBackend(VectorBackend):
    """
    Pure-Python fallback using SQLite BLOB storage + in-process cosine similarity.
    For Lite tier when sqlite-vec is not installed.
    Works for small corpora (<10k docs). Scales linearly — use pgvector for larger sets.
    """

    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript(_SQLITE_SCHEMA)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def upsert(self, agent_id: str, scope: str, doc_id: str,
               text: str, embedding: list[float], metadata: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO vector_docs (id, agent_id, scope, text, embedding, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id, scope, id) DO UPDATE SET
                     text=excluded.text, embedding=excluded.embedding,
                     metadata=excluded.metadata, created_at=excluded.created_at""",
                (doc_id, agent_id, scope, text, _pack(embedding), json.dumps(metadata), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def search(self, agent_id: str, scope: str,
               query_embedding: list[float], top_k: int = 5) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id, text, embedding, metadata FROM vector_docs WHERE agent_id=? AND scope=?",
                (agent_id, scope),
            ).fetchall()
        finally:
            conn.close()

        scored = []
        for row in rows:
            emb = _unpack(row["embedding"])
            score = _cosine(query_embedding, emb)
            scored.append({
                "doc_id": row["id"],
                "text": row["text"],
                "score": score,
                "metadata": json.loads(row["metadata"]),
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def delete(self, agent_id: str, scope: str, doc_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM vector_docs WHERE agent_id=? AND scope=? AND id=?",
                (agent_id, scope, doc_id),
            )
            conn.commit()
        finally:
            conn.close()

    def count(self, agent_id: str, scope: str) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM vector_docs WHERE agent_id=? AND scope=?",
                (agent_id, scope),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# pgvector backend (Standard tier)
# ---------------------------------------------------------------------------

class PgVectorBackend(VectorBackend):
    """
    PostgreSQL + pgvector backend for Standard/Enterprise tier.
    Requires: pip install psycopg2-binary pgvector
    """

    def __init__(self, dsn: str, dim: int = 1024) -> None:
        self.dsn = dsn
        self.dim = dim
        self._init_db()

    def _conn(self):
        import psycopg2
        return psycopg2.connect(self.dsn)

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS vector_docs (
                        id         TEXT NOT NULL,
                        agent_id   TEXT NOT NULL,
                        scope      TEXT NOT NULL,
                        text       TEXT NOT NULL,
                        embedding  vector({self.dim}),
                        metadata   JSONB NOT NULL DEFAULT '{{}}',
                        created_at DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY (agent_id, scope, id)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_vec_ivfflat "
                    f"ON vector_docs USING ivfflat (embedding vector_cosine_ops) "
                    f"WITH (lists = 100)"
                )
            conn.commit()
        finally:
            conn.close()

    def upsert(self, agent_id: str, scope: str, doc_id: str,
               text: str, embedding: list[float], metadata: dict) -> None:
        from pgvector.psycopg2 import register_vector
        conn = self._conn()
        register_vector(conn)
        try:
            import numpy as np
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO vector_docs (id, agent_id, scope, text, embedding, metadata, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (agent_id, scope, id) DO UPDATE SET
                         text=EXCLUDED.text, embedding=EXCLUDED.embedding,
                         metadata=EXCLUDED.metadata, created_at=EXCLUDED.created_at""",
                    (doc_id, agent_id, scope, text,
                     np.array(embedding), json.dumps(metadata), time.time()),
                )
            conn.commit()
        finally:
            conn.close()

    def search(self, agent_id: str, scope: str,
               query_embedding: list[float], top_k: int = 5) -> list[dict]:
        from pgvector.psycopg2 import register_vector
        import numpy as np
        conn = self._conn()
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, text, metadata,
                              1 - (embedding <=> %s::vector) AS score
                       FROM vector_docs
                       WHERE agent_id=%s AND scope=%s
                       ORDER BY embedding <=> %s::vector
                       LIMIT %s""",
                    (np.array(query_embedding), agent_id, scope,
                     np.array(query_embedding), top_k),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"doc_id": r[0], "text": r[1], "metadata": r[2], "score": float(r[3])} for r in rows]

    def delete(self, agent_id: str, scope: str, doc_id: str) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vector_docs WHERE agent_id=%s AND scope=%s AND id=%s",
                    (agent_id, scope, doc_id),
                )
            conn.commit()
        finally:
            conn.close()

    def count(self, agent_id: str, scope: str) -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM vector_docs WHERE agent_id=%s AND scope=%s",
                    (agent_id, scope),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class VectorStore:
    """
    High-level vector memory interface used by agent runtime.
    Auto-embeds text and stores/retrieves by semantic similarity.
    """

    def __init__(self, backend: VectorBackend, embedder: EmbeddingClient) -> None:
        self._backend = backend
        self._embedder = embedder

    def upsert(self, agent_id: str, scope: str, doc_id: str,
               text: str, metadata: dict | None = None) -> None:
        """Embed text and store in vector memory."""
        embedding = self._embedder.embed_one(text)
        self._backend.upsert(agent_id, scope, doc_id, text, embedding, metadata or {})

    def search(self, agent_id: str, scope: str, query: str, top_k: int = 5) -> list[dict]:
        """Embed query and return top-k semantically similar documents."""
        q_emb = self._embedder.embed_one(query)
        return self._backend.search(agent_id, scope, q_emb, top_k)

    def auto_store_turn(self, agent_id: str, scope: str,
                        trigger_id: str, text: str, role: str = "user") -> None:
        """Auto-embed and store a conversation turn for future retrieval."""
        import uuid
        doc_id = f"{trigger_id}_{role}_{uuid.uuid4().hex[:8]}"
        self.upsert(agent_id, scope, doc_id, text, {"trigger_id": trigger_id, "role": role})

    def delete(self, agent_id: str, scope: str, doc_id: str) -> None:
        self._backend.delete(agent_id, scope, doc_id)

    def count(self, agent_id: str, scope: str) -> int:
        return self._backend.count(agent_id, scope)

    @classmethod
    def from_config(cls, cfg: dict) -> "VectorStore":
        tier = cfg.get("infra_tier", "lite")
        embedder = EmbeddingClient(model=cfg.get("embedding_model", "voyage-3"))

        if tier in ("standard", "enterprise"):
            db_url = cfg.get("database_url", "")
            if db_url:
                try:
                    dim = cfg.get("embedding_dim", 1024)
                    return cls(PgVectorBackend(db_url, dim=dim), embedder)
                except Exception as e:
                    logger.warning("pgvector init failed (%s) — falling back to SQLite", e)

        db_path = cfg.get("db_path", "data/agentix.db")
        return cls(SQLiteVectorBackend(db_path), embedder)
