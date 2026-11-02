"""Storylines: long-running, falsifiable arcs the owner tracks (ADR-011 seeded tier).

A storyline is a ``storyline``-role entity (``library.linking.roles.storyline`` in
``config/lanes.yaml``) with a thesis, a current state, and evidence: capture
Documents linked to it by ``ADVANCES`` edges in the LINK phase. Evidence time is
the Document's ``occurred_at`` (ADR-004).

Field ownership — each field has exactly one writer:

- seed file (``seeds/storyline.yaml``, ``library_seeds``): name, aliases,
  description, thesis, stakes, and ``status: closed``. ``would_change`` and
  ``open_question`` are seeded only while the node's field is empty.
- reviewer (``update_storyline``): ``REVIEW_FIELDS``. Every write appends a
  version to ``memory/storyline_history/<id>.jsonl``.
- server only: ``SERVER_FIELDS`` (status and seed status, lifecycle stamps).
  No client surface may set them; ``guard_client_properties`` enforces that for
  the generic graph mutation tools.

Agents create storylines with ``propose_storyline``: ``seed_status:
provisional``, ``status: proposed``. Listing one in the seed file adopts it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.entity_utils import slugify
from app.utils.event_time import to_iso, to_utc

logger = logging.getLogger(__name__)

ROLE = "storyline"

STATUSES = ("proposed", "active", "dormant", "closed")
DIRECTIONS = ("strengthening", "steady", "fading", "resolved")
CONFIDENCES = ("low", "med", "high")
STAKES = ("practice", "build", "writing", "learning")

# The reviewer's fields (update_storyline / PATCH).
REVIEW_FIELDS = ("state", "direction", "confidence", "would_change", "open_question")
# Set by the server only; rejected from every client write.
SERVER_FIELDS = frozenset({
    "status", "seed_status", "seed_file", "status_changed_at", "status_changed_by",
    "closed_by", "last_reviewed", "proposed_by", "lifecycle_run_id",
})

_TEXT_LIMITS = {"thesis": 500, "state": 4000, "would_change": 1000, "open_question": 500,
                "description": 300}
HISTORY_DIR = Path("memory") / "storyline_history"
MAX_EVIDENCE = 200
MAX_WINDOW_DAYS = 365
ACTIVITY_EDGES = {"topic": "ABOUT", "storyline": "ADVANCES"}  # others: MENTIONED_IN


class StorylineError(ValueError):
    """A client error: unknown storyline, bad field or value."""


# ---------------------------------------------------------------------------
# Type and field helpers
# ---------------------------------------------------------------------------


def domain_types(kg: Any) -> set[str]:
    entities = getattr(getattr(kg, "domain", None), "entities", None) or {}
    return set(entities)


def storyline_type(kg: Any) -> str | None:
    """The active domain's storyline type, or None when the role is off."""
    from app.services.capture_link_policy import linking_policy

    return linking_policy().role_type(ROLE, domain_types(kg) or None)


def _label(entity_type: str) -> str:
    from app.services.graph.neo4j_schema import entity_type_to_label

    return entity_type_to_label(entity_type)


