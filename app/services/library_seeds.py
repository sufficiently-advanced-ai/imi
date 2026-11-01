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


@dataclass
class Seed:
    name: str
    entity_id: str
    aliases: list[str] = field(default_factory=list)
    description: str | None = None


@dataclass
class SeedSyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.__dict__.items() if v}


def seed_path(repo_root: Path, role: str) -> Path:
    return Path(repo_root) / SEEDS_DIR / f"{role}.yaml"


def parse_seed_file(text: str, entity_type: str) -> list[Seed]:
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
        seeds[entity_id] = Seed(
            name=name,
            entity_id=entity_id,
            aliases=[a.strip() for a in aliases if isinstance(a, str) and a.strip()],
            description=description.strip()[:_DESCRIPTION_CHARS]
            if isinstance(description, str) and description.strip()
            else None,
        )
    return list(seeds.values())


def load_seeds(repo_root: Path, role: str, entity_type: str) -> list[Seed] | None:
    """The role's seeds, or None when it has no seed file."""
    path = seed_path(repo_root, role)
    if not path.is_file():
        return None
    try:
        return parse_seed_file(path.read_text(encoding="utf-8"), entity_type)
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
        entry = {"id": node.id, "name": node.name}
        description = metadata.get("description")
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()[:_DESCRIPTION_CHARS]
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
                await writer.create(
                    entity_type,
                    seed.name,
                    seed.entity_id,
                    {
                        "seed_status": CANONICAL,
                        "seed_file": seed_file,
                        "created_by": "seed_sync",
                        **({"description": seed.description} if seed.description else {}),
                    },
                    aliases=seed.aliases,
                )
                result.created.append(seed.entity_id)
                continue
            matched.add(node.id)
            metadata = getattr(node, "metadata", None) or {}
            changes: dict[str, Any] = {}
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
