"""The LINK phase: library captures into the graph (ADR-011).

``run_link`` processes up to N unlinked library captures, outside any request
that created them (§2):

  1. sync the seed files (topics, storylines, essays) into seeded entities;
  2. derive each capture's source by rule and link FROM_SOURCE (deterministic);
  3. ask the model (operation ``capture_linking``) to select seeded items and
     name substantive mentions, in batches of title + summary + source;
  4. resolve mentions against existing entities; names that do not resolve
     stay as text in the link file;
  5. corroborate: a create_types name (or an unlisted theme) seen in enough
     captures from enough sources within the window is put to entity
     admission, created, and back-linked to every capture that named it;
  6. write link files, project them into the graph, append the audit log,
     commit, and return a changelog.

``mode`` (lanes.yaml ``library.linking.mode``): ``off`` does nothing;
``shadow`` writes link files and audit rows but projects nothing and creates
no entity; ``on`` acts. Seeds sync in both modes: they are the owner's.

``revert_run`` undoes one run. ``attach`` / ``detach`` are the owner's links
(§5), which pin a library capture against decay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from app.services.capture_link_policy import SEEDED_ROLES, SOURCE_ROLE, LinkingPolicy, linking_policy
from app.services.capture_link_store import (
    ABOUT,
    ADVANCES,
    CAPTURE_TARGET,
    EVIDENCE_FOR,
    FROM_SOURCE,
    LINK_PHASE,
    LINK_VERSION,
    MENTIONED_IN,
    OWNER,
    STANCES,
    CaptureLink,
    CaptureLinkFile,
    CaptureLinkStore,
    LinkAuditLog,
    UnresolvedName,
    now_iso,
)
from app.services.capture_sources import derive_source
from app.services.entity_resolver import make_slug, resolve_against
from app.services.graph.capture_graph_writer import capture_title
from app.services.library_seeds import PROVISIONAL, offered, sync_role
from app.services.memory_capture import REPO_ROOT, CaptureStore
from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

LINK_OPERATION = "capture_linking"
PROMPT_NAME = "capture_linking"
MAX_RESULTS_CAP = 500
MAX_ABOUT = 3
MAX_KNOWN_ENTITIES = 60
_SUMMARY_CHARS = 600
_ROLE_EDGES = {"topic": ABOUT, "storyline": ADVANCES, "essay": EVIDENCE_FOR}
_RESPONSE_KEYS = {"topic": "about", "storyline": "advances", "essay": "evidence_for"}
_PERSON_TYPES = ("person", "contact")

_run_lock = asyncio.Lock()


class LinkRunBusy(RuntimeError):
    """A link run (or revert) is already in progress."""


# ---------------------------------------------------------------------------
# Entity writes (file write-through via the graph mutation layer)
# ---------------------------------------------------------------------------


class LibraryEntityWriter:
    """Create, update and archive entities through the graph mutation layer,
    so every node the LINK phase writes has its markdown file (invariant 2)."""

    def __init__(self, kg: Any):
        self.kg = kg

    async def create(
        self,
        entity_type: str,
        name: str,
        entity_id: str | None,
        properties: dict[str, Any],
        aliases: list[str] | tuple[str, ...] = (),
    ) -> str:
        node = await self.kg.add_node(entity_type, name, entity_id=entity_id, properties=properties)
        # An id whose file was archived (e.g. by a revert) keeps that file, so
        # add_node's write-through skips it: un-archive through the file layer
        # or the next rebuild drops the node again.
        is_archived = getattr(self.kg, "_is_entity_archived", None)
        if is_archived is not None and is_archived(node["id"]):
            await self.update(node["id"], {"is_archived": False})
        if aliases:
            # add_node strips aliases (identity field); set them as an update.
            await self.update(node["id"], {"aliases": list(aliases)})
        return node["id"]

    async def update(self, entity_id: str, changes: dict[str, Any]) -> None:
        from app.services.tools.graph_node_tools import _update_source_file

        await _update_source_file(
            graph_service=self.kg, entity_id=entity_id, properties=changes, git_ops=self.kg.git_ops
        )
        await self.kg.update_node(entity_id, changes)

    async def archive(self, entity_id: str) -> None:
        await self.kg.delete_node(entity_id, cascade=True)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class LinkContext:
    kg: Any
    policy: LinkingPolicy
    repo_root: Path
    captures: CaptureStore
    links: CaptureLinkStore
    audit: LinkAuditLog
    entities: Any  # LibraryEntityWriter
    graph: Any | None  # CaptureGraphWriter; None skips projection
    claude_client: Any | None = None
    decision_client: Any | None = None
    git: Any | None = None

    @property
    def domain_types(self) -> dict[str, str]:
        domain = getattr(self.kg, "domain", None)
        entities = getattr(domain, "entities", None) or {}
        return {name: (getattr(ent, "description", "") or "") for name, ent in entities.items()}

    def role_type(self, role: str) -> str | None:
        return self.policy.role_type(role, set(self.domain_types) or None)

    @property
    def create_types(self) -> frozenset[str]:
        return self.policy.create_types(set(self.domain_types) or None)

    @property
    def mention_types(self) -> list[str]:
        """Types a capture may link to by mention: every domain type no
        deterministic or seeded role owns."""
        owned = {self.role_type(r) for r in (SOURCE_ROLE, *SEEDED_ROLES)}
        return sorted(t for t in self.domain_types if t not in owned)


def default_context(repo_root: Path = REPO_ROOT) -> LinkContext:
    from app.services.claude_client import get_claude_client
    from app.services.graph.capture_graph_writer import CaptureGraphWriter
    from app.services.graph.factory import get_knowledge_graph

    kg = get_knowledge_graph()
    repo = Path(getattr(getattr(kg, "git_ops", None), "repo_path", None) or repo_root)
    captures = CaptureStore(capture_dir=repo / "memory" / "captures", repo_root=repo)
    try:
        claude = get_claude_client()
    except Exception as e:
        logger.warning("[LINK] No Claude client: %s", e)
        claude = None
    return LinkContext(
        kg=kg,
        policy=linking_policy(),
        repo_root=repo,
        captures=captures,
        links=CaptureLinkStore(repo_root=repo),
        audit=LinkAuditLog(repo_root=repo),
        entities=LibraryEntityWriter(kg),
        graph=CaptureGraphWriter(kg.neo4j, captures.relative_path) if getattr(kg, "neo4j", None) else None,
        claude_client=claude,
        git=getattr(kg, "git_ops", None),
    )


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------


@dataclass
class Changelog:
    run_id: str
    mode: str
    processed: int = 0
    remaining: int = 0
    links_by_type: Counter = field(default_factory=Counter)
    entities_created: list[dict[str, str]] = field(default_factory=list)
    provisional_topics: list[dict[str, str]] = field(default_factory=list)
    would_create: list[dict[str, Any]] = field(default_factory=list)
    admission_dropped: list[dict[str, str]] = field(default_factory=list)
    below_bar: list[dict[str, Any]] = field(default_factory=list)
    sources_created: list[str] = field(default_factory=list)
    seeds: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    committed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "link_version": LINK_VERSION,
            "processed": self.processed,
            "remaining": self.remaining,
            "links_by_type": dict(self.links_by_type),
            "entities_created": self.entities_created,
            "provisional_topics": self.provisional_topics,
            "would_create": self.would_create,
            "admission_dropped": self.admission_dropped,
            "below_bar": self.below_bar,
            "sources_created": self.sources_created,
            "seeds": self.seeds,
            "errors": self.errors,
            "committed": self.committed,
        }


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


def capture_brief(memory: Any, ref: str) -> dict[str, Any]:
    """What the model sees of a capture: title + summary + source, never the
    full text (ADR-011 §7). An unsummarized capture sends its opening."""
    summary = getattr(memory, "summary", None) or (memory.enrichment or {}).get("summary")
    if not summary:
        summary = " ".join((memory.content or "").split())[:_SUMMARY_CHARS]
    return {
        "ref": ref,
        "title": capture_title(memory),
        "summary": summary,
        "source": memory.source,
        "date": (memory.source_date or "")[:10] or None,
    }


def _known_entities(kg: Any, mention_types: list[str], text: str) -> list[dict[str, str]]:
    """Existing entities of the mention types whose name (or an alias) appears
    in the batch text: canonical names for the model to reuse."""
    wanted = set(mention_types)
    lowered = text.lower()
    found = []
    for node in list((getattr(kg, "nodes", None) or {}).values()):
        if getattr(node, "type", None) not in wanted:
            continue
        metadata = getattr(node, "metadata", None) or {}
        if metadata.get("stub"):
            continue
        aliases = metadata.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        for surface in (node.name, *aliases):
            if (
                isinstance(surface, str)
                and len(surface) >= 3
                and re.search(rf"(?<!\w){re.escape(surface.lower())}(?!\w)", lowered)
            ):
                found.append({"name": node.name, "type": node.type})
                break
        if len(found) >= MAX_KNOWN_ENTITIES:
            break
    return found


async def call_linker(
    client: Any, payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """One capture_linking call -> {ref: selections}. Empty on any failure."""
    from app.config import settings
    from app.services.capture_enrichment import _extract_response_text, _parse_json_object
    from app.services.prompt_loader import load_prompt

    try:
        response = await client.generate_message(
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            model=settings.CLAUDE_SONNET_MODEL,
            max_tokens=500 * max(1, len(payload.get("captures") or [])) + 500,
            temperature=0.0,
            system=load_prompt(PROMPT_NAME),
            operation=LINK_OPERATION,
        )
    except Exception as e:
        logger.warning("[LINK] capture_linking call failed: %s", e)
        return {}
    text = _extract_response_text(response)
    parsed = _parse_json_object(text) if text else None
    if not parsed or not isinstance(parsed.get("captures"), list):
        logger.warning("[LINK] Unparseable capture_linking response")
        return {}
    return {
        str(item["ref"]): item
        for item in parsed["captures"]
        if isinstance(item, dict) and item.get("ref") is not None
    }


def _confidence(item: dict[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(item.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def _items(selection: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = selection.get(key)
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _is_person_type(entity_type: str) -> bool:
    return entity_type.lower() in _PERSON_TYPES


def _full_name(name: str) -> bool:
    return len([t for t in re.split(r"\s+", name.strip()) if t]) >= 2


def resolve_mention(resolver: Any, entity_type: str, name: str) -> tuple[str, str | None]:
    """("link", id) | ("new", None) | ("ambiguous", None).

    People link only on an exact or alias match of a full name: an author
    must never merge into the owner's contacts on a first name or a fuzzy
    match (ADR-011 §3). Such a near miss is ambiguous: left as text and never
    created."""
    hit = resolver.resolve(entity_type, name)
    if hit.matched_via == "new":
        if _is_person_type(entity_type) and not _full_name(name):
            return "ambiguous", None
        return "new", None
    if _is_person_type(entity_type) and (
        hit.matched_via not in ("exact", "alias") or not _full_name(name)
    ):
        return "ambiguous", None
    return "link", hit.id


def _resolver(kg: Any) -> Any:
    from app.services.entity_resolver import EntityResolver

    # Heuristic tiers only: a bulk library run must not fan out tiebreak calls.
    return EntityResolver(knowledge_graph=kg, decisions=None)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _unlinked(ctx: LinkContext, mode: str) -> list[tuple[Any, CaptureLinkFile | None]]:
    out = []
    for memory in ctx.captures.iter_all():
        if memory.lane != "library" or memory.review_status == "rejected" or memory.superseded_by:
            continue
        record = ctx.links.get(memory.id)
        if record is not None and record.is_linked(mode):
            continue
        out.append((memory, record))
    # Newest first: a daily run links today's captures before the backfill.
    out.sort(key=lambda pair: pair[0].created_at or "", reverse=True)
    return out


def _source_key(memory: Any, source_ref: Any) -> str:
    return source_ref.key if source_ref else f"capture-source:{memory.source}"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def run_link(max_results: int = 50, ctx: LinkContext | None = None) -> dict[str, Any]:
    """One LINK run. Raises LinkRunBusy when another run holds the lock."""
    if _run_lock.locked():
        raise LinkRunBusy("a link run is already in progress")
    async with _run_lock:
        ctx = ctx or default_context()
        return await _run(ctx, max(1, min(int(max_results), MAX_RESULTS_CAP)))


async def _run(ctx: LinkContext, max_results: int) -> dict[str, Any]:
    mode = ctx.policy.mode
    log = Changelog(run_id=f"link-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}", mode=mode)
    if mode == "off":
        log.errors.append("library.linking.mode is off in config/lanes.yaml")
        return log.as_dict()
    if not ctx.domain_types:
        log.errors.append("no active domain: entity types unknown")
        return log.as_dict()

    build = getattr(ctx.kg, "build_graph", None)
    if build is not None:
        await build()

    # 1. seeds (owner content: synced in shadow too)
    for role in SEEDED_ROLES:
        entity_type = ctx.role_type(role)
        if entity_type:
            result = await sync_role(ctx.kg, ctx.entities, ctx.repo_root, role, entity_type)
            if result.as_dict():
                log.seeds[role] = result.as_dict()

    pending = _unlinked(ctx, mode)
    batch = pending[:max_results]
    log.remaining = len(pending) - len(batch)
    if not batch:
        return log.as_dict()

    audit_rows: list[dict[str, Any]] = []
    linked_at = now_iso()
    records: dict[str, CaptureLinkFile] = {}
    memories = {m.id: m for m, _ in batch}

    # 2. deterministic sources
    source_type = ctx.role_type(SOURCE_ROLE)
    for memory, previous in batch:
        ref = derive_source(memory.source, memory.source_id, memory.content)
        record = CaptureLinkFile(
            capture_id=memory.id,
            mode="shadow" if mode == "shadow" else "on",
            run_id=log.run_id,
            linked_at=linked_at,
            lane=memory.lane,
            occurred_at=memory.source_date,
            source_key=_source_key(memory, ref),
            links=previous.owner_links() if previous else [],
            reverted_runs=previous.reverted_runs if previous else [],
        )
        if ref and source_type:
            source_id = ref.entity_id(source_type)
            if source_id not in (getattr(ctx.kg, "nodes", None) or {}) and mode == "on":
                try:
                    await ctx.entities.create(
                        source_type,
                        ref.name,
                        source_id,
                        {"medium": ref.medium, "source_key": ref.key, "created_by": LINK_PHASE,
                         "run_id": log.run_id, **({"url": ref.url} if ref.url else {})},
                    )
                    log.sources_created.append(source_id)
                    audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "create_entity",
                                       "entity_id": source_id, "entity_type": source_type,
                                       "tier": "deterministic", "key": ref.key})
                except Exception as e:
                    log.errors.append(f"source {source_id}: {e}")
                    source_id = None
            if source_id:
                record.links.append(
                    CaptureLink(edge=FROM_SOURCE, target_id=source_id, target_type=source_type,
                                run_id=log.run_id, linked_at=linked_at)
                )
        records[memory.id] = record

    # 3-4. model selections, resolved
    seeded = {
        role: offered(ctx.kg, t) for role in SEEDED_ROLES if (t := ctx.role_type(role))
    }
    seeded_ids = {role: {s["id"] for s in items} for role, items in seeded.items()}
    topic_type = ctx.role_type("topic")
    resolver = _resolver(ctx.kg)
    chunks = [batch[i : i + ctx.policy.batch_size] for i in range(0, len(batch), ctx.policy.batch_size)]
    semaphore = asyncio.Semaphore(ctx.policy.concurrency)

    async def select(chunk: list) -> tuple[list[str], dict[str, dict[str, Any]] | None]:
        """(capture ids, selections by capture id), or None for a failed call."""
        refs = {f"c{i + 1}": memory for i, (memory, _) in enumerate(chunk)}
        briefs = [capture_brief(memory, ref) for ref, memory in refs.items()]
        text = " ".join(f"{b['title']} {b['summary']}" for b in briefs)
        payload: dict[str, Any] = {
            "types": {t: ctx.domain_types[t] for t in ctx.mention_types},
            "known_entities": _known_entities(ctx.kg, ctx.mention_types, text),
            "captures": briefs,
        }
        for role, items in seeded.items():
            payload[role + "s"] = items
        ids = [memory.id for memory in refs.values()]
        if ctx.claude_client is None:
            return ids, None
        async with semaphore:
            answers = await call_linker(ctx.claude_client, payload)
        if not answers:
            return ids, None
        return ids, {refs[ref].id: answer for ref, answer in answers.items() if ref in refs}

    # A capture the model answered for (even with empty lists) is linked; one
    # whose call failed, or that the answer left out, stays unlinked so the
    # next run retries it.
    selections: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for ids, answers in await asyncio.gather(*(select(c) for c in chunks)):
        if answers is None:
            failed.extend(ids)
            continue
        selections.update(answers)
        failed.extend(i for i in ids if i not in answers)
    if failed:
        log.errors.append(
            f"{len(failed)} capture(s) left unlinked: "
            + ("no Claude client" if ctx.claude_client is None
               else "capture_linking call failed or left them out")
        )
        for capture_id in failed:
            records.pop(capture_id, None)
        log.remaining += len(failed)

    floor = ctx.policy.min_confidence
    for capture_id, selection in selections.items():
        record = records[capture_id]

        def add(edge: str, target_id: str, target_type: str, confidence: float, stance=None,
                record: CaptureLinkFile = record) -> None:
            _add_link(record, CaptureLink(edge=edge, target_id=target_id, target_type=target_type,
                                          confidence=confidence, run_id=log.run_id,
                                          linked_at=linked_at, stance=stance))

        for role, ids in seeded_ids.items():
            chosen = sorted(
                (i for i in _items(selection, _RESPONSE_KEYS[role])
                 if i.get("id") in ids and _confidence(i) >= floor),
                key=_confidence, reverse=True,
            )
            if role == "topic":
                chosen = chosen[:MAX_ABOUT]
            for item in chosen:
                stance = None
                if role == "essay":
                    stance = item.get("stance") if item.get("stance") in STANCES else "context"
                add(_ROLE_EDGES[role], item["id"], ctx.role_type(role) or role, _confidence(item), stance)

        for item in _items(selection, "mentions"):
            name = item.get("name")
            entity_type = item.get("type")
            if not isinstance(name, str) or not name.strip() or entity_type not in ctx.mention_types:
                continue
            if _confidence(item) < floor:
                continue
            name = " ".join(name.split())
            verdict, target = resolve_mention(resolver, entity_type, name)
            if verdict == "link" and target:
                add(MENTIONED_IN, target, entity_type, _confidence(item))
            elif verdict == "new":
                record.unresolved.append(
                    UnresolvedName(name=name, entity_type=entity_type, confidence=_confidence(item))
                )

        if topic_type:
            for item in _items(selection, "new_topics"):
                name = item.get("name")
                if not isinstance(name, str) or not name.strip() or _confidence(item) < floor:
                    continue
                name = " ".join(name.split())
                hit = resolve_against(topic_type, name, [
                    {"id": s["id"], "name": s["name"]} for s in seeded.get("topic", [])
                ])
                if hit.matched_via in ("exact", "alias"):
                    add(ABOUT, hit.id, topic_type, _confidence(item))
                else:
                    record.unresolved.append(
                        UnresolvedName(name=name, entity_type=topic_type, kind="topic",
                                       confidence=_confidence(item))
                    )

    for capture_id, record in records.items():
        for link in record.links:
            if link.created_by == LINK_PHASE:
                log.links_by_type[link.edge] += 1
                audit_rows.append({
                    "run_id": log.run_id, "mode": mode, "action": "link", "capture_id": capture_id,
                    "edge": link.edge, "target_id": link.target_id, "confidence": link.confidence,
                    **({"stance": link.stance} if link.stance else {}),
                })
        for name in record.unresolved:
            audit_rows.append({
                "run_id": log.run_id, "mode": mode, "action": "unresolved", "capture_id": capture_id,
                "name": name.name, "entity_type": name.entity_type, "kind": name.kind,
                "confidence": name.confidence,
            })
        # Merge, not overwrite: an owner link added while the run was working
        # must survive (owner links are the file's, not the run's).
        ctx.links.mutate(capture_id, partial(_merge_run_links, run_record=record))
    log.processed = len(records)

    # 5. corroboration
    backlinked = await _corroborate(ctx, log, records, audit_rows, memories)

    # 6. project, audit, commit
    touched = set(records) | backlinked
    if mode == "on" and ctx.graph is not None:
        items = []
        for capture_id in touched:
            memory = memories.get(capture_id) or ctx.captures.get(capture_id)
            record = ctx.links.get(capture_id)
            if memory is not None and record is not None:
                items.append((memory, record))
        try:
            await ctx.graph.write(items)
        except Exception as e:
            logger.warning("[LINK] Graph projection failed (files are written): %s", e, exc_info=True)
            log.errors.append(f"graph projection: {e}")

    audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "run_summary",
                       **{k: v for k, v in log.as_dict().items() if k not in ("run_id", "mode")}})
    ctx.audit.append_many(audit_rows)
    log.committed = await _commit(
        ctx, [ctx.links.relative_path(c) for c in sorted(touched)] + [ctx.audit.relative_path()],
        f"link: {log.run_id} ({mode}, {log.processed} captures)",
    )
    logger.info("[LINK] %s %s: %d processed, links %s, created %d, %d remaining",
                mode, log.run_id, log.processed, dict(log.links_by_type),
                len(log.entities_created), log.remaining)
    return log.as_dict()


# ---------------------------------------------------------------------------
# Corroboration
# ---------------------------------------------------------------------------


@dataclass
class _Cluster:
    entity_type: str
    kind: str
    forms: Counter = field(default_factory=Counter)
    members: dict[str, tuple[str | None, str, float]] = field(default_factory=dict)  # capture -> (occurred_at, source_key, conf)

    @property
    def name(self) -> str:
        return max(self.forms.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def passes_bar(members: list[tuple[datetime, str]], min_captures: int, min_sources: int,
               window_days: int) -> bool:
    """Some window of ``window_days`` holds ``min_captures`` captures from
    ``min_sources`` distinct sources. ``members`` are (occurred_at, source)."""
    ordered = sorted(members)
    span = timedelta(days=window_days)
    start = 0
    for end in range(len(ordered)):
        while ordered[end][0] - ordered[start][0] > span:
            start += 1
        window = ordered[start : end + 1]
        if len(window) >= min_captures and len({s for _, s in window}) >= min_sources:
            return True
    return False


def build_ledger(records: list[CaptureLinkFile], create_types: frozenset[str],
                 topic_type: str | None) -> list[_Cluster]:
    """Cluster unresolved names across link files (same type, same entity by
    the resolver's heuristic tiers)."""
    clusters: dict[str, list[_Cluster]] = defaultdict(list)
    for record in records:
        for name in record.unresolved:
            if name.kind == "topic":
                if name.entity_type != topic_type:
                    continue
            elif name.entity_type not in create_types:
                continue
            bucket = clusters[f"{name.kind}:{name.entity_type}"]
            candidates = [{"id": str(i), "name": c.name} for i, c in enumerate(bucket)]
            hit = resolve_against(name.entity_type, name.name, candidates)
            strict = _is_person_type(name.entity_type) and hit.matched_via == "fuzzy"
            if hit.matched_via == "new" or strict:
                cluster = _Cluster(entity_type=name.entity_type, kind=name.kind)
                bucket.append(cluster)
            else:
                cluster = bucket[int(hit.id)]
            cluster.forms[name.name] += 1
            cluster.members.setdefault(
                record.capture_id, (record.occurred_at, record.source_key or record.capture_id,
                                    name.confidence)
            )
    return [c for bucket in clusters.values() for c in bucket]


async def _corroborate(ctx: LinkContext, log: Changelog, records: dict[str, CaptureLinkFile],
                       audit_rows: list[dict[str, Any]], memories: dict[str, Any]) -> set[str]:
    """Create entities whose names crossed the bar; back-link their captures.
    Returns the capture ids whose link files changed."""
    mode = ctx.policy.mode
    topic_type = ctx.role_type("topic")
    create_types = ctx.create_types
    if not create_types and not topic_type:
        return set()
    all_records = {r.capture_id: r for r in ctx.links.iter_all()}
    all_records.update(records)
    clusters = build_ledger(list(all_records.values()), create_types, topic_type)
    bar = ctx.policy.corroboration
    resolver = _resolver(ctx.kg)
    passing: list[_Cluster] = []
    for cluster in clusters:
        if not set(cluster.members) & set(records):
            continue  # nothing new from this run
        dated = [(at, src) for occurred, src, _ in cluster.members.values()
                 if (at := to_utc(occurred)) is not None]
        if passes_bar(dated, bar.min_captures, bar.min_sources, bar.window_days):
            passing.append(cluster)
        else:
            log.below_bar.append({"name": cluster.name, "entity_type": cluster.entity_type,
                                  "kind": cluster.kind, "captures": len(cluster.members),
                                  "sources": len({s for _, s, _ in cluster.members.values()})})
    log.below_bar.sort(key=lambda b: (-b["captures"], b["name"]))
    del log.below_bar[25:]
    if not passing:
        return set()

    verdicts = await _admit(ctx, passing, memories)
    changed: set[str] = set()
    for cluster in passing:
        name, entity_type = cluster.name, cluster.entity_type
        verdict = verdicts.get((entity_type, name))
        if verdict is not None and verdict.action == "drop":
            log.admission_dropped.append({"name": name, "entity_type": entity_type})
            audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "admission_drop",
                               "name": name, "entity_type": entity_type, "p_named": verdict.p_named})
            continue
        if verdict is not None and verdict.action == "retype":
            if verdict.new_type in create_types:
                entity_type = verdict.new_type
            else:
                log.admission_dropped.append({"name": name, "entity_type": entity_type,
                                              "retyped_to": verdict.new_type or ""})
                continue
        is_topic = cluster.kind == "topic"
        state, target = ("new", None) if is_topic else resolve_mention(resolver, entity_type, name)
        if is_topic:
            hit = resolver.resolve(entity_type, name)
            if hit.matched_via in ("exact", "alias"):
                state, target = "link", hit.id
        if state == "ambiguous":
            log.below_bar.append({"name": name, "entity_type": entity_type, "kind": cluster.kind,
                                  "captures": len(cluster.members), "reason": "ambiguous person"})
            continue
        if state == "new":
            entity_id = make_slug(entity_type, name)
            entry = {"id": entity_id, "name": name, "entity_type": entity_type,
                     "captures": len(cluster.members)}
            if mode != "on":
                log.would_create.append({**entry, "provisional_topic": is_topic})
                audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "would_create",
                                   **entry, "tier": "topic" if is_topic else "corroborated"})
                continue
            props: dict[str, Any] = {"created_by": LINK_PHASE, "run_id": log.run_id}
            if is_topic:
                props["seed_status"] = PROVISIONAL
            elif _is_person_type(entity_type):
                props["relationship"] = "public"
            try:
                target = await ctx.entities.create(entity_type, name, entity_id, props)
            except Exception as e:
                log.errors.append(f"create {entity_id}: {e}")
                continue
            entry["id"] = target
            (log.provisional_topics if is_topic else log.entities_created).append(entry)
            audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "create_entity",
                               **entry, "tier": "topic" if is_topic else "corroborated",
                               "evidence": sorted(cluster.members)})
        if mode != "on" or not target:
            continue
        edge = ABOUT if is_topic else MENTIONED_IN
        for capture_id, (_, _, confidence) in cluster.members.items():
            if ctx.links.get(capture_id) is None:
                continue
            link = CaptureLink(edge=edge, target_id=target, target_type=entity_type,
                               confidence=confidence, run_id=log.run_id)
            added: list[bool] = []
            ctx.links.mutate(capture_id, partial(
                _backlink, link=link, unresolved_type=cluster.entity_type, kind=cluster.kind,
                forms=frozenset(cluster.forms), added=added,
            ))
            changed.add(capture_id)
            if added:
                log.links_by_type[edge] += 1
                audit_rows.append({"run_id": log.run_id, "mode": mode, "action": "backlink",
                                   "capture_id": capture_id, "edge": edge, "target_id": target,
                                   "confidence": confidence})
    return changed


