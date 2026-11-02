"""Seeded library entities (ADR-011 §3, the seeded tier).

Topics, storylines and essays exist only because the owner says so: created by
hand, or synced from seed files the owner maintains in the corpus,
``seeds/<role>.yaml``. The linker selects from them and never invents one
(ADR-006 §3); the one exception is a provisional topic (``seed_status:
provisional``), which only the owner makes canonical by adding it to the seed
file.

A seed file is a YAML list (or a mapping holding one list), one item per seed:

    - name: AI agents                 # required (``title`` is accepted too)
      id: topic-ai-agents             # optional; default <type>-<slug of name>
      aliases: [agentic AI, ai-agents]
      description: Autonomous systems that plan and act with tools.

Some roles carry more fields (``ROLE_FIELDS``). A storyline seed may set
``thesis`` and ``stakes`` (the file owns them: synced every run),
``would_change`` and ``open_question`` (seeded only while the node's field is
empty; the storyline reviewer owns them after that) and ``status: closed``
(the owner's close; removing it reopens). A seeded storyline starts
``active``; an agent-proposed one (``status: proposed``) listed in the file is
adopted: canonical and active. See ``app/services/storylines.py``.

Sync is idempotent and runs at the start of every link run. A seed that
disappears from its file is marked ``seed_status: retired``: no longer offered
to the model, its links kept. A missing file changes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.services.entity_resolver import resolve_against
from app.services.entity_utils import slugify

logger = logging.getLogger(__name__)

SEEDS_DIR = "seeds"
CANONICAL = "canonical"
PROVISIONAL = "provisional"
RETIRED = "retired"
_DESCRIPTION_CHARS = 300


# Role-specific seed fields. owned: synced from the file every run.
# if_absent: written on create, or while the node's field is empty.
ROLE_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "storyline": {"owned": ("thesis", "stakes"), "if_absent": ("would_change", "open_question")},
}


@dataclass
class Seed:
    name: str
    entity_id: str
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    status: str | None = None


@dataclass
class SeedSyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Repo-relative files the sync wrote outside the graph mutation layer
    # (storyline history); the caller commits them. Not part of as_dict.
    paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.__dict__.items() if v and k != "paths"}


def seed_path(repo_root: Path, role: str) -> Path:
    return Path(repo_root) / SEEDS_DIR / f"{role}.yaml"


def _role_fields(role: str | None, entity_type: str, item: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """(role-specific fields, status) of one seed item; bad values are skipped
    with a warning so one typo never drops the seed."""
    spec = ROLE_FIELDS.get(role or "")
    if not spec:
        return {}, None
    from app.services import storylines

    out: dict[str, Any] = {}
    for name in (*spec["owned"], *spec["if_absent"]):
        if item.get(name) is None:
            continue
        try:
            value = (storylines.clean_stakes(item[name]) if name == "stakes"
                     else storylines.clean_text(name, item[name]))
        except storylines.StorylineError as e:
            logger.warning("[SEEDS] %s seed %r: %s", entity_type, item.get("name"), e)
            continue
        if value:
            out[name] = value
    status = item.get("status")
    if status is not None and status != "closed":
        logger.warning("[SEEDS] %s seed %r: only `status: closed` is read from a seed file",
                       entity_type, item.get("name"))
    return out, "closed" if status == "closed" else None


def parse_seed_file(text: str, entity_type: str, role: str | None = None) -> list[Seed]:
    """Seeds from one file's YAML text. Malformed items are skipped with a
    warning; a file that is not a list (or a mapping holding one) yields none."""
    data = yaml.safe_load(text) or []
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if len(lists) == 1 else []
    if not isinstance(data, list):
        logger.warning("[SEEDS] %s seed file must be a list of items; ignoring it", entity_type)
        return []
    seeds: dict[str, Seed] = {}
    for item in data:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            logger.warning("[SEEDS] Skipping %s seed %r: not a mapping", entity_type, item)
            continue
        name = item.get("name") or item.get("title")
        if not isinstance(name, str) or not name.strip():
            logger.warning("[SEEDS] Skipping %s seed without a name: %r", entity_type, item)
            continue
        name = name.strip()
        raw_id = item.get("id")
        entity_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
        if entity_id is None:
            slug = slugify(name)
            if not slug:
                continue
            entity_id = f"{entity_type}-{slug}"
        aliases = item.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        description = item.get("description")
        fields, status = _role_fields(role, entity_type, item)
        seeds[entity_id] = Seed(
            name=name,
            entity_id=entity_id,
            aliases=[a.strip() for a in aliases if isinstance(a, str) and a.strip()],
            description=description.strip()[:_DESCRIPTION_CHARS]
            if isinstance(description, str) and description.strip()
            else None,
            fields=fields,
            status=status,
        )
    return list(seeds.values())


def load_seeds(repo_root: Path, role: str, entity_type: str) -> list[Seed] | None:
    """The role's seeds, or None when it has no seed file."""
    path = seed_path(repo_root, role)
    if not path.is_file():
        return None
    try:
        return parse_seed_file(path.read_text(encoding="utf-8"), entity_type, role)
    except (OSError, yaml.YAMLError) as e:
        logger.warning("[SEEDS] Could not read %s: %s", path, e)
        return None