def clean_text(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorylineError(f"{field} must be a string")
    text = value.strip()
    return text[: _TEXT_LIMITS.get(field, 1000)] or None


def clean_stakes(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise StorylineError(f"stakes must be a list of {', '.join(STAKES)}")
    out = []
    for item in items:
        if not isinstance(item, str) or item.strip().lower() not in STAKES:
            raise StorylineError(f"stakes values must be one of {', '.join(STAKES)}")
        if item.strip().lower() not in out:
            out.append(item.strip().lower())
    return out


def _enum(field: str, value: Any, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise StorylineError(f"{field} must be one of {', '.join(allowed)}")
    return value.strip().lower()


def validate_review(fields: dict[str, Any]) -> dict[str, Any]:
    """The reviewer's changes, validated. Anything outside REVIEW_FIELDS is refused."""
    if not isinstance(fields, dict) or not fields:
        raise StorylineError("no fields to update")
    refused = sorted(set(fields) - set(REVIEW_FIELDS))
    if refused:
        server = [f for f in refused if f in SERVER_FIELDS]
        raise StorylineError(
            (f"{', '.join(server)} {'is' if len(server) == 1 else 'are'} set by the server only; "
             if server else "")
            + f"updatable fields are {', '.join(REVIEW_FIELDS)} (refused: {', '.join(refused)})"
        )
    out: dict[str, Any] = {}
    for field, value in fields.items():
        if field == "direction":
            out[field] = _enum(field, value, DIRECTIONS)
        elif field == "confidence":
            out[field] = _enum(field, value, CONFIDENCES)
        else:
            out[field] = clean_text(field, value)
    return out


def is_storyline_node(kg: Any, entity_id: str) -> bool:
    t = storyline_type(kg)
    node = (getattr(kg, "nodes", None) or {}).get(entity_id)
    return bool(t and node is not None and getattr(node, "type", None) == t)


def guard_client_properties(kg: Any, entity_type: str | None, properties: dict[str, Any]) -> None:
    """Refuse server-owned storyline fields from a generic client write
    (graph_add_node / graph_update_node). Raises StorylineError."""
    t = storyline_type(kg)
    if not t or entity_type != t:
        return
    refused = sorted(SERVER_FIELDS.intersection(properties or {}))
    if refused:
        raise StorylineError(
            f"{', '.join(refused)} on a storyline is set by the server only "
            "(lifecycle, seed file); use update_storyline for review fields"
        )


def server_field_changes(kg: Any, entity_type: str | None, new: dict[str, Any],
                         old: dict[str, Any] | None = None) -> list[str]:
    """Server-owned storyline fields a client write would change (a field sent
    back unchanged, as an edit form does, is not a change)."""
    t = storyline_type(kg)
    if not t or entity_type != t:
        return []
    old = old or {}
    return sorted(f for f in SERVER_FIELDS.intersection(new or {}) if new[f] != old.get(f))


# ---------------------------------------------------------------------------
# History (append-only, one JSONL per storyline, in the corpus)
# ---------------------------------------------------------------------------


def history_relpath(entity_id: str) -> str:
    safe = slugify(entity_id) or "storyline"
    return str(HISTORY_DIR / f"{safe}.jsonl")


def append_history(repo_root: Path, entity_id: str, entry: dict[str, Any]) -> str:
    """Append one version row; returns the repo-relative path to commit."""
    rel = history_relpath(entity_id)
    path = Path(repo_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(UTC).isoformat(), "storyline_id": entity_id, **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return rel


def read_history(repo_root: Path, entity_id: str) -> list[dict[str, Any]]:
    path = Path(repo_root) / history_relpath(entity_id)
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def state_as_of(history: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    """Review fields and status folded from every history row at or before as_of."""
    state: dict[str, Any] = {}
    for row in history:
        at = to_utc(row.get("at"))
        if at is None or at > as_of:
            continue
        state.update(row.get("fields") or {})
        if row.get("status_to"):
            state["status"] = row["status_to"]
        state["as_of_version"] = row.get("at")
    return state


async def _commit(git: Any, paths: list[str], message: str) -> bool:
    if git is None or not paths:
        return False
    try:
        await git.commit_and_push(paths, message)
        return True
    except Exception as e:  # files are written; the next commit picks them up
        logger.warning("[STORYLINE] git commit failed (non-fatal): %s", e)
        return False


async def record_status(kg: Any, writer: Any, repo_root: Path, entity_id: str, status_from: str | None,
                        status_to: str, actor: str, reason: str, extra: dict[str, Any] | None = None,
                        commit: bool = True) -> str:
    """Set a storyline's status (server side) and log it. Returns the history path."""
    now = datetime.now(UTC).isoformat()
    await writer.update(entity_id, {"status": status_to, "status_changed_at": now,
                                    "status_changed_by": actor, **(extra or {})})
    rel = append_history(repo_root, entity_id, {
        "event": "status", "actor": actor, "status_from": status_from, "status_to": status_to,
        "reason": reason,
    })
    if commit:
        await _commit(getattr(kg, "git_ops", None), [rel], f"storyline: {entity_id} {status_from} -> {status_to}")
    return rel


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _writer(kg: Any) -> Any:
    from app.services.capture_linker import LibraryEntityWriter

    return LibraryEntityWriter(kg)


def _repo_root(kg: Any) -> Path:
    from app.services.capture_link_store import REPO_ROOT

    return Path(getattr(getattr(kg, "git_ops", None), "repo_path", None) or REPO_ROOT)


def _require(kg: Any, entity_id: str) -> Any:
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise StorylineError("storyline id is required")
    t = storyline_type(kg)
    if not t:
        raise StorylineError("storylines are not enabled (library.linking.roles.storyline)")
    node = (getattr(kg, "nodes", None) or {}).get(entity_id.strip())
    if node is None or getattr(node, "type", None) != t:
        raise StorylineError(f"no storyline with id {entity_id!r}")
    return node


async def update_storyline(entity_id: str, fields: dict[str, Any], *, actor: str,
                           kg: Any | None = None) -> dict[str, Any]:
    """The reviewer's write: REVIEW_FIELDS only, versioned in the history file."""
    kg = kg or _graph()
    node = _require(kg, entity_id)
    changes = validate_review(fields)
    if (getattr(node, "metadata", None) or {}).get("status") == "closed":
        raise StorylineError(f"{node.id} is closed; reopen it in seeds/storyline.yaml first")
    now = datetime.now(UTC).isoformat()
    await _writer(kg).update(node.id, {**changes, "last_reviewed": now})
    rel = append_history(_repo_root(kg), node.id, {"event": "review", "actor": actor, "fields": changes})
    await _commit(getattr(kg, "git_ops", None), [rel], f"storyline: review {node.id} ({', '.join(changes)})")
    return await get_storyline(node.id, kg=kg, evidence=0)


async def propose_storyline(name: str, *, thesis: str | None = None, description: str | None = None,
                            open_question: str | None = None, would_change: str | None = None,
                            stakes: Any = None, actor: str, kg: Any | None = None) -> dict[str, Any]:
    """An agent's proposal: a provisional storyline in status ``proposed``.

    Idempotent on the id (``<type>-<slug of name>``): proposing an existing
    storyline returns it with ``created: false`` and changes nothing."""
    kg = kg or _graph()
    t = storyline_type(kg)
    if not t:
        raise StorylineError("storylines are not enabled (library.linking.roles.storyline)")
    name = clean_text("name", name) if name is not None else None
    if not name:
        raise StorylineError("name is required")
    slug = slugify(name)
    if not slug:
        raise StorylineError("name must contain a letter or digit")
    entity_id = f"{t}-{slug}"
    existing = (getattr(kg, "nodes", None) or {}).get(entity_id)
    if existing is not None:
        out = await get_storyline(entity_id, kg=kg, evidence=0)
        return {**out, "created": False}
    props: dict[str, Any] = {
        "seed_status": "provisional",
        "status": "proposed",
        "created_by": "storyline_proposal",
        "proposed_by": actor,
        "status_changed_at": datetime.now(UTC).isoformat(),
        "status_changed_by": actor,
    }
    for field, value in (("thesis", thesis), ("description", description),
                         ("open_question", open_question), ("would_change", would_change)):
        text = clean_text(field, value)
        if text:
            props[field] = text
    if stakes is not None:
        props["stakes"] = clean_stakes(stakes)
    await _writer(kg).create(t, name, entity_id, props)
    rel = append_history(_repo_root(kg), entity_id, {
        "event": "proposed", "actor": actor, "status_from": None, "status_to": "proposed",
        "fields": {k: v for k, v in props.items() if k in ("thesis", "open_question", "would_change")},
    })
    await _commit(getattr(kg, "git_ops", None), [rel], f"storyline: proposed {entity_id}")
    out = await get_storyline(entity_id, kg=kg, evidence=0)
    return {**out, "created": True}


# ---------------------------------------------------------------------------
# Reads (Neo4j; one aggregate query per call, no per-row work)
# ---------------------------------------------------------------------------


def _graph() -> Any:
    from app.services.graph.factory import get_knowledge_graph

    return get_knowledge_graph()


def _neo4j(kg: Any) -> Any:
    neo4j = getattr(kg, "neo4j", None)
    if neo4j is None:
        raise StorylineError("storyline reads need the Neo4j graph backend")
    return neo4j


_PUBLIC_FIELDS = ("thesis", "description", "state", "direction", "confidence", "would_change",
                  "open_question", "stakes", "status", "seed_status", "last_reviewed",
                  "status_changed_at", "proposed_by", "aliases")


def _shape(props: dict[str, Any]) -> dict[str, Any]:
    out = {"id": props.get("id"), "name": props.get("name")}
    for field in _PUBLIC_FIELDS:
        value = props.get(field)
        if field == "aliases" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [value]
        if field == "stakes" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [value]
        if value not in (None, "", []):
            out[field] = to_iso(value) if field in ("last_reviewed", "status_changed_at") else value
    return out


def _windows(window_days: int, now: datetime | None = None) -> dict[str, datetime]:
    if not isinstance(window_days, int) or not 1 <= window_days <= MAX_WINDOW_DAYS:
        raise StorylineError(f"window must be 1-{MAX_WINDOW_DAYS} days")
    end = now or datetime.now(UTC)
    return {"end": end, "recent_start": end - timedelta(days=window_days),
            "prior_start": end - timedelta(days=2 * window_days)}


async def list_storylines(status: str | None = None, window_days: int = 14, *,
                          include_retired: bool = False, kg: Any | None = None) -> dict[str, Any]:
    """Every storyline with evidence counts: last ``window_days`` vs the window before."""
    kg = kg or _graph()
    t = storyline_type(kg)
    if not t:
        return {"storylines": [], "window_days": window_days, "error": "storylines are not enabled"}
    if status is not None:
        status = _enum("status", status, STATUSES)
    params = {**_windows(window_days), "status": status, "include_retired": include_retired}
    query = (
        f"MATCH (s:{_label(t)}) "
        "WHERE ($status IS NULL OR s.status = $status) "
        "AND ($include_retired OR coalesce(s.seed_status, '') <> 'retired') "
        "OPTIONAL MATCH (d:Document)-[:ADVANCES]->(s) "
        "WITH s, count(DISTINCT d) AS total, "
        "count(DISTINCT CASE WHEN d.occurred_at >= $recent_start AND d.occurred_at < $end THEN d END) AS recent, "
        "count(DISTINCT CASE WHEN d.occurred_at >= $prior_start AND d.occurred_at < $recent_start THEN d END) AS prior, "
        "max(d.occurred_at) AS last_evidence_at "
        "RETURN properties(s) AS props, total, recent, prior, last_evidence_at "
        "ORDER BY recent DESC, total DESC, s.name"
    )
    rows = await _neo4j(kg).execute_read(query, params)
    storylines = []
    for row in rows:
        item = _shape(row.get("props") or {})
        item["evidence"] = {"total": row.get("total", 0), "recent": row.get("recent", 0),
                            "prior": row.get("prior", 0),
                            "last_at": to_iso(row.get("last_evidence_at"))}
        storylines.append(item)
    return {"storylines": storylines, "window_days": window_days, "count": len(storylines)}


async def storyline_evidence(entity_id: str, *, since: Any = None, until: Any = None,
                             stance: str | None = None, max_results: int = 50,
                             kg: Any | None = None) -> dict[str, Any]:
    """The storyline's evidence, newest first by occurred_at."""
    kg = kg or _graph()
    node = _require(kg, entity_id)
    since_dt, until_dt = to_utc(since), to_utc(until)
    if since is not None and since_dt is None:
        raise StorylineError("since must be an ISO-8601 date or timestamp")
    if until is not None and until_dt is None:
        raise StorylineError("until must be an ISO-8601 date or timestamp")
    if stance is not None:
        from app.services.capture_link_store import STANCES

        stance = _enum("stance", stance, STANCES)
    limit = max(1, min(int(max_results or 50), MAX_EVIDENCE))
    query = (
        "MATCH (d:Document)-[r:ADVANCES]->(s:Entity {id: $id}) "
        "WHERE ($since IS NULL OR d.occurred_at >= $since) "
        "AND ($until IS NULL OR d.occurred_at < $until) "
        "AND ($stance IS NULL OR r.stance = $stance) "
        "RETURN d.id AS document_id, d.capture_id AS capture_id, d.title AS title, d.url AS url, "
        "d.summary AS summary, d.source AS source, d.lane AS lane, d.occurred_at AS occurred_at, "
        "d.recorded_at AS recorded_at, r.stance AS stance, r.confidence AS confidence, "
        "r.created_by AS created_by, r.run_id AS run_id "
        "ORDER BY d.occurred_at DESC LIMIT $limit"
    )
    rows = await _neo4j(kg).execute_read(query, {"id": node.id, "since": since_dt, "until": until_dt,
                                                 "stance": stance, "limit": limit})
    evidence = []
    for row in rows:
        item = {k: v for k, v in row.items() if v not in (None, "")}
        for k in ("occurred_at", "recorded_at"):
            if k in item:
                item[k] = to_iso(item[k])
        evidence.append(item)
    return {"storyline_id": node.id, "evidence": evidence, "count": len(evidence),
            "since": to_iso(since_dt), "until": to_iso(until_dt), "stance": stance}


async def get_storyline(entity_id: str, *, as_of: Any = None, evidence: int = 10,
                        window_days: int = 14, kg: Any | None = None) -> dict[str, Any]:
    """One storyline: fields, evidence counts, recent evidence, and (with
    ``as_of``) the review fields as they stood then."""
    kg = kg or _graph()
    node = _require(kg, entity_id)
    params = {"id": node.id, **_windows(window_days)}
    query = (
        "MATCH (s:Entity {id: $id}) "
        "OPTIONAL MATCH (d:Document)-[:ADVANCES]->(s) "
        "WITH s, count(DISTINCT d) AS total, "
        "count(DISTINCT CASE WHEN d.occurred_at >= $recent_start AND d.occurred_at < $end THEN d END) AS recent, "
        "count(DISTINCT CASE WHEN d.occurred_at >= $prior_start AND d.occurred_at < $recent_start THEN d END) AS prior, "
        "max(d.occurred_at) AS last_evidence_at "
        "RETURN properties(s) AS props, total, recent, prior, last_evidence_at"
    )
    rows = await _neo4j(kg).execute_read(query, params)
    row = rows[0] if rows else {"props": {"id": node.id, "name": node.name, **(node.metadata or {})}}
    out = _shape(row.get("props") or {})
    out["evidence_counts"] = {"total": row.get("total", 0), "recent": row.get("recent", 0),
                              "prior": row.get("prior", 0), "window_days": window_days,
                              "last_at": to_iso(row.get("last_evidence_at"))}
    history = read_history(_repo_root(kg), node.id)
    out["versions"] = len([h for h in history if h.get("event") in ("review", "proposed")])
    if as_of is not None:
        as_of_dt = to_utc(as_of)
        if as_of_dt is None:
            raise StorylineError("as_of must be an ISO-8601 date or timestamp")
        out["as_of"] = {"timestamp": as_of_dt.isoformat(), **state_as_of(history, as_of_dt)}
    if evidence:
        out["recent_evidence"] = (await storyline_evidence(
            node.id, max_results=evidence, kg=kg))["evidence"]
    return out


async def storyline_history(entity_id: str, *, max_results: int = 100,
                            kg: Any | None = None) -> dict[str, Any]:
    kg = kg or _graph()
    node = _require(kg, entity_id)
    rows = read_history(_repo_root(kg), node.id)
    limit = max(1, min(int(max_results or 100), 1000))
    return {"storyline_id": node.id, "versions": rows[-limit:][::-1], "count": len(rows)}


async def activity(entity_type: str, window_days: int = 14, *, max_results: int = 100,
                   kg: Any | None = None) -> dict[str, Any]:
    """Evidence per entity of one type: Documents in the last ``window_days``
    vs the window before, by Document ``occurred_at``.

    topic: ``Document-[:ABOUT]->``; storyline: ``Document-[:ADVANCES]->``;
    every other type: ``entity-[:MENTIONED_IN]->Document``."""
    kg = kg or _graph()
    types = domain_types(kg)
    if entity_type not in types:
        raise StorylineError(f"unknown type {entity_type!r}; known: {', '.join(sorted(types))}")
    roles = {}
    from app.services.capture_link_policy import linking_policy

    policy = linking_policy()
    for role in ("topic", "storyline"):
        if (rt := policy.role_type(role, types or None)) is not None:
            roles[rt] = ACTIVITY_EDGES[role]
    label = _label(entity_type)
    edge = roles.get(entity_type)
    match = (f"MATCH (d:Document)-[:{edge}]->(e:{label}) " if edge
             else f"MATCH (e:{label})-[:MENTIONED_IN]->(d:Document) ")
    limit = max(1, min(int(max_results or 100), 1000))
    query = (
        match
        + "WHERE d.occurred_at >= $prior_start AND d.occurred_at < $end "
        "WITH e, count(DISTINCT CASE WHEN d.occurred_at >= $recent_start THEN d END) AS recent, "
        "count(DISTINCT CASE WHEN d.occurred_at < $recent_start THEN d END) AS prior "
        "RETURN e.id AS id, e.name AS name, recent, prior "
        "ORDER BY recent DESC, prior DESC, name LIMIT $limit"
    )
    windows = _windows(window_days)
    rows = await _neo4j(kg).execute_read(query, {**windows, "limit": limit})
    return {"type": entity_type, "window_days": window_days, "edge": edge or "MENTIONED_IN",
            "as_of": windows["end"].isoformat(),
            "items": [{"id": r["id"], "name": r["name"], "recent": r["recent"], "prior": r["prior"]}
                      for r in rows]}


# ---------------------------------------------------------------------------
# Lifecycle (server side; runs at the end of every LINK run and backfill)
# ---------------------------------------------------------------------------

PROMOTE_MIN_EVIDENCE = 5   # proposed -> active: this many ADVANCES ...
PROMOTE_WINDOW_DAYS = 14   # ... with occurred_at in this window
DORMANT_AFTER_DAYS = 30    # active -> dormant: no evidence in this window (and
                           # no status change in it: a new storyline gets a grace period)


def lifecycle_transition(status: str | None, recent_promote: int, recent_dormant: int,
                         status_changed_at: Any, now: datetime) -> tuple[str, str] | None:
    """(new status, reason) for one storyline, or None. Pure: the rules live here."""
    current = status or "proposed"  # a storyline made outside the tools has no status
    if current == "proposed" and recent_promote >= PROMOTE_MIN_EVIDENCE:
        return "active", f"{recent_promote} evidence links in {PROMOTE_WINDOW_DAYS} days"
    if current == "active" and recent_dormant == 0:
        changed = to_utc(status_changed_at)
        if changed is None or now - changed >= timedelta(days=DORMANT_AFTER_DAYS):
            return "dormant", f"no evidence in {DORMANT_AFTER_DAYS} days"
    if current == "dormant" and recent_dormant > 0:
        return "active", f"new evidence within {DORMANT_AFTER_DAYS} days"
    return None


async def apply_lifecycle(kg: Any, writer: Any, repo_root: Path, run_id: str,
                          now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply status transitions to every open storyline.

    Returns (audit rows, history paths to commit). Each row carries
    ``status_from`` so a revert of the run restores it."""
    t = storyline_type(kg)
    neo4j = getattr(kg, "neo4j", None)
    if not t or neo4j is None:
        return [], []
    now = now or datetime.now(UTC)
    rows = await neo4j.execute_read(
        f"MATCH (s:{_label(t)}) "
        "WHERE coalesce(s.status, 'proposed') IN ['proposed', 'active', 'dormant'] "
        "AND coalesce(s.seed_status, '') <> 'retired' "
        "OPTIONAL MATCH (d:Document)-[:ADVANCES]->(s) "
        "WITH s, count(DISTINCT CASE WHEN d.occurred_at >= $promote_start AND d.occurred_at <= $now THEN d END) AS promote, "
        "count(DISTINCT CASE WHEN d.occurred_at >= $dormant_start AND d.occurred_at <= $now THEN d END) AS dormant "
        "RETURN s.id AS id, s.status AS status, s.status_changed_at AS changed_at, promote, dormant",
        {"now": now, "promote_start": now - timedelta(days=PROMOTE_WINDOW_DAYS),
         "dormant_start": now - timedelta(days=DORMANT_AFTER_DAYS)},
    )
    audit, paths = [], []
    for row in rows:
        change = lifecycle_transition(row.get("status"), row.get("promote", 0), row.get("dormant", 0),
                                      row.get("changed_at"), now)
        if change is None:
            continue
        status_to, reason = change
        try:
            paths.append(await record_status(kg, writer, repo_root, row["id"], row.get("status"),
                                             status_to, "lifecycle", reason,
                                             extra={"lifecycle_run_id": run_id}, commit=False))
        except Exception as e:  # one storyline never stops the run
            logger.warning("[STORYLINE] lifecycle %s -> %s failed: %s", row["id"], status_to, e)
            continue
        audit.append({"run_id": run_id, "mode": "on", "action": "storyline_status",
                      "entity_id": row["id"], "status_from": row.get("status"),
                      "status_to": status_to, "reason": reason})
    return audit, paths


async def revert_lifecycle(kg: Any, writer: Any, repo_root: Path, run_id: str,
                           rows: list[dict[str, Any]]) -> list[str]:
    """Undo a run's status transitions (newest first), where the storyline is
    still in the status the run set. Returns history paths to commit."""
    nodes = getattr(kg, "nodes", None) or {}
    paths = []
    for row in reversed(rows):
        node = nodes.get(row.get("entity_id"))
        if node is None or (getattr(node, "metadata", None) or {}).get("status") != row.get("status_to"):
            continue
        restore = row.get("status_from") or "proposed"
        paths.append(await record_status(kg, writer, repo_root, node.id, row.get("status_to"), restore,
                                         "revert", f"revert {run_id}", commit=False))
    return paths