def _merge_run_links(current: CaptureLinkFile, *, run_record: CaptureLinkFile) -> CaptureLinkFile:
    run_record.links = current.owner_links() + [
        link for link in run_record.links if link.created_by != OWNER
    ]
    return run_record


def _add_link(record: CaptureLinkFile, link: CaptureLink) -> bool:
    """Append ``link`` unless the record already has that edge to that target;
    ABOUT is capped at MAX_ABOUT per document."""
    if any(existing.key() == link.key() for existing in record.links):
        return False
    if link.edge == ABOUT and sum(1 for e in record.links if e.edge == ABOUT) >= MAX_ABOUT:
        return False
    record.links.append(link)
    return True


def _backlink(record: CaptureLinkFile, *, link: CaptureLink, unresolved_type: str, kind: str,
              forms: frozenset[str], added: list[bool]) -> CaptureLinkFile:
    """The name is an entity now: drop it from the text list and link it."""
    record.unresolved = [
        u for u in record.unresolved
        if not (u.entity_type == unresolved_type and u.kind == kind and u.name in forms)
    ]
    if _add_link(record, link):
        added.append(True)
    return record


async def _admit(ctx: LinkContext, clusters: list[_Cluster], memories: dict[str, Any]):
    """Entity admission (ADR-006 §4) for corroborated names. Topics are not
    entities in the admission sense: the corroboration bar is their gate."""
    from app.services.entity_admission import LIBRARY_ORIGIN, judge_entities

    mentions = []
    for cluster in clusters:
        if cluster.kind == "topic":
            continue
        titles = []
        for capture_id in list(cluster.members)[:3]:
            memory = memories.get(capture_id) or ctx.captures.get(capture_id)
            if memory is not None:
                titles.append(capture_title(memory))
        mentions.append({"name": cluster.name, "type": cluster.entity_type,
                         "evidence": " | ".join(titles)})
    if not mentions:
        return {}
    try:
        return await judge_entities(
            mentions, ctx.domain_types, client=ctx.decision_client, origin=LIBRARY_ORIGIN
        )
    except Exception as e:  # judge_entities never raises; belt and braces
        logger.warning("[LINK] Entity admission unavailable, keeping: %s", e)
        return {}


