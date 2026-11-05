"""Meeting graph writer: project meeting link files into Neo4j (ADR-013 §5).

Each excerpt in a file becomes one edge
``(d:Document {id: document_id})-[r:ADVANCES {excerpt_id}]->(s:Entity)``,
marked ``meeting_link: true``. A write first deletes every ``meeting_link``
edge on the Document, so the file stays the source of truth and a clean
rebuild (``project_all``) reproduces the edges exactly.

The writer never creates, merges or sets properties on a Document: meeting
ingest owns those. A file whose Document is missing is skipped and reported.
It never touches ``capture_link`` edges, and the capture writer never touches
these.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.meeting_link_store import Excerpt, MeetingLinkFile, MeetingLinkStore
from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

LINK_PHASE = "link_phase"
_CHUNK = 500


def excerpt_props(excerpt: Excerpt) -> dict[str, Any]:
    """Edge properties for one excerpt. Neo4j lists cannot hold null, so
    ``with_ids`` keeps an empty string where a name did not resolve; it stays
    aligned with ``with_names``."""
    props: dict[str, Any] = {
        "excerpt_id": excerpt.id,
        "meeting_link": True,
        "lane": "record",
        "created_by": LINK_PHASE,
        "stance": excerpt.stance,
        "confidence": float(excerpt.confidence),
        "claim": excerpt.claim,
        "quote": excerpt.quote,
        "lines": list(excerpt.lines),
        "speaker_id": excerpt.speaker.id,
        "speaker_name": excerpt.speaker.name,
        "speaker_basis": excerpt.speaker.basis,
        "speaker_is_owner": excerpt.speaker_is_owner,  # absent when None
        "with_ids": [p.id or "" for p in excerpt.with_],
        "with_names": [p.name for p in excerpt.with_],
        "with_basis": excerpt.with_basis,
        "run_id": excerpt.run_id,
        "linked_at": to_utc(excerpt.linked_at),  # DATETIME (ADR-004)
    }
    return {k: v for k, v in props.items() if v is not None}


class MeetingGraphWriter:
    def __init__(self, neo4j_client: Any):
        self.neo4j = neo4j_client

    async def write(self, records: list[MeetingLinkFile]) -> dict[str, Any]:
        """Project these files. A file that projects nothing (shadow,
        orphaned) only loses the edges it owned."""
        doc_ids = sorted({r.document_id for r in records})
        present: set[str] = set()
        for chunk in _chunks(doc_ids):
            rows = await self.neo4j.execute_read(
                "UNWIND $ids AS id MATCH (d:Document {id: id}) RETURN d.id AS id", {"ids": chunk}
            )
            present.update(row["id"] for row in rows or [])
        for chunk in _chunks(doc_ids):
            await self.neo4j.execute_write(
                "UNWIND $ids AS id MATCH (d:Document {id: id})-[r:ADVANCES]->() "
                "WHERE r.meeting_link = true DELETE r",
                {"ids": chunk},
            )
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        documents = 0
        for record in records:
            if not record.projected():
                continue
            if record.document_id not in present:
                missing.append(record.document_id)
                continue
            documents += 1
            for excerpt in record.excerpts:
                rows.append({"doc": record.document_id, "storyline_id": excerpt.storyline_id,
                             "id": excerpt.id, "props": excerpt_props(excerpt)})
        for chunk in _chunks(rows):
            await self.neo4j.execute_write(
                "UNWIND $rows AS row MATCH (d:Document {id: row.doc}) "
                "MATCH (s:Entity {id: row.storyline_id}) "
                "MERGE (d)-[r:ADVANCES {excerpt_id: row.id}]->(s) SET r += row.props",
                {"rows": chunk},
            )
        if missing:
            logger.warning("[GRAPH] %d meeting link file(s) name a missing Document", len(missing))
        return {"documents": documents, "edges": len(rows), "missing_document": missing}

    async def project_all(self, store: MeetingLinkStore) -> dict[str, Any]:
        """Clean rebuild: every file with ``mode: on`` and no ``orphaned_at``."""
        records = [r for r in store.iter_all() if r.projected()]
        if not records:
            return {"documents": 0, "edges": 0, "missing_document": []}
        return await self.write(records)


def _chunks(rows: list, size: int = _CHUNK):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
