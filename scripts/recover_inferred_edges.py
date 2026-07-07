#!/usr/bin/env python3
"""One-off: restore inference-derived semantic edges from a rebuild log.

The 2026-06-12 source rebuild's inferred relationship edges were erased by a
subsequent signals-tier rebuild (build_graph(force_rebuild=True) rebuilt the
graph from entity files, where inferred edges were not yet persisted — fixed
since via create_semantic_relationship file write-through). This script
re-creates those edges in Neo4j from the rebuild log's
"Created semantic relationship" lines and batch-persists them to entity
frontmatter so future rebuilds round-trip them.

Usage (inside container):
    python scripts/recover_inferred_edges.py [/app/data/rebuild_20260612.log]
"""

from __future__ import annotations

import asyncio
import collections
import os
import re
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LOG = sys.argv[1] if len(sys.argv) > 1 else "/app/data/rebuild_20260612.log"
PAT = re.compile(
    r"Created semantic relationship: (\S+) --\[(\w+)\]--> (\S+) \(strength: ([\d.]+)\)"
)


def collect_edges() -> list[tuple[str, str, str, float]]:
    edges, seen = [], set()
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = PAT.search(line)
            if not m:
                continue
            key = (m.group(1), m.group(2), m.group(3))
            if key in seen:
                continue
            seen.add(key)
            edges.append((m.group(1), m.group(2), m.group(3), float(m.group(4))))
    return edges


async def main() -> None:
    edges = collect_edges()
    print(f"recovered {len(edges)} unique inferred edges from log", flush=True)

    # Standalone process: the Neo4j client is lazy and starts uninitialized —
    # initialize it first so the factory returns the Neo4j-backed graph
    # instead of the in-memory fallback (same pattern as rebuild_kb.py).
    from app.neo4j_client import get_neo4j_client

    client = get_neo4j_client()
    if not client.is_initialized:
        await client.initialize()

    from app.services.graph.neo4j_schema import relationship_type_to_neo4j
    from app.services.knowledge_graph import get_knowledge_graph

    kg = get_knowledge_graph()
    if not hasattr(kg, "_upsert_relationship"):
        raise RuntimeError(
            "Got the in-memory fallback graph — Neo4j initialization failed"
        )
    await kg.build_graph()

    wrote = skipped = 0
    for src, rtype, dst, strength in edges:
        if src not in kg.nodes or dst not in kg.nodes:
            skipped += 1
            continue
        await kg._upsert_relationship(
            source_id=src,
            target_id=dst,
            rel_type=relationship_type_to_neo4j(rtype),
            properties={
                "strength": strength,
                "semantic": True,
                "source": "ingest",
                "evidence": "recovered from rebuild 2026-06-12 inference log",
                "reasoning": "inference-derived edge restored after signals-tier rebuild",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        wrote += 1
    print(f"neo4j edges written: {wrote}, skipped (missing endpoint): {skipped}", flush=True)

    # Batched frontmatter write-through: one file write per entity (single
    # git commit done by the caller afterwards).
    import yaml

    per_entity: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for src, rtype, dst, _ in edges:
        if src in kg.nodes and dst in kg.nodes:
            per_entity[src][rtype].append(dst)

    touched = 0
    for eid, rels in per_entity.items():
        path = kg._find_entity_file(eid)
        if not path:
            continue
        text = open(path, encoding="utf-8").read()
        m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        changed = False
        for rtype, targets in rels.items():
            existing = meta.get(rtype) or []
            if isinstance(existing, str):
                existing = [existing]
            for t in targets:
                if t not in existing:
                    existing.append(t)
                    changed = True
            meta[rtype] = existing
        if changed:
            open(path, "w", encoding="utf-8").write(
                "---\n"
                + yaml.dump(
                    meta, default_flow_style=False, allow_unicode=True, sort_keys=False
                )
                + "---\n"
                + text[m.end() :]
            )
            touched += 1
    print(f"frontmatter updated on {touched} entity files", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