def nodes_of_type(kg: Any, entity_type: str) -> list[Any]:
    nodes = getattr(kg, "nodes", None) or {}
    return [n for n in list(nodes.values()) if getattr(n, "type", None) == entity_type]


def offered(kg: Any, entity_type: str) -> list[dict[str, Any]]:
    """The seeded set the model may select from: every node of the type that
    is not retired (an owner-created node with no seed status counts)."""
    out = []
    for node in nodes_of_type(kg, entity_type):
        metadata = getattr(node, "metadata", None) or {}
        if metadata.get("seed_status") == RETIRED or metadata.get("stub"):
            continue
        if metadata.get("status") == "closed":
            continue  # a closed storyline takes no new evidence
        entry = {"id": node.id, "name": node.name}
        description = metadata.get("description")
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()[:_DESCRIPTION_CHARS]
        thesis = metadata.get("thesis")
        if isinstance(thesis, str) and thesis.strip():
            entry["thesis"] = thesis.strip()[:_DESCRIPTION_CHARS]
        if metadata.get("seed_status") == PROVISIONAL:
            entry["provisional"] = True
        out.append(entry)
    return out


def _candidate(node: Any) -> dict[str, Any]:
    metadata = getattr(node, "metadata", None) or {}
    aliases = metadata.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {"id": node.id, "name": node.name, "aliases": aliases}


def _field_changes(role: str, metadata: dict[str, Any], seed: Seed) -> dict[str, Any]:
    """Role fields the sync writes onto an existing node: owned fields when
    they differ, if-absent fields only while the node's value is empty."""
    spec = ROLE_FIELDS.get(role)
    if not spec:
        return {}
    changes: dict[str, Any] = {}
    for name, value in seed.fields.items():
        current = metadata.get(name)
        if name in spec["owned"] and value != current:
            changes[name] = value
        elif name in spec["if_absent"] and current in (None, "", []):
            changes[name] = value
    return changes


def _seed_status(role: str, metadata: dict[str, Any], seed: Seed) -> tuple[dict[str, Any], str] | None:
    """(status changes, reason) the seed file implies for a storyline, or None.

    New seed: active (closed if the file says so). Listed agent proposal:
    adopted, active. ``status: closed`` closes; removing it reopens a storyline
    the file closed. Other statuses (dormant, the lifecycle's) are left alone."""
    if role != "storyline":
        return None
    from datetime import UTC, datetime

    current = metadata.get("status")
    now = datetime.now(UTC).isoformat()
    stamp = {"status_changed_at": now, "status_changed_by": "seed_sync"}
    if seed.status == "closed":
        if current == "closed":
            return None
        return {"status": "closed", "closed_by": "seed", **stamp}, "closed in the seed file"
    if current is None and not metadata:
        return {"status": "active", **stamp}, "seeded"
    if current in (None, "", "proposed"):
        return {"status": "active", **stamp}, "listed in the seed file"
    if current == "closed" and metadata.get("closed_by") == "seed":
        return {"status": "active", "closed_by": None, **stamp}, "reopened in the seed file"
    return None


