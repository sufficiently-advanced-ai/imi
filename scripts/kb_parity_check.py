#!/usr/bin/env python3
"""Does the live graph equal what a rebuild from the corpus files produces?

"Files are the source of truth": a clean rebuild must reproduce the graph that
live ingest built. This snapshots node ids+labels and (source, type, target)
edge triples, runs a CLEAN rebuild (POST /api/admin/rebuild-graph?clean=true,
signals re-ingested from signals/*.json), snapshots again and diffs.

Run inside the app container:

    docker exec imi-app python scripts/kb_parity_check.py [--json]

The rebuild replaces the current graph with the file-derived one, so run it
only on a KB whose files you trust (that is what it tests). Exit code 1 when
the snapshots differ, 2 when the rebuild did not complete.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = os.environ.get("IMI_API", "http://localhost:8000")
# Bookkeeping nodes/edges that are not corpus-derived.
_IGNORED_LABELS = ("_TypeRegistry", "_CorpusState")


def snapshot() -> tuple[set, set]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        # Queries name edge types a KB may not have yet (MENTIONED_IN on a
        # fresh graph); the server's "does not exist" warnings are noise here.
        notifications_min_severity="OFF",
    )
    try:
        with driver.session() as s:
            nodes = {
                (r["id"], tuple(sorted(r["labels"])))
                for r in s.run(
                    "MATCH (n) WHERE none(l IN labels(n) WHERE l IN $ignored) "
                    "RETURN coalesce(n.id, elementId(n)) AS id, labels(n) AS labels",
                    ignored=list(_IGNORED_LABELS),
                )
            }
            edges = {
                (r["src"], r["type"], r["dst"])
                for r in s.run(
                    "MATCH (a)-[r]->(b) "
                    "WHERE none(l IN labels(a) + labels(b) WHERE l IN $ignored) "
                    "RETURN coalesce(a.id, elementId(a)) AS src, type(r) AS type, "
                    "coalesce(b.id, elementId(b)) AS dst",
                    ignored=list(_IGNORED_LABELS),
                )
            }
    finally:
        driver.close()
    return nodes, edges


def _post(path: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def rebuild(timeout_s: int = 1800) -> dict:
    _post("/api/admin/rebuild-graph?clean=true&reingest_signals=true")
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        status = _get("/api/admin/rebuild-graph/status")
        if status.get("state") in ("completed", "failed") or status.get("status") in (
            "completed",
            "failed",
        ):
            return status
        time.sleep(5)
    raise TimeoutError("rebuild did not finish")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    live_nodes, live_edges = snapshot()
    status = rebuild()
    state = status.get("state") or status.get("status")
    if state != "completed":
        # A rebuild that failed before touching the graph would otherwise
        # "match" the live snapshot and report parity.
        print(f"rebuild did not complete (state={state!r}): {json.dumps(status, default=str)}",
              file=sys.stderr)
        return 2
    file_nodes, file_edges = snapshot()

    diff = {
        "rebuild_status": status,
        "nodes": {"live": len(live_nodes), "rebuilt": len(file_nodes)},
        "edges": {"live": len(live_edges), "rebuilt": len(file_edges)},
        "only_live_nodes": sorted(map(list, live_nodes - file_nodes)),
        "only_rebuilt_nodes": sorted(map(list, file_nodes - live_nodes)),
        "only_live_edges": sorted(map(list, live_edges - file_edges)),
        "only_rebuilt_edges": sorted(map(list, file_edges - live_edges)),
    }
    if args.json:
        print(json.dumps(diff, indent=2, default=str))
    else:
        print(f"nodes live={len(live_nodes)} rebuilt={len(file_nodes)}; "
              f"edges live={len(live_edges)} rebuilt={len(file_edges)}")
        for key in ("only_live_nodes", "only_rebuilt_nodes", "only_live_edges", "only_rebuilt_edges"):
            items = diff[key]
            print(f"\n{key}: {len(items)}")
            for item in items[:25]:
                print("  ", item)
    differs = any(diff[k] for k in ("only_live_nodes", "only_rebuilt_nodes",
                                     "only_live_edges", "only_rebuilt_edges"))
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
