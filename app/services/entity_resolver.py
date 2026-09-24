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

Optional decision-model tiebreak (``EntityResolver.prefetch``): when a
mention's outcome rests on string similarity alone — a fuzzy ratio match, or
"new" while near-miss candidates exist — a System One model (Jev) is asked a
single Choice question: which candidate, if any, is the same real-world
entity, given the mention's evidence and the candidates' roles/aliases. Its
answer overrides the heuristic only above ``TIEBREAK_MIN_PROBABILITY``
(``matched_via="decision"``). Controlled by the ``entity_resolution_tiebreak``
mode in config/inference.yaml (off | shadow | on); shadow logs the verdict
without acting. Any decision failure keeps the heuristic outcome.

Aliases are durable in entity markdown frontmatter (`aliases:` list) — entity
files are the registry that build_graph() re-ingests — and mirrored on the
Neo4j node's `aliases` property.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

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
    matched_via: str  # "exact" | "alias" | "fuzzy" | "decision" | "new"
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


# ---------------------------------------------------------------------------
# Decision-model tiebreak
# ---------------------------------------------------------------------------

TIEBREAK_OPERATION = "entity_resolution_tiebreak"
# A decision overrides the heuristic only when the model is sure enough, and
# the bar is asymmetric because a false merge is far more damaging than a
# duplicate: merging needs TIEBREAK_MIN_PROBABILITY, while undoing a
# similarity-only merge ("none" over a fuzzy match) needs only
# TIEBREAK_SPLIT_MIN_PROBABILITY. The digit-token veto still runs first,
# removing candidates before the model ever sees them.
TIEBREAK_MIN_PROBABILITY = 0.85
TIEBREAK_SPLIT_MIN_PROBABILITY = 0.60
# Candidates enter the fuzzy zone at this SequenceMatcher ratio (well below
# every per-type merge threshold), or via an acronym / shared distinctive word.
FUZZY_ZONE_FLOOR = 0.60
FUZZY_ZONE_MAX_CANDIDATES = 5
_NONE_OPTION = "none"
# Words too common in org/team/project names to make two names related.
_GENERIC_WORDS = {
    "group", "global", "holdings", "partners", "services", "solutions", "systems",
    "technologies", "technology", "international", "consulting", "digital",
    "management", "network", "labs", "health", "capital", "ventures", "media",
    "engineering", "operations", "platform", "customer", "product", "program",
}
_CANDIDATE_CONTEXT_KEYS = ("title", "role", "company", "department", "description")


def _initials(normalized: str) -> str:
    return "".join(w[0] for w in normalized.split())


def _acronym_of(a_norm: str, b_norm: str) -> bool:
    """'bcbs' ~ 'blue cross blue shield' and 'j j' (J&J) ~ 'johnson johnson',
    either direction."""
    for short, long in ((a_norm, b_norm), (b_norm, a_norm)):
        tokens = short.split()
        if len(tokens) > 1 and all(len(t) == 1 for t in tokens):
            short = "".join(tokens)
        if " " not in short and 2 <= len(short) <= 6 and len(long.split()) >= 2:
            if _initials(long) == short:
                return True
    return False


def _clipped_form(a_norm: str, b_norm: str) -> bool:
    """'infra' ~ 'infrastructure', 'data eng' ~ 'data engineering': same word
    count, every word of one is a prefix (3+ chars) of the other's."""
    a, b = a_norm.split(), b_norm.split()
    if len(a) != len(b) or a == b:
        return False
    return all(
        x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)))
        for x, y in zip(a, b, strict=True)
    )


def _distinctive_tokens(normalized: str) -> set[str]:
    return {w for w in normalized.split() if len(w) >= 4 and w not in _GENERIC_WORDS}


