#!/usr/bin/env python3
"""Knowledge-graph quality report for the active KB.

Run inside the app container (it reads NEO4J_* from the environment and the
corpus at /app/repo):

    docker exec imi-app python scripts/kb_quality_report.py            # markdown
    docker exec imi-app python scripts/kb_quality_report.py --json     # machine-readable

Reports the defect classes that live ingest has produced: disconnected
entities/signals, duplicate candidates, placeholder names, entities whose
graph node and markdown file disagree. Numbers are meant to be compared run
over run while iterating on extraction/resolution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(os.environ.get("IMI_REPO_PATH", "/app/repo"))


def _session():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        # Queries name edge types a KB may not have yet (MENTIONED_IN on a
        # fresh graph); the server's "does not exist" warnings are noise here.
        notifications_min_severity="OFF",
    )
    return driver, driver.session()


def _q(session, query: str, **params) -> list[dict]:
    return [dict(r) for r in session.run(query, **params)]


def _frontmatter(path: Path) -> dict:
    import yaml

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    try:
        data = yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def collect() -> dict:
    from app.services.entity_resolver import normalize_entity_name

    try:
        from app.services.entity_utils import is_placeholder_entity_name
    except ImportError:  # older deployment without the prefilter
        def is_placeholder_entity_name(name: str) -> bool:
            return False

    driver, s = _session()
    try:
        entities = _q(
            s,
            "MATCH (e:Entity) WHERE NOT e:Signal "
            "RETURN e.id AS id, e.name AS name, e.entity_type AS type, "
            "coalesce(e.stub, false) AS stub, e.source_file AS source_file, "
            "COUNT { (e)--() } AS degree, "
            "COUNT { (e)-[:MENTIONED_IN]->(:Document) } AS docs, "
            "COUNT { (e)--(:Entity) } AS entity_links",
        )
        signals = _q(
            s,
            "MATCH (s:Signal) RETURN s.id AS id, left(s.content, 80) AS content, "
            "COUNT { (s)--() } AS degree, COUNT { (s)-[:FROM_DOCUMENT]->() } AS docs",
        )
        documents = _q(s, "MATCH (d:Document) RETURN count(d) AS n")[0]["n"]
        rel_types = _q(
            s, "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC"
        )
    finally:
        s.close()
        driver.close()

    by_type: dict[str, int] = defaultdict(int)
    for e in entities:
        by_type[e["type"] or "?"] += 1

    # Duplicate candidates: same normalized name within a type; a bare first
    # name that matches exactly one multi-word person.
    groups: dict[tuple, list] = defaultdict(list)
    for e in entities:
        groups[(e["type"], normalize_entity_name(e["name"] or "", e["type"] or ""))].append(e["id"])
    same_name = [ids for ids in groups.values() if len(ids) > 1]
    people = [e for e in entities if e["type"] == "person"]
    full_by_first: dict[str, list] = defaultdict(list)
    for p in people:
        toks = normalize_entity_name(p["name"] or "", "person").split()
        if len(toks) > 1:
            full_by_first[toks[0]].append(p["id"])
    first_name_dups = []
    for p in people:
        toks = normalize_entity_name(p["name"] or "", "person").split()
        if len(toks) == 1 and len(full_by_first.get(toks[0], [])) == 1:
            first_name_dups.append([p["id"], full_by_first[toks[0]][0]])

    # Graph <-> files
    node_ids = {e["id"] for e in entities}
    archived_ids, live_file_ids = set(), set()
    for path in REPO.glob("*/*.md"):
        if path.parts[-2] in {"meetings", "deltas", "digests", "signals", "memory"}:
            continue
        fm = _frontmatter(path)
        eid = fm.get("id")
        if not eid:
            continue
        (archived_ids if fm.get("is_archived") else live_file_ids).add(eid)

    return {
        "counts": {
            "entities": len(entities),
            "entities_by_type": dict(sorted(by_type.items())),
            "stubs": sum(1 for e in entities if e["stub"]),
            "signals": len(signals),
            "documents": documents,
            "relationship_types": {r["type"]: r["n"] for r in rel_types},
        },
        "disconnected": {
            "entities_no_edges": sorted(e["id"] for e in entities if e["degree"] == 0),
            "entities_no_document": sorted(e["id"] for e in entities if e["docs"] == 0),
            "entities_no_entity_link": sorted(e["id"] for e in entities if e["entity_links"] == 0),
            "signals_no_edges": sorted(x["id"] for x in signals if x["degree"] == 0),
            "signals_no_document": sorted(x["id"] for x in signals if x["docs"] == 0),
        },
        "duplicates": {
            "same_normalized_name": same_name,
            "first_name_vs_full_name": first_name_dups,
        },
        "junk": {
            "placeholder_names": sorted(
                e["id"] for e in entities if is_placeholder_entity_name(e["name"] or "")
            ),
            "single_word_people": sorted(
                e["name"] for e in people if len((e["name"] or "").split()) == 1
            ),
        },
        "graph_vs_files": {
            "nodes_without_source_file": sorted(
                e["id"] for e in entities if not e["stub"] and not e["source_file"]
            ),
            "nodes_without_live_file": sorted(
                e["id"] for e in entities if not e["stub"] and e["id"] not in live_file_ids
            ),
            "live_files_without_node": sorted(live_file_ids - node_ids),
            "archived_but_still_nodes": sorted(archived_ids & node_ids),
        },
    }


def render(report: dict) -> str:
    c = report["counts"]
    lines = [
        "# KB quality report",
        "",
        f"Entities **{c['entities']}** ({', '.join(f'{k} {v}' for k, v in c['entities_by_type'].items())}), "
        f"stubs {c['stubs']}, signals {c['signals']}, documents {c['documents']}",
        "",
        "Relationships: " + ", ".join(f"{k} {v}" for k, v in c["relationship_types"].items()),
        "",
        "| check | count | examples |",
        "|---|---|---|",
    ]
    for section in ("disconnected", "duplicates", "junk", "graph_vs_files"):
        for key, items in report[section].items():
            examples = ", ".join(str(i) for i in items[:6])
            lines.append(f"| {section}.{key} | {len(items)} | {examples} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = ap.parse_args()
    report = collect()
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