async def _commit(ctx: LinkContext, paths: list[str], message: str) -> bool:
    if ctx.git is None or not paths:
        return False
    try:
        await ctx.git.commit_and_push(paths, message)
        return True
    except Exception as e:
        logger.warning("[LINK] Git commit failed (non-fatal): %s", e)
        return False


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


async def revert_run(run_id: str, ctx: LinkContext | None = None) -> dict[str, Any]:
    """Remove one run's links and archive the entities it created that
    nothing else references (ADR-011 §6). Owner links and captures stay."""
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run_id is required")
    if _run_lock.locked():
        raise LinkRunBusy("a link run is already in progress")
    async with _run_lock:
        ctx = ctx or default_context()
        return await _revert(ctx, run_id)


async def _revert(ctx: LinkContext, run_id: str) -> dict[str, Any]:
    changed: list[str] = []
    removed = 0
    projection_error: str | None = None

    def strip(record: CaptureLinkFile) -> CaptureLinkFile | None:
        nonlocal removed
        keep = [link for link in record.links
                if not (link.created_by == LINK_PHASE and link.run_id == run_id)]
        if len(keep) == len(record.links):
            return None
        removed += len(record.links) - len(keep)
        record.links = keep
        if run_id not in record.reverted_runs:
            record.reverted_runs.append(run_id)
        return record

    for record in list(ctx.links.iter_all()):
        if ctx.links.mutate(record.capture_id, strip) is not None:
            changed.append(record.capture_id)

    if ctx.graph is not None and changed:
        items = []
        for capture_id in changed:
            memory = ctx.captures.get(capture_id)
            record = ctx.links.get(capture_id)
            if memory is not None and record is not None:
                items.append((memory, record))
        try:
            await ctx.graph.write(items)
        except Exception as e:
            logger.warning("[LINK] Revert projection failed (files are written): %s", e, exc_info=True)
            projection_error = str(e)

    referenced = {link.target_id for record in ctx.links.iter_all() for link in record.links}
    archived, kept = [], []
    for node in list((getattr(ctx.kg, "nodes", None) or {}).values()):
        metadata = getattr(node, "metadata", None) or {}
        if metadata.get("run_id") != run_id or metadata.get("created_by") != LINK_PHASE:
            continue
        if node.id in referenced or await _has_other_edges(ctx.kg, node.id):
            kept.append(node.id)
            continue
        try:
            await ctx.entities.archive(node.id)
            archived.append(node.id)
        except Exception as e:
            logger.warning("[LINK] Archiving %s failed: %s", node.id, e)
            kept.append(node.id)

    summary = {"run_id": run_id, "captures_changed": len(changed), "links_removed": removed,
               "entities_archived": archived, "entities_kept": kept}
    if projection_error:
        summary["errors"] = [f"graph projection: {projection_error}"]
    ctx.audit.append_many([{"run_id": run_id, "action": "revert", **summary}])
    summary["committed"] = await _commit(
        ctx, [ctx.links.relative_path(c) for c in changed] + [ctx.audit.relative_path()],
        f"link: revert {run_id} ({removed} links)",
    )
    logger.info("[LINK] Reverted %s: %d links, archived %s", run_id, removed, archived)
    return summary