def fuzzy_zone(entity_type: str, name: str, candidates: list[dict]) -> list[tuple[float, dict]]:
    """Candidates too close for string similarity alone to rule out, best
    first: ratio >= FUZZY_ZONE_FLOOR, an acronym, a clipped form, or a shared
    distinctive word. The digit-token veto still applies — 'Q3 Migration'
    never reaches the model as a candidate for 'Q4 Migration'."""
    normalized = normalize_entity_name(name, entity_type)
    if not normalized:
        return []
    zone: dict[str, tuple[float, dict]] = {}
    for c in candidates:
        best, related = 0.0, False
        for s in [c.get("name", ""), *(c.get("aliases") or [])]:
            s_norm = normalize_entity_name(s, entity_type)
            if not s_norm or _digit_token_conflict(normalized, s_norm):
                continue
            ratio = SequenceMatcher(None, normalized, s_norm).ratio()
            best = max(best, ratio)
            if (
                ratio >= FUZZY_ZONE_FLOOR
                or _acronym_of(normalized, s_norm)
                or _clipped_form(normalized, s_norm)
                or _distinctive_tokens(normalized) & _distinctive_tokens(s_norm)
            ):
                related = True
        if related:
            zone[c["id"]] = (best, c)
    ranked = sorted(zone.values(), key=lambda t: t[0], reverse=True)
    return ranked[:FUZZY_ZONE_MAX_CANDIDATES]


def needs_tiebreak(result: ResolvedEntity) -> bool:
    """Only similarity-based outcomes are open to a decision. Slug identity,
    alias equivalence and rule-based person variations (score 1.0) are not."""
    if result.matched_via == "new":
        return True
    return result.matched_via == "fuzzy" and result.score < 1.0


def _describe(c: dict) -> str:
    parts = [c.get("name", "")]
    aliases = [a for a in (c.get("aliases") or []) if a]
    if aliases:
        parts.append(f"also known as {', '.join(aliases)}")
    ctx = c.get("context") or {}
    parts.extend(f"{k}: {v}" for k, v in ctx.items())
    return "; ".join(parts)


def build_tiebreak_question(entity_type: str, zone: list[tuple[float, dict]]):
    """(Choice, option_id -> candidate) for a mention and its fuzzy zone."""
    from app.services.inference.decisions import Choice

    options = {f"c{i}": c for i, (_, c) in enumerate(zone, start=1)}
    criteria = {key: _describe(c) for key, c in options.items()}
    criteria[_NONE_OPTION] = f"None of these: a different {entity_type} that is not in the list"
    question = Choice(
        instructions=(
            f"The mention is a {entity_type} name heard in a meeting transcript. Which existing "
            f"{entity_type} does it refer to? Choose a candidate only if it is the same real-world "
            f"{entity_type}: spelling or speech-to-text variants, nicknames, initials, "
            "abbreviations or acronyms, and legal-suffix differences count as the same. Different "
            f"{entity_type}s whose names are merely similar (a shared first name or surname, sibling "
            "projects, similarly named companies) are not the same; choose none. If the evidence "
            "cannot tell them apart, choose none."
        ),
        criteria=criteria,
    )
    return question, options


def build_tiebreak_state(mention: dict, options: dict[str, dict]) -> dict:
    m = {"name": mention["name"], "type": mention["type"]}
    for key in ("aliases_heard", "role", "evidence"):
        if mention.get(key):
            m[key] = mention[key]
    return {
        "mention": m,
        "candidates": {
            key: {
                "name": c.get("name", ""),
                **({"aliases": c["aliases"]} if c.get("aliases") else {}),
                **(c.get("context") or {}),
            }
            for key, c in options.items()
        },
    }


def apply_tiebreak(
    heuristic: ResolvedEntity,
    options: dict[str, dict],
    choice: str,
    probability: float,
    entity_type: str,
    name: str,
    min_probability: float = TIEBREAK_MIN_PROBABILITY,
    split_min_probability: float = TIEBREAK_SPLIT_MIN_PROBABILITY,
) -> ResolvedEntity:
    """The decision replaces the heuristic outcome only when confident enough
    for the direction it moves: splitting a fuzzy merge is the cheap error to
    risk, merging is the expensive one."""
    if choice == _NONE_OPTION:
        if heuristic.matched_via == "new" or probability < split_min_probability:
            return heuristic
        return ResolvedEntity(
            id=make_slug(entity_type, name), canonical_name=name, matched_via="new"
        )
    if probability < min_probability:
        return heuristic
    c = options.get(choice)
    if c is None:
        return heuristic
    return ResolvedEntity(
        id=c["id"],
        canonical_name=c.get("name", name),
        matched_via="decision",
        score=round(probability, 3),
    )


