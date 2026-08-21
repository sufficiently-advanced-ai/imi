"""Batched Neo4j writes for full graph builds.

The legacy ``Neo4jKnowledgeGraph`` build issued one ``execute_write`` per node,
per stub, per relationship and per document→entity link — on a 12.5k-file
corpus that is ~130k sequential bolt round trips (measured: 129 s, ~1k/s).
During a full build those writes go through this collector instead and are
flushed as a handful of ``UNWIND $rows`` statements in chunks.

Semantics are preserved:

* nodes use ``MERGE ... SET n += props`` (last write for an id wins, props are
  merged in collection order, exactly like the sequential path);
* stubs use ``MERGE ... ON CREATE SET`` and are flushed *after* real nodes, so
  an id that is both referenced and defined ends up as the real node —
  the same final state the sequential path converges to;
* relationships ``MATCH`` both endpoints then ``MERGE``, after all nodes and
  stubs exist.

Everything is plain data until ``flush()``; the collector never touches the
event loop, so it is equally usable from a worker thread.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500


def _chunks(rows: list[Any], size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


class Neo4jBatchWriter:
    """Collects graph writes and flushes them with UNWIND in chunks."""

    def __init__(self, neo4j_client: Any, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.neo4j = neo4j_client
        self.chunk_size = max(1, chunk_size)
        self.nodes: dict[str, dict[str, dict[str, Any]]] = {}  # label -> id -> props
        self.stubs: dict[str, dict[str, dict[str, Any]]] = {}  # label -> id -> row
        self.documents: dict[str, dict[str, Any]] = {}  # id -> props
        self.relationships: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}  # type -> (s,t) -> props
        self.mentions: dict[tuple[str, str], dict[str, Any]] = {}  # (eid, did) -> row
        self.type_usage: set[tuple[str, str]] = set()  # (kind, name)
        self.statements_executed = 0

    # ── collection ──────────────────────────────────────────────────────

    def add_node(self, entity_id: str, label: str, props: dict[str, Any]) -> None:
        bucket = self.nodes.setdefault(label, {})
        existing = bucket.get(entity_id)
        if existing is None:
            bucket[entity_id] = dict(props)
        else:
            existing.update(props)

    def add_stub(self, entity_id: str, label: str, row: dict[str, Any]) -> None:
        self.stubs.setdefault(label, {}).setdefault(entity_id, dict(row))

    def add_document(self, doc_id: str, props: dict[str, Any]) -> None:
        existing = self.documents.get(doc_id)
        if existing is None:
            self.documents[doc_id] = dict(props)
        else:
            existing.update(props)

    def add_relationship(
        self, source_id: str, target_id: str, rel_type: str, props: dict[str, Any]
    ) -> None:
        bucket = self.relationships.setdefault(rel_type, {})
        key = (source_id, target_id)
        existing = bucket.get(key)
        if existing is None:
            bucket[key] = dict(props)
        else:
            existing.update(props)

    def add_mention(self, entity_id: str, doc_id: str, path: str, name: str) -> None:
        self.mentions.setdefault(
            (entity_id, doc_id), {"eid": entity_id, "did": doc_id, "path": path, "name": name}
        )

    def record_type_usage(self, kind: str, name: str) -> None:
        self.type_usage.add((kind, name))

    # ── flush ───────────────────────────────────────────────────────────

    def pending(self) -> dict[str, int]:
        return {
            "nodes": sum(len(b) for b in self.nodes.values()),
            "stubs": sum(len(b) for b in self.stubs.values()),
            "documents": len(self.documents),
            "relationships": sum(len(b) for b in self.relationships.values()),
            "mentions": len(self.mentions),
            "type_usage": len(self.type_usage),
        }

    async def _run(self, query: str, rows: list[dict[str, Any]]) -> None:
        for chunk in _chunks(rows, self.chunk_size):
            await self.neo4j.execute_write(query, {"rows": chunk})
            self.statements_executed += 1

    async def flush(
        self,
        record_type_usage: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> dict[str, int]:
        """Write everything collected so far, in dependency order, and reset."""
        stats = self.pending()
        self.statements_executed = 0

        # 1. real entity nodes
        for label, bucket in self.nodes.items():
            rows = [{"id": eid, "props": props} for eid, props in bucket.items()]
            await self._run(
                f"UNWIND $rows AS row "
                f"MERGE (n:Entity:{label} {{id: row.id}}) "
                f"SET n += row.props",
                rows,
            )

        # 2. stubs (ON CREATE only — never overwrite a real node)
        for label, bucket in self.stubs.items():
            rows = list(bucket.values())
            await self._run(
                f"UNWIND $rows AS row "
                f"MERGE (n:Entity:{label} {{id: row.id}}) "
                f"ON CREATE SET n.name = row.name, n.canonical_name = row.canonical, "
                f"n.entity_type = row.etype, n.stub = true, n.updated_at = row.ts",
                rows,
            )

        # 3. document nodes
        if self.documents:
            rows = [{"id": did, "props": props} for did, props in self.documents.items()]
            await self._run(
                "UNWIND $rows AS row MERGE (d:Document {id: row.id}) SET d += row.props",
                rows,
            )

        # 4. relationships (endpoints now exist)
        for rel_type, bucket in self.relationships.items():
            rows = [
                {"source": s, "target": t, "props": props}
                for (s, t), props in bucket.items()
            ]
            await self._run(
                f"UNWIND $rows AS row "
                f"MATCH (a:Entity {{id: row.source}}) "
                f"MATCH (b:Entity {{id: row.target}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) "
                f"SET r += row.props",
                rows,
            )

        # 5. entity → document mentions
        if self.mentions:
            await self._run(
                "UNWIND $rows AS row "
                "MATCH (e:Entity {id: row.eid}) "
                "MERGE (d:Document {id: row.did}) "
                "ON CREATE SET d.path = row.path, d.name = row.name "
                "MERGE (e)-[:MENTIONED_IN]->(d)",
                list(self.mentions.values()),
            )

        # 6. type registry — once per distinct type instead of once per write
        if record_type_usage is not None:
            for kind, name in sorted(self.type_usage):
                await record_type_usage(kind, name)

        stats["statements"] = self.statements_executed
        logger.info("[BATCH_WRITER] flushed %s", stats)
        self._reset()
        return stats

    def _reset(self) -> None:
        self.nodes.clear()
        self.stubs.clear()
        self.documents.clear()
        self.relationships.clear()
        self.mentions.clear()
        self.type_usage.clear()