async def _has_other_edges(kg: Any, entity_id: str) -> bool:
    """Whether the entity has any edge besides its own profile mention: an
    owner edge, a signal, a relationship. Unknown counts as yes."""
    neo4j = getattr(kg, "neo4j", None)
    if neo4j is None:
        return False
    try:
        rows = await neo4j.execute_read(
            "MATCH (n:Entity {id: $id})-[r]-(o) "
            "WHERE NOT (type(r) = 'MENTIONED_IN' AND o.path = n.source_file) "
            "RETURN count(r) AS n",
            {"id": entity_id},
        )
        return bool(rows and rows[0]["n"])
    except Exception as e:
        logger.warning("[LINK] Edge check for %s failed, keeping it: %s", entity_id, e)
        return True


# ---------------------------------------------------------------------------
# Owner links (§5)
# ---------------------------------------------------------------------------

OWNER_EDGES = {"about": ABOUT, "mentions": MENTIONED_IN, "advances": ADVANCES,
               "evidence_for": EVIDENCE_FOR}


class AttachError(ValueError):
    pass


def resolve_target(ctx: LinkContext, target: str) -> tuple[str, str] | None:
    """(target_id, target_type) for an entity id or name, or a capture id.
    Names resolve on exact or alias matches only; an ambiguous name raises."""
    target = (target or "").strip()
    if not target:
        return None
    nodes = getattr(ctx.kg, "nodes", None) or {}
    node = nodes.get(target)
    if node is not None and getattr(node, "type", None) != "document":
        return node.id, node.type
    if ctx.captures.get(target) is not None:
        return target, CAPTURE_TARGET
    resolver = _resolver(ctx.kg)
    hits = []
    for entity_type in ctx.domain_types:
        hit = resolver.resolve(entity_type, target)
        if hit.matched_via in ("exact", "alias"):
            hits.append((hit.id, entity_type))
    if len(hits) > 1:
        raise AttachError(f"{target!r} matches several entities: {', '.join(i for i, _ in hits)}")
    return hits[0] if hits else None