def _default_decision_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(TIEBREAK_OPERATION) != "off" else None
    except Exception as e:  # config errors must not break resolution
        logger.warning("[RESOLVER] Decision tiebreak unavailable: %s", e)
        return None


_UNSET: Any = object()


class EntityResolver:
    """Graph-backed resolver. Builds same-type candidate lists from the
    knowledge graph's in-memory node cache (id, name, type, metadata.aliases).

    ``resolve`` stays synchronous and pure-heuristic unless ``prefetch`` has
    run: prefetch asks the decision model about every fuzzy-zone mention in
    one concurrent batch and caches the outcomes ``resolve`` then returns."""

    def __init__(self, knowledge_graph=None, decisions=_UNSET):
        self._kg = knowledge_graph
        self._decisions = _default_decision_client() if decisions is _UNSET else decisions
        self._decided: dict[tuple[str, str], ResolvedEntity] = {}

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
            context = {
                k: str(metadata[k])[:200]
                for k in _CANDIDATE_CONTEXT_KEYS
                if isinstance(metadata.get(k), str | int | float) and str(metadata[k]).strip()
            }
            candidates.append(
                {
                    "id": node.id,
                    "name": getattr(node, "name", ""),
                    "aliases": aliases,
                    "context": context,
                }
            )
        return candidates

    async def prefetch(self, mentions: list[dict]) -> int:
        """Run the decision tiebreak for every mention that needs one.

        mentions: [{"type", "name", optional "evidence", "role",
        "aliases_heard"}]. Never raises; returns the number of decisions that
        will change what ``resolve`` returns (always 0 in shadow mode)."""
        client = self._decisions
        if client is None:
            return 0
        mode = client.mode(TIEBREAK_OPERATION)
        if mode == "off":
            return 0
        jobs = []
        seen: set[tuple[str, str]] = set()
        by_type: dict[str, list[dict]] = {}
        for m in mentions:
            etype, name = (m.get("type") or "").strip(), (m.get("name") or "").strip()
            key = (etype, name)
            if not etype or not name or key in seen or key in self._decided:
                continue
            seen.add(key)
            if etype not in by_type:
                by_type[etype] = self._candidates(etype)
            candidates = by_type[etype]
            heuristic = resolve_against(etype, name, candidates)
            if not needs_tiebreak(heuristic):
                continue
            zone = fuzzy_zone(etype, name, candidates)
            if not zone:
                continue
            jobs.append(self._tiebreak(client, mode, {**m, "type": etype, "name": name}, heuristic, zone))
        if not jobs:
            return 0
        changed = await asyncio.gather(*jobs)
        return sum(changed)

    async def _tiebreak(self, client, mode: str, mention: dict, heuristic: ResolvedEntity, zone) -> bool:
        from app.services.inference.decisions import DecisionUnavailable

        etype, name = mention["type"], mention["name"]
        question, options = build_tiebreak_question(etype, zone)
        state = build_tiebreak_state(mention, options)
        try:
            result = await client.decide(state, {"match": question}, operation=TIEBREAK_OPERATION)
        except (DecisionUnavailable, ValueError) as e:
            logger.warning("[RESOLVER] Tiebreak failed for %s/%r, keeping heuristic: %s", etype, name, e)
            return False
        answer = result.choice("match")
        probability = answer.probabilities.get(answer.choice, 0.0)
        decided = apply_tiebreak(heuristic, options, answer.choice, probability, etype, name)
        differs = decided.id != heuristic.id
        logger.info(
            "[RESOLVER] tiebreak %s %s/%r: heuristic=%s(%s) decision=%s p=%.2f -> %s%s",
            mode, etype, name, heuristic.id, heuristic.matched_via,
            options[answer.choice]["id"] if answer.choice in options else answer.choice,
            probability, decided.id, "" if differs else " (same)",
        )
        if mode == "on" and differs:
            self._decided[(etype, name)] = decided
            return True
        return False

    def resolve(self, entity_type: str, name: str) -> ResolvedEntity:
        """Resolve a surface form against the current graph."""
        decided = self._decided.get((entity_type, (name or "").strip()))
        if decided is not None:
            return decided
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