def _log_status(repo_root: Path, result: SeedSyncResult, entity_id: str, status_from: str | None,
                status_to: str, reason: str) -> None:
    from app.services.storylines import append_history

    try:
        result.paths.append(append_history(repo_root, entity_id, {
            "event": "status", "actor": "seed_sync", "status_from": status_from,
            "status_to": status_to, "reason": reason,
        }))
    except OSError as e:
        logger.warning("[SEEDS] storyline history for %s not written: %s", entity_id, e)


async def sync_role(kg: Any, writer: Any, repo_root: Path, role: str, entity_type: str) -> SeedSyncResult:
    """Bring the graph's ``entity_type`` nodes in line with ``seeds/<role>.yaml``.

    ``writer`` is a ``LibraryEntityWriter`` (create/update with file
    write-through)."""
    result = SeedSyncResult()
    seeds = load_seeds(repo_root, role, entity_type)
    if seeds is None:
        return result
    existing = {n.id: n for n in nodes_of_type(kg, entity_type)}
    candidates = [_candidate(n) for n in existing.values()]
    matched: set[str] = set()
    seed_file = str(seed_path(Path("."), role))

    for seed in seeds:
        node = existing.get(seed.entity_id)
        if node is None:
            # An owner-created node or a provisional topic under another id.
            hit = resolve_against(entity_type, seed.name, candidates)
            if hit.matched_via in ("exact", "alias"):
                node = existing.get(hit.id)
        try:
            if node is None:
                status = _seed_status(role, {}, seed)
                await writer.create(
                    entity_type,
                    seed.name,
                    seed.entity_id,
                    {
                        "seed_status": CANONICAL,
                        "seed_file": seed_file,
                        "created_by": "seed_sync",
                        **({"description": seed.description} if seed.description else {}),
                        **seed.fields,
                        **(status[0] if status else {}),
                    },
                    aliases=seed.aliases,
                )
                result.created.append(seed.entity_id)
                if status:
                    _log_status(repo_root, result, seed.entity_id, None, status[0]["status"], status[1])
                continue
            matched.add(node.id)
            metadata = getattr(node, "metadata", None) or {}
            changes: dict[str, Any] = _field_changes(role, metadata, seed)
            status = _seed_status(role, metadata, seed)
            if status:
                changes.update(status[0])
            if metadata.get("seed_status") != CANONICAL:
                changes["seed_status"] = CANONICAL
                changes["seed_file"] = seed_file
            if seed.name != node.name:
                changes["name"] = seed.name
            if seed.description and seed.description != metadata.get("description"):
                changes["description"] = seed.description
            current_aliases = _candidate(node)["aliases"]
            if seed.aliases and sorted(seed.aliases) != sorted(current_aliases):
                changes["aliases"] = sorted(set(current_aliases) | set(seed.aliases))
            if changes:
                await writer.update(node.id, changes)
                result.updated.append(node.id)
                if status:
                    _log_status(repo_root, result, node.id, metadata.get("status"),
                                status[0]["status"], status[1])
        except Exception as e:  # one bad seed never stops the sync
            logger.warning("[SEEDS] %s seed %r failed: %s", role, seed.name, e)
            result.errors.append(f"{seed.entity_id}: {e}")

    for node_id, node in existing.items():
        metadata = getattr(node, "metadata", None) or {}
        if node_id in matched or metadata.get("seed_status") != CANONICAL:
            continue  # owner-created and provisional nodes are not the file's to retire
        try:
            await writer.update(node_id, {"seed_status": RETIRED})
            result.retired.append(node_id)
        except Exception as e:
            logger.warning("[SEEDS] Retiring %s failed: %s", node_id, e)
            result.errors.append(f"{node_id}: {e}")
    return result