def _pin_key(capture_id: str, edge: str, target_id: str) -> str:
    return f"{capture_id}:{edge}:{target_id}"


def _set_pin(ctx: LinkContext, capture_id: str, key: str, pinned: bool) -> str | None:
    """Add or remove one pin on a library capture. Returns its repo path when
    the capture file changed."""
    memory = ctx.captures.get(capture_id)
    if memory is None or memory.lane != "library":
        return None
    pins = list(memory.pinned_by)
    if pinned and key not in pins:
        pins.append(key)
    elif not pinned and key in pins:
        pins.remove(key)
    else:
        return None
    ctx.captures.update(memory.model_copy(update={"pinned_by": pins}))
    return ctx.captures.relative_path(capture_id)


async def attach(capture_id: str, target: str, edge: str = "about", stance: str | None = None,
                 actor: str | None = None, ctx: LinkContext | None = None) -> dict[str, Any]:
    """An owner link from a capture to an entity or another capture. Pins the
    library capture(s) it touches; never creates a node."""
    ctx = ctx or default_context()
    edge_type = OWNER_EDGES.get((edge or "").lower())
    if edge_type is None:
        raise AttachError(f"edge must be one of {sorted(OWNER_EDGES)}")
    memory = ctx.captures.get(capture_id)
    if memory is None:
        raise AttachError(f"Capture {capture_id!r} not found")
    resolved = resolve_target(ctx, target)
    if resolved is None:
        return {"linked": False, "unresolved": target}
    target_id, target_type = resolved
    if target_id == capture_id:
        raise AttachError("a capture cannot be linked to itself")
    if target_type == CAPTURE_TARGET and edge_type != ABOUT:
        raise AttachError("a capture can only be linked to another capture with 'about'")
    for role, role_edge in (("storyline", ADVANCES), ("essay", EVIDENCE_FOR)):
        if edge_type == role_edge and target_type != ctx.role_type(role):
            raise AttachError(f"'{edge}' links to a {ctx.role_type(role) or role} entity")
    if edge_type == EVIDENCE_FOR:
        stance = stance if stance in STANCES else "context"
    else:
        stance = None

    def add(record: CaptureLinkFile) -> CaptureLinkFile | None:
        if any(link.key() == (edge_type, target_id) and link.created_by == OWNER
               for link in record.links):
            return None
        record.links = [link for link in record.links if link.key() != (edge_type, target_id)]
        record.links.append(CaptureLink(edge=edge_type, target_id=target_id,
                                        target_type=target_type, created_by=OWNER,
                                        stance=stance))
        record.lane = memory.lane
        record.occurred_at = memory.source_date
        return record

    changed = ctx.links.mutate(capture_id, add) is not None
    pin = _pin_key(capture_id, edge_type, target_id)
    paths = [ctx.links.relative_path(capture_id)] if changed else []
    for pinned_id in {capture_id, target_id if target_type == CAPTURE_TARGET else capture_id}:
        path = _set_pin(ctx, pinned_id, pin, True)
        if path:
            paths.append(path)
    await _project_owner(ctx, capture_id, target_id if target_type == CAPTURE_TARGET else None)
    if paths:
        ctx.audit.append_many([{"action": "owner_link", "actor": actor, "capture_id": capture_id,
                                "edge": edge_type, "target_id": target_id,
                                **({"stance": stance} if stance else {})}])
        paths.append(ctx.audit.relative_path())
    committed = await _commit(ctx, paths, f"link: owner {edge_type} {capture_id} -> {target_id}")
    pinned = any(
        (m := ctx.captures.get(cid)) is not None and pin in m.pinned_by
        for cid in {capture_id, target_id if target_type == CAPTURE_TARGET else capture_id}
    )
    return {"linked": True, "created": changed, "capture_id": capture_id, "edge": edge_type,
            "target_id": target_id, "target_type": target_type, "pinned": pinned,
            "committed": committed}


