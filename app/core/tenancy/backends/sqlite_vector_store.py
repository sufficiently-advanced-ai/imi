"""SqliteVectorStore — persistent community vector backend (no FAISS, no server).

Why this exists: the semantica 0.3.x FAISS facade drops metadata at
``store_vectors`` time and ignores ``MetadataFilter`` in search, so governed
recall (memory_recall / search_signals_semantic) can never re-hydrate
``content_type`` and silently returns zero results on the community stack.
It is also in-memory only, so memory-record vectors vanish on every restart.

This store mirrors the hosted PgVectorStore contract used behind
``signal_indexing.resolve_vector_store``:

    store_vectors(embeddings, metadata=..., ids=...) -> list[str]  # upsert by id
    search_vectors(query_embedding, k=..., filter=...) -> [{id, score, metadata}]
    delete(vector_id)

Design constraints:
  * Exact brute-force cosine, not ANN. Governed memory records number in the
    thousands, not millions — numpy over a few MB of float32 is ~ms, and
    exactness removes a whole class of "why wasn't it recalled" questions.
  * Embeddings are derived, regenerable data (the same posture as the FAISS
    index), so the table is self-managed in a sidecar file rather than the
    alembic-managed ops database.
  * ``filter`` honours content_type eq/in conditions (the only store-side
    filter recall relies on); anything else falls through untouched because
    governance is re-hydrated Python-side from the authoritative record.
  * tenant_id=None (community single-tenant) is normalised to "" internally:
    SQLite UNIQUE treats NULLs as distinct, which would break upsert-by-id.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Generator
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    content_type TEXT,
    metadata TEXT,
    embedding BLOB NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_vectors_tenant_kind
    ON vectors (tenant_id, content_type);
"""


def _normalise_tenant(tenant_id: str | None) -> str:
    return tenant_id if tenant_id is not None else ""


def _content_type_values(filter: Any) -> list[str] | None:
    """Extract content_type constraints from a (duck-typed) MetadataFilter.

    Returns None when the filter places no constraint on content_type —
    unknown fields/operators must fall through, never over-restrict.
    """
    conditions = getattr(filter, "conditions", None)
    if not conditions:
        return None
    values: list[str] = []
    for cond in conditions:
        if cond.get("field") != "content_type":
            continue
        operator, value = cond.get("operator"), cond.get("value")
        if operator == "eq" and isinstance(value, str):
            values.append(value)
        elif operator in ("in", "in_list") and isinstance(value, (list, tuple)):
            values.extend(v for v in value if isinstance(v, str))
    return values or None


class SqliteVectorStore:
    """Tenant-scoped vector store over a single SQLite file (WAL mode)."""

    def __init__(self, db_path: str, tenant_id: str | None = None) -> None:
        self._db_path = db_path
        self._tenant_id = _normalise_tenant(tenant_id)
        # Embedding dimension already present for this tenant (lazily read).
        # ``dim`` is a per-row column, so without a guard a generator that
        # resolved to a different model would silently create a mixed-dim
        # table that search (which filters on dim) could never fully see.
        self._known_dim: int | None = None
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _existing_dim(self, conn: sqlite3.Connection) -> int | None:
        if self._known_dim is None:
            row = conn.execute(
                "SELECT dim FROM vectors WHERE tenant_id = ? LIMIT 1", (self._tenant_id,)
            ).fetchone()
            self._known_dim = int(row[0]) if row else None
        return self._known_dim

    @contextlib.contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Per-call connection: commit on success, always close.

        ``with sqlite3.connect(...)`` alone commits but never closes — in a
        long-lived process that leaks a file handle per call.
        """
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # store — upsert by id
    # ------------------------------------------------------------------

    def store_vectors(
        self,
        embeddings: list[Any],
        metadata: list[dict] | None = None,
        ids: list[str] | None = None,
        **_: Any,
    ) -> list[str]:
        metadata = metadata or [{}] * len(embeddings)
        out_ids: list[str] = []
        rows = []
        for i, embedding in enumerate(embeddings):
            meta = metadata[i] if i < len(metadata) else {}
            vec_id = (
                (ids[i] if ids and i < len(ids) else None)
                or meta.get("id")
                or str(uuid.uuid4())
            )
            vec = np.asarray(embedding, dtype=np.float32)
            if vec.ndim > 1:
                vec = vec.reshape(-1)
            rows.append(
                (
                    vec_id,
                    self._tenant_id,
                    meta.get("content_type"),
                    json.dumps(meta),
                    vec.tobytes(),
                    int(vec.shape[0]),
                )
            )
            out_ids.append(vec_id)
        incoming_dims = {row[5] for row in rows}
        if len(incoming_dims) > 1:
            raise ValueError(f"store_vectors: mixed embedding dimensions in one call: {sorted(incoming_dims)}")
        with self._connect() as conn:
            existing = self._existing_dim(conn)
            if rows and existing is not None and rows[0][5] != existing:
                raise ValueError(
                    f"store_vectors: embedding dimension {rows[0][5]} does not match the "
                    f"{existing}-dim vectors already stored for tenant {self._tenant_id!r} — "
                    "the embedding model changed; rebuild the vector store instead of mixing"
                )
            if rows and existing is None:
                self._known_dim = rows[0][5]
            conn.executemany(
                "INSERT INTO vectors (id, tenant_id, content_type, metadata, embedding, dim) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, id) DO UPDATE SET "
                "  content_type = excluded.content_type, "
                "  metadata = excluded.metadata, "
                "  embedding = excluded.embedding, "
                "  dim = excluded.dim",
                rows,
            )
        return out_ids

    # ------------------------------------------------------------------
    # search — exact cosine, higher score = more similar
    # ------------------------------------------------------------------

    def search_vectors(
        self,
        query_embedding: Any,
        k: int = 10,
        filter: Any | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        query = np.asarray(query_embedding, dtype=np.float32)
        if query.ndim > 1:
            query = query.reshape(-1)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            return []

        sql = (
            "SELECT id, metadata, embedding FROM vectors "
            "WHERE tenant_id = ? AND dim = ?"
        )
        params: list[Any] = [self._tenant_id, int(query.shape[0])]
        kinds = _content_type_values(filter)
        if kinds:
            sql += f" AND content_type IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            return []

        matrix = np.frombuffer(b"".join(row[2] for row in rows), dtype=np.float32)
        matrix = matrix.reshape(len(rows), query.shape[0])
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0.0] = np.inf  # zero vectors score 0, never NaN
        scores = (matrix @ query) / (norms * query_norm)

        top = np.argsort(scores)[::-1][: max(k, 0)]
        results = []
        for idx in top:
            row = rows[int(idx)]
            try:
                meta = json.loads(row[1]) if row[1] else {}
            except (TypeError, json.JSONDecodeError):
                meta = {}
            results.append(
                {"id": row[0], "score": float(scores[int(idx)]), "metadata": meta}
            )
        return results

    # ------------------------------------------------------------------
    # delete — remove a single vector by id
    # ------------------------------------------------------------------

    def delete(self, vector_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM vectors WHERE id = ? AND tenant_id = ?",
                (vector_id, self._tenant_id),
            )

    # ------------------------------------------------------------------
    # count — cheap "is this index populated?" probe
    # ------------------------------------------------------------------

    def count(self, content_type: str | None = None) -> int:
        """Number of vectors for this tenant, optionally for one content_type."""
        sql = "SELECT COUNT(*) FROM vectors WHERE tenant_id = ?"
        params: list[Any] = [self._tenant_id]
        if content_type is not None:
            sql += " AND content_type = ?"
            params.append(content_type)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
