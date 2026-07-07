"""Entity canonicalization and resolution.

Single place that decides whether a surface form ("Nation Swell", "Acme Corp",
"Exec Online") refers to an existing entity or warrants a new node. Used by
the graph write path (neo4j_graph.add_node) so duplicate accounts like
nation-swell/nationswell or heydrich/heydrick stop being minted, and by the
eval harness so eval matching and pipeline matching share one definition of
"the same name".

Resolution chain (never crosses entity types):
  1. exact   — the generated slug matches an existing node id
  2. alias   — normalized/tight form equals the node's name or a known alias
  3. fuzzy   — per-type SequenceMatcher threshold on normalized names
               (persons also get nickname/initial variation checks)
  4. new     — mint a fresh slug

Aliases are durable in entity markdown frontmatter (`aliases:` list) — entity
files are the registry that build_graph() re-ingests — and mirrored on the
Neo4j node's `aliases` property.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalization (shared with evals/harness/matching.py)
# ---------------------------------------------------------------------------

_PERSON_TITLES = {"mr", "mrs", "ms", "dr", "prof", "professor", "sir"}
_LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "llc",
    "ltd",
    "limited",
    "co",
    "company",
    "gmbh",
    "plc",
    "llp",
}

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Fuzzy thresholds per entity type. Projects are legitimately similar to each
# other ("Q3 migration" vs "Q4 migration"), so they get the strictest bar.
FUZZY_THRESHOLDS = {
    "person": 0.85,
    "account": 0.88,
    "company": 0.88,
    "team": 0.88,
    "project": 0.90,
}
_DEFAULT_FUZZY_THRESHOLD = 0.90

_NICKNAMES = {
    "robert": {"bob", "rob"},
    "william": {"bill", "will"},
    "james": {"jim", "jimmy"},
    "john": {"jack"},
    "richard": {"dick", "rick"},
    "michael": {"mike"},
    "elizabeth": {"liz", "beth"},
    "jennifer": {"jen", "jenny"},
    "patricia": {"pat", "patty"},
    "thomas": {"tom", "tommy"},
    "christopher": {"chris"},
    "katherine": {"kate", "katie", "kathy"},
    "daniel": {"dan", "danny"},
    "matthew": {"matt"},
    "steven": {"steve"},
    "stephen": {"steve"},
}


_LEADING_ARTICLES = {"the", "a", "an"}
# Generic type words stripped from the edges of team/project names when more
# than one word remains: "Apex Team" == "Apex", "the Atlas project" == "Atlas".
_TYPE_WORDS = {
    "team": {"team"},
    "project": {"project", "engagement", "initiative", "plan"},
}


def normalize_entity_name(name: str, entity_type: str = "") -> str:
    """Lowercase, strip punctuation, collapse whitespace; strip person titles
    for persons, legal suffixes ("Inc", "Corp", ...) and leading articles for
    everything else, and generic type words for teams/projects."""
    text = _PUNCT_RE.sub(" ", (name or "").lower())
    words = _WS_RE.sub(" ", text).strip().split()
    if entity_type == "person":
        words = [w for w in words if w not in _PERSON_TITLES]
    else:
        while len(words) > 1 and words[0] in _LEADING_ARTICLES:
            words = words[1:]
        while words and words[-1] in _LEGAL_SUFFIXES:
            words = words[:-1]
        type_words = _TYPE_WORDS.get(entity_type, set())
        while len(words) > 1 and words[-1] in type_words:
            words = words[:-1]
        while len(words) > 1 and words[0] in type_words:
            words = words[1:]
    return " ".join(words)


def tight_name(name: str, entity_type: str = "") -> str:
    """Normalized form with spaces removed: "Nation Swell" == "Nationswell"."""
    return normalize_entity_name(name, entity_type).replace(" ", "")


def surface_forms_equivalent(a: str, b: str, entity_type: str = "") -> bool:
    """True when two surface forms normalize to the same name."""
    na, nb = normalize_entity_name(a, entity_type), normalize_entity_name(b, entity_type)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return tight_name(a, entity_type) == tight_name(b, entity_type)


def make_slug(entity_type: str, name: str) -> str:
    """Canonical slug id from a surface form. Slugifies the NORMALIZED name so
    "Acme Corp" and "Acme" produce the same id (matches the historical
    add_node slug regex otherwise)."""
    normalized = normalize_entity_name(name, entity_type)
    slug = _SLUG_RE.sub("-", normalized).strip("-")
    return f"{entity_type}-{slug}" if slug else ""


def _digit_tokens(normalized: str) -> set[str]:
    return {w for w in normalized.split() if any(ch.isdigit() for ch in w)}


def _digit_token_conflict(a_norm: str, b_norm: str) -> bool:
    """Veto fuzzy merges between names whose digit-bearing tokens differ:
    'Q3 Migration' vs 'Q4 Migration', 'Phase 1' vs 'Phase 2'. A false merge
    is far more damaging than a duplicate node."""
    return _digit_tokens(a_norm) != _digit_tokens(b_norm)


def _person_name_variation(a: str, b: str) -> bool:
    """Nickname/initial variations: 'Bob Smith' ~ 'Robert Smith', 'J. Smith'
    ~ 'John Smith'. Same word count only (ported from EntityDeduplicator)."""
    parts_a = normalize_entity_name(a, "person").split()
    parts_b = normalize_entity_name(b, "person").split()
    if len(parts_a) != len(parts_b) or not parts_a:
        return False
    for pa, pb in zip(parts_a, parts_b, strict=False):
        if pa == pb:
            continue
        if len(pa) == 1 and pb.startswith(pa):
            continue
        if len(pb) == 1 and pa.startswith(pb):
            continue
        if pb in _NICKNAMES.get(pa, set()) or pa in _NICKNAMES.get(pb, set()):
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass
class ResolvedEntity:
    id: str
    canonical_name: str
    matched_via: str  # "exact" | "alias" | "fuzzy" | "new"
    score: float = 1.0


def resolve_against(
    entity_type: str,
    name: str,
    candidates: list[dict],
) -> ResolvedEntity:
    """Resolve a surface form against candidate entities of the SAME type.

    candidates: [{"id": ..., "name": ..., "aliases": [...]}]. Pure function —
    unit-testable without a graph. Candidates of other types must not be
    passed in (the caller filters; cross-type matching is forbidden).
    """
    name = (name or "").strip()
    slug = make_slug(entity_type, name)

    # Tier 1: slug identity
    for c in candidates:
        if c.get("id") == slug:
            return ResolvedEntity(
                id=c["id"], canonical_name=c.get("name", name), matched_via="exact"
            )

    # Tier 2: normalized name / alias equivalence
    for c in candidates:
        surfaces = [c.get("name", ""), *(c.get("aliases") or [])]
        if any(surface_forms_equivalent(name, s, entity_type) for s in surfaces):
            return ResolvedEntity(
                id=c["id"], canonical_name=c.get("name", name), matched_via="alias"
            )

    # Tier 3: fuzzy per-type threshold (+ person nickname variations)
    threshold = FUZZY_THRESHOLDS.get(entity_type, _DEFAULT_FUZZY_THRESHOLD)
    normalized = normalize_entity_name(name, entity_type)
    best: tuple[float, dict] | None = None
    for c in candidates:
        for s in [c.get("name", ""), *(c.get("aliases") or [])]:
            if not s:
                continue
            if entity_type == "person" and _person_name_variation(name, s):
                return ResolvedEntity(
                    id=c["id"],
                    canonical_name=c.get("name", name),
                    matched_via="fuzzy",
                    score=1.0,
                )
            s_norm = normalize_entity_name(s, entity_type)
            if _digit_token_conflict(normalized, s_norm):
                continue
            ratio = SequenceMatcher(None, normalized, s_norm).ratio()
            if ratio >= threshold and (best is None or ratio > best[0]):
                best = (ratio, c)
    if best is not None:
        score, c = best
        return ResolvedEntity(
            id=c["id"],
            canonical_name=c.get("name", name),
            matched_via="fuzzy",
            score=round(score, 3),
        )

    return ResolvedEntity(id=slug, canonical_name=name, matched_via="new")


class EntityResolver:
    """Graph-backed resolver. Builds same-type candidate lists from the
    knowledge graph's in-memory node cache (id, name, type, metadata.aliases)."""

    def __init__(self, knowledge_graph=None):
        self._kg = knowledge_graph

    def _candidates(self, entity_type: str) -> list[dict]:
        if self._kg is None or not getattr(self._kg, "nodes", None):
            return []
        candidates = []
        for node in self._kg.nodes.values():
            if getattr(node, "type", None) != entity_type:
                continue
            metadata = getattr(node, "metadata", None) or {}
            aliases = metadata.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            candidates.append(
                {"id": node.id, "name": getattr(node, "name", ""), "aliases": aliases}
            )
        return candidates

    def resolve(self, entity_type: str, name: str) -> ResolvedEntity:
        """Resolve a surface form against the current graph."""
        result = resolve_against(entity_type, name, self._candidates(entity_type))
        if result.matched_via != "new" and result.id != make_slug(entity_type, name):
            logger.info(
                "[RESOLVER] '%s' (%s) resolved to existing %s via %s (score %.3f)",
                name,
                entity_type,
                result.id,
                result.matched_via,
                result.score,
            )
        return result