async def detach(capture_id: str, target: str, edge: str = "about", actor: str | None = None,
                 ctx: LinkContext | None = None) -> dict[str, Any]:
    """Remove an owner link (never a linker one: revert the run for those)
    and the pin it held."""
    ctx = ctx or default_context()
    edge_type = OWNER_EDGES.get((edge or "").lower())
    if edge_type is None:
        raise AttachError(f"edge must be one of {sorted(OWNER_EDGES)}")
    resolved = resolve_target(ctx, target)
    target_id = resolved[0] if resolved else target
    target_type = resolved[1] if resolved else None

    def drop(record: CaptureLinkFile) -> CaptureLinkFile | None:
        keep = [link for link in record.links
                if not (link.key() == (edge_type, target_id) and link.created_by == OWNER)]
        if len(keep) == len(record.links):
            return None
        record.links = keep
        return record

    if ctx.links.get(capture_id) is None or ctx.links.mutate(capture_id, drop) is None:
        return {"removed": False, "capture_id": capture_id, "edge": edge_type, "target_id": target_id}
    pin = _pin_key(capture_id, edge_type, target_id)
    paths = [ctx.links.relative_path(capture_id)]
    for pinned_id in {capture_id, target_id if target_type == CAPTURE_TARGET else capture_id}:
        path = _set_pin(ctx, pinned_id, pin, False)
        if path:
            paths.append(path)
    await _project_owner(ctx, capture_id, None)
    ctx.audit.append_many([{"action": "owner_unlink", "actor": actor, "capture_id": capture_id,
                            "edge": edge_type, "target_id": target_id}])
    paths.append(ctx.audit.relative_path())
    committed = await _commit(ctx, paths, f"link: owner remove {edge_type} {capture_id} -> {target_id}")
    return {"removed": True, "capture_id": capture_id, "edge": edge_type, "target_id": target_id,
            "committed": committed}


