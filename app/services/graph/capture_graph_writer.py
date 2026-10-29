"""Capture graph writer — project capture link files into Neo4j (ADR-011 §1).

A linked capture becomes a ``Document {kind: capture}`` node and its link
file's edges are written onto it. The link file is the source of truth: each
write replaces every edge the projection owns on the document (marked
``capture_link: true``), so a re-link that drops a link drops the edge, and a
clean rebuild (``project_all``) reproduces the graph exactly.

Edge directions follow ADR-011 §4: ``MENTIONED_IN`` runs entity -> Document
(the existing mention edge), every other edge Document -> target.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.services.capture_link_store import (
    CAPTURE_TARGET,
    EDGE_TYPES,
    MENTIONED_IN,
    CaptureLinkFile,
    CaptureLinkStore,
)
from app.utils.event_time import TIME_SOURCE_CONTENT, TIME_SOURCE_FALLBACK, to_utc

logger = logging.getLogger(__name__)

CAPTURE_KIND = "capture"
_TITLE_CHARS = 140
_CHUNK = 500


def capture_doc_id(capture_path: str) -> str:
    """Document id for a capture file, by the corpus convention ``doc:<path>``."""
    return f"doc:{capture_path}"


def capture_title(memory: Any) -> str:
    """First heading or line of the content, else the summary."""
    for line in (getattr(memory, "content", "") or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:_TITLE_CHARS]
    return (getattr(memory, "summary", None) or getattr(memory, "id", ""))[:_TITLE_CHARS]


def capture_url(memory: Any) -> str | None:
    source_id = getattr(memory, "source_id", None)
    return source_id if isinstance(source_id, str) and "://" in source_id else None


def document_props(memory: Any, path: str) -> dict[str, Any]:
    """Node properties for one capture. Event time is the content's date
    (``source_date``); ingest time is ``recorded_at`` only (ADR-004)."""
    occurred = to_utc(getattr(memory, "source_date", None))
    props: dict[str, Any] = {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "type": "document",
        "kind": CAPTURE_KIND,
        "capture_id": memory.id,
        "lane": getattr(memory, "lane", None) or "record",
        "title": capture_title(memory),
        "source": getattr(memory, "source", None),
        "url": capture_url(memory),
        "recorded_at": to_utc(getattr(memory, "created_at", None)),
        "time_source": TIME_SOURCE_CONTENT if occurred else TIME_SOURCE_FALLBACK,
    }
    if occurred is not None:
        props["occurred_at"] = occurred
    if getattr(memory, "summary", None):
        props["summary"] = memory.summary[:1000]
    return {k: v for k, v in props.items() if v is not None}


def _edge_props(link: Any, lane: str) -> dict[str, Any]:
    props = {
        "capture_link": True,
        "lane": lane,
        "created_by": link.created_by,
        "confidence": float(link.confidence),
        "linked_at": link.linked_at,
        "run_id": link.run_id,
        "stance": link.stance,
    }
    return {k: v for k, v in props.items() if v is not None}


class CaptureGraphWriter:
    def __init__(self, neo4j_client: Any, capture_path_for: Any):
        """``capture_path_for(capture_id)`` -> repo-relative path of the capture file."""
        self.neo4j = neo4j_client
        self.capture_path_for = capture_path_for

    async def write(self, items: list[tuple[Any, CaptureLinkFile]]) -> dict[str, int]:
        """Project (capture, link file) pairs. Shadow-only files project
        nothing; a file that projects nothing removes the edges it owned."""
        docs: list[dict[str, Any]] = []
        doc_ids: list[str] = []
        edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for memory, record in items:
            path = self.capture_path_for(memory.id)
            doc_id = capture_doc_id(path)
            doc_ids.append(doc_id)
            if not record.projected():
                continue
            docs.append({"id": doc_id, "props": document_props(memory, path)})
            lane = getattr(memory, "lane", None) or "record"
            for link in record.graph_links():
                if link.edge not in EDGE_TYPES:
                    continue
                target = (
                    capture_doc_id(self.capture_path_for(link.target_id))
                    if link.target_type == CAPTURE_TARGET
                    else link.target_id
                )
                edges[link.edge].append(
                    {
                        "doc": doc_id,
                        "target": target,
                        "to_capture": link.target_type == CAPTURE_TARGET,
                        "props": _edge_props(link, lane),
                    }
                )

        # A document owns its outgoing edges and its incoming MENTIONED_IN;
        # an ABOUT from another capture into it belongs to that capture.
        for chunk in _chunks(doc_ids):
            await self.neo4j.execute_write(
                "UNWIND $ids AS id MATCH (d:Document {id: id})-[r]->() "
                "WHERE r.capture_link = true DELETE r",
                {"ids": chunk},
            )
            await self.neo4j.execute_write(
                "UNWIND $ids AS id MATCH (:Entity)-[r:MENTIONED_IN]->(d:Document {id: id}) "
                "WHERE r.capture_link = true DELETE r",
                {"ids": chunk},
            )
        for chunk in _chunks(docs):
            await self.neo4j.execute_write(
                "UNWIND $rows AS row MERGE (d:Document {id: row.id}) SET d += row.props",
                {"rows": chunk},
            )
        written = 0
        for edge, rows in edges.items():
            entity_rows = [r for r in rows if not r["to_capture"]]
            capture_rows = [r for r in rows if r["to_capture"]]
            if edge == MENTIONED_IN:
                query = (
                    "UNWIND $rows AS row MATCH (d:Document {id: row.doc}) "
                    "MATCH (e:Entity {id: row.target}) "
                    "MERGE (e)-[r:MENTIONED_IN]->(d) SET r += row.props"
                )
            else:
                query = (
                    "UNWIND $rows AS row MATCH (d:Document {id: row.doc}) "
                    "MATCH (t:Entity {id: row.target}) "
                    f"MERGE (d)-[r:{edge}]->(t) SET r += row.props"
                )
            for chunk in _chunks(entity_rows):
                await self.neo4j.execute_write(query, {"rows": chunk})
            for chunk in _chunks(capture_rows):
                await self.neo4j.execute_write(
                    "UNWIND $rows AS row MATCH (d:Document {id: row.doc}) "
                    "MATCH (t:Document {id: row.target}) "
                    f"MERGE (d)-[r:{edge}]->(t) SET r += row.props",
                    {"rows": chunk},
                )
            written += len(rows)
        return {"documents": len(docs), "edges": written}

    async def project_all(self, link_store: CaptureLinkStore, capture_store: Any) -> dict[str, int]:
        """Re-project every link file (clean rebuild). Owner-linked capture
        targets are projected first so edges onto them find their node."""
        records = [r for r in link_store.iter_all() if r.projected()]
        if not records:
            return {"documents": 0, "edges": 0}
        items = []
        missing = 0
        for record in records:
            memory = capture_store.get(record.capture_id)
            if memory is None:
                missing += 1
                continue
            items.append((memory, record))
        targets = {
            link.target_id
            for _, record in items
            for link in record.graph_links()
            if link.target_type == CAPTURE_TARGET
        }
        have = {memory.id for memory, _ in items}
        for target_id in targets - have:
            memory = capture_store.get(target_id)
            if memory is not None:
                items.append((memory, CaptureLinkFile(capture_id=target_id, mode="owner")))
        bare = [(m, r) for m, r in items if not r.links]
        stats = {"documents": 0, "edges": 0}
        for group in (bare, [(m, r) for m, r in items if r.links]):
            for chunk in _chunks(group):
                result = await self.write(chunk)
                stats["documents"] += result["documents"]
                stats["edges"] += result["edges"]
        if missing:
            logger.warning("[GRAPH] %d capture link file(s) name a missing capture", missing)
        return stats


def _chunks(rows: list, size: int = _CHUNK):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