async def _project_owner(ctx: LinkContext, capture_id: str, target_capture: str | None) -> None:
    if ctx.graph is None:
        return
    items = []
    if target_capture:
        target_memory = ctx.captures.get(target_capture)
        if target_memory is not None:
            items.append((target_memory, ctx.links.get(target_capture)
                          or CaptureLinkFile(capture_id=target_capture, mode="owner")))
    memory = ctx.captures.get(capture_id)
    record = ctx.links.get(capture_id)
    if memory is not None and record is not None:
        items.append((memory, record))
    try:
        if items:
            await ctx.graph.write(items)
    except Exception as e:
        logger.warning("[LINK] Projecting owner link failed (files are written): %s", e)


async def attach_about(capture_id: str, about: list[str], actor: str | None = None,
                       ctx: LinkContext | None = None) -> dict[str, Any]:
    """``capture_thought(about=...)``: ABOUT edges from a new note to each
    named entity or capture. Unresolved names are returned, never created."""
    ctx = ctx or default_context()
    linked, unresolved, errors = [], [], []
    for target in about:
        if not isinstance(target, str) or not target.strip():
            continue
        try:
            result = await attach(capture_id, target, "about", actor=actor, ctx=ctx)
        except AttachError as e:
            errors.append(f"{target}: {e}")
            continue
        if result.get("linked"):
            linked.append({"target_id": result["target_id"], "target_type": result["target_type"]})
        else:
            unresolved.append(target)
    return {"linked": linked, "unresolved": unresolved, **({"errors": errors} if errors else {})}
