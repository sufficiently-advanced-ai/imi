"""Deterministic matching logic for eval scoring.

Entity matching: normalized-name + alias-table equality, with a fuzzy tier for
persons only (reuses the production EntityDeduplicator heuristics so the eval
agrees with the pipeline about what counts as "the same name").

Relationship matching: triples reduced to a canonical predicate vocabulary
(anchored to config/domains/consulting_firm.yaml), direction-aware after
inverse normalization, symmetric predicates compared as unordered pairs.

Signal matching: author-chosen keyword sets against extracted content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Name normalization
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


def normalize_name(name: str, entity_type: str = "") -> str:
    """Normalize an entity surface form for comparison.

    Lowercase, punctuation stripped, whitespace collapsed; person titles
    stripped for persons, legal suffixes stripped for everything else
    ("Acme Corp" -> "acme").
    """
    text = _PUNCT_RE.sub(" ", (name or "").lower())
    words = _WS_RE.sub(" ", text).strip().split()
    if entity_type == "person":
        words = [w for w in words if w not in _PERSON_TITLES]
    else:
        while words and words[-1] in _LEGAL_SUFFIXES:
            words = words[:-1]
    return " ".join(words)


def tight_form(name: str, entity_type: str = "") -> str:
    """Normalized form with all spaces removed ("north wind" -> "northwind")."""
    return normalize_name(name, entity_type).replace(" ", "")


def names_equivalent(a: str, b: str, entity_type: str = "") -> bool:
    """True when two surface forms normalize to the same name."""
    na, nb = normalize_name(a, entity_type), normalize_name(b, entity_type)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return tight_form(a, entity_type) == tight_form(b, entity_type)


def _person_fuzzy_match(a: str, b: str) -> bool:
    """Tier-3 person matching via the production deduplicator heuristics."""
    try:
        from app.services.entity_deduplication import EntityDeduplicator

        dedup = EntityDeduplicator(registry=None)
        return dedup._are_names_similar(a, b)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Entity matching & scoring
# ---------------------------------------------------------------------------


@dataclass
class EntityMatchResult:
    """Outcome of matching extracted entities against a fixture's gold labels."""

    matched: list[dict] = field(default_factory=list)  # {extracted, gold_id}
    duplicates: list[dict] = field(
        default_factory=list
    )  # extra emissions of a matched gold
    false_positives: list[dict] = field(default_factory=list)
    trap_hits: list[dict] = field(
        default_factory=list
    )  # subset of FPs matching forbidden_entities
    missed_required: list[dict] = field(default_factory=list)

    @property
    def unique_gold_matched(self) -> int:
        return len({m["gold_id"] for m in self.matched})


def match_entity(name: str, entity_type: str, gold_entities: list[dict]) -> dict | None:
    """Match one extracted (name, type) against gold entities of the same type."""
    candidates = [g for g in gold_entities if g.get("type") == entity_type]
    for g in candidates:
        surfaces = [g["canonical_name"], *g.get("aliases", [])]
        if any(names_equivalent(name, s, entity_type) for s in surfaces):
            return g
    if entity_type == "person":
        for g in candidates:
            surfaces = [g["canonical_name"], *g.get("aliases", [])]
            if any(_person_fuzzy_match(name, s) for s in surfaces):
                return g
    return None


def match_entities(
    extracted: list[dict],
    gold_entities: list[dict],
    forbidden_entities: list[dict],
) -> EntityMatchResult:
    """Match a full extraction (list of {name, type}) against gold + traps."""
    result = EntityMatchResult()
    seen_gold: set[str] = set()

    for item in extracted:
        name = (item.get("name") or "").strip()
        etype = (item.get("type") or "").strip()
        if not name:
            continue
        gold = match_entity(name, etype, gold_entities)
        if gold is not None:
            record = {"extracted": name, "type": etype, "gold_id": gold["canonical_id"]}
            if gold["canonical_id"] in seen_gold:
                result.duplicates.append(record)
            else:
                seen_gold.add(gold["canonical_id"])
            result.matched.append(record)
            continue

        fp = {"extracted": name, "type": etype}
        for trap in forbidden_entities:
            if trap.get("type") in (None, "", etype) and names_equivalent(
                name, trap["name"], etype
            ):
                fp["trap"] = trap.get("reason", trap["name"])
                result.trap_hits.append(fp)
                break
        result.false_positives.append(fp)

    for g in gold_entities:
        if g.get("required", True) and g["canonical_id"] not in seen_gold:
            result.missed_required.append(
                {"gold_id": g["canonical_id"], "name": g["canonical_name"]}
            )

    return result


# ---------------------------------------------------------------------------
# Relationship (triple) matching
# ---------------------------------------------------------------------------

# Canonical predicate vocabulary, anchored to config/domains/consulting_firm.yaml
# (plus reports_to from solo_consulting.yaml — being added to consulting_firm in
# the relationship-fix work). Map: surface predicate -> (canonical, invert).
PREDICATE_MAP: dict[str, tuple[str, bool]] = {
    "reports_to": ("reports_to", False),
    "manages": ("reports_to", True),
    "supervises": ("reports_to", True),
    "managed_by": ("managed_by", False),
    "manages_accounts": ("managed_by", True),
    "manages_account": ("managed_by", True),
    "works_on": ("works_on", False),
    "works_on_projects": ("works_on", False),
    "has_team_members": ("works_on", True),
    "assigned_to": ("works_on", False),
    "member_of_team": ("member_of_team", False),
    "member_of": ("member_of_team", False),
    "has_members": ("member_of_team", True),
    "collaborates_with": ("collaborates_with", False),
    "belongs_to_account": ("belongs_to_account", False),
    "has_projects": ("belongs_to_account", True),
    "discussed_topic": ("discussed_topic", False),
    # InferRelationshipsTool vocabulary (infer_relationships.py)
    "leads": ("leads", False),
    "owns": ("owns", False),
    "depends_on": ("depends_on", False),
    # NOTE: "related_to" is deliberately unmapped — it is the coerced junk
    # type the pipeline substitutes for unrecognized relationships, and the
    # eval counts it as an unknown-predicate FP on purpose.
}

_SYMMETRIC_PREDICATES = {"collaborates_with"}


def normalize_predicate(predicate: str) -> tuple[str, bool] | None:
    """Return (canonical_predicate, invert) or None for unknown predicates."""
    key = (predicate or "").strip().lower().replace(" ", "_").replace("-", "_")
    return PREDICATE_MAP.get(key)


def canonical_triple(subject: str, predicate: str, obj: str):
    """Reduce a triple to canonical comparable form, or None if the predicate
    is unknown. Symmetric predicates compare as unordered pairs."""
    norm = normalize_predicate(predicate)
    if norm is None:
        return None
    pred, invert = norm
    s, o = (obj, subject) if invert else (subject, obj)
    if pred in _SYMMETRIC_PREDICATES:
        return (pred, frozenset((s, o)))
    return (pred, s, o)


def _resolve_endpoint(name_or_id: str, gold_entities: list[dict]) -> str:
    """Resolve a predicted endpoint (surface name or slug id) to a gold
    canonical_id where possible; otherwise return a normalized fallback."""
    raw = (name_or_id or "").strip()
    for g in gold_entities:
        if raw == g["canonical_id"]:
            return g["canonical_id"]
        surfaces = [g["canonical_name"], *g.get("aliases", [])]
        if any(names_equivalent(raw, s, g.get("type", "")) for s in surfaces):
            return g["canonical_id"]
        # Slug-ish input: "person-stephen-cole" -> compare the name part
        if "-" in raw:
            name_part = raw.split("-", 1)[1].replace("-", " ")
            if any(names_equivalent(name_part, s, g.get("type", "")) for s in surfaces):
                return g["canonical_id"]
    return normalize_name(raw)


@dataclass
class TripleMatchResult:
    matched: list[dict] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)
    trap_hits: list[dict] = field(default_factory=list)
    missed_required: list[dict] = field(default_factory=list)
    unknown_predicates: list[str] = field(default_factory=list)


def match_relationships(
    predicted: list[dict],
    gold_relationships: list[dict],
    forbidden_relationships: list[dict],
    gold_entities: list[dict],
) -> TripleMatchResult:
    """Match predicted triples ({subject, predicate, object}) against gold."""
    result = TripleMatchResult()

    def build(rel: dict):
        # Resolve endpoints through the same path as predictions so gold ids,
        # surface names, and non-gold slugs all land in one comparison space.
        return canonical_triple(
            _resolve_endpoint(rel.get("subject", ""), gold_entities),
            rel.get("predicate", ""),
            _resolve_endpoint(rel.get("object", ""), gold_entities),
        )

    gold_index = {}
    for g in gold_relationships:
        key = build(g)
        if key is not None:
            gold_index[key] = g

    forbidden_index = {}
    for f in forbidden_relationships:
        key = build(f)
        if key is not None:
            forbidden_index[key] = f

    matched_keys: set = set()
    for p in predicted:
        norm = normalize_predicate(p.get("predicate", ""))
        if norm is None:
            result.unknown_predicates.append(str(p.get("predicate")))
            result.false_positives.append(dict(p))
            continue
        subject = _resolve_endpoint(p.get("subject", ""), gold_entities)
        obj = _resolve_endpoint(p.get("object", ""), gold_entities)
        key = canonical_triple(subject, p.get("predicate", ""), obj)
        if key in gold_index and key not in matched_keys:
            matched_keys.add(key)
            result.matched.append({"predicted": dict(p), "gold": gold_index[key]})
        elif key in forbidden_index:
            hit = {
                "predicted": dict(p),
                "reason": forbidden_index[key].get("reason", ""),
            }
            result.trap_hits.append(hit)
            result.false_positives.append(dict(p))
        else:
            result.false_positives.append(dict(p))

    for key, g in gold_index.items():
        if g.get("required", True) and key not in matched_keys:
            result.missed_required.append(dict(g))

    return result


# ---------------------------------------------------------------------------
# Signal matching
# ---------------------------------------------------------------------------

SIGNAL_TYPES = ("decision", "action_item", "key_point", "insight")


def _keywords_match(content: str, gold_signal: dict) -> bool:
    text = (content or "").lower()
    keywords_all = gold_signal.get("keywords_all") or []
    keywords_any = gold_signal.get("keywords_any") or []
    if not all(k.lower() in text for k in keywords_all):
        return False
    if keywords_any and not any(k.lower() in text for k in keywords_any):
        return False
    return True


@dataclass
class SignalMatchResult:
    matched: list[dict] = field(default_factory=list)  # correct type
    type_errors: list[dict] = field(default_factory=list)  # right concept, wrong type
    false_positives: list[dict] = field(default_factory=list)
    trap_hits: list[dict] = field(default_factory=list)
    missed_required: list[dict] = field(default_factory=list)
    # confusion[gold_type][predicted_type] over keyword-matched signals
    confusion: dict = field(default_factory=dict)

    def _bump_confusion(self, gold_type: str, predicted_type: str) -> None:
        row = self.confusion.setdefault(gold_type, {})
        row[predicted_type] = row.get(predicted_type, 0) + 1


def match_signals(
    predicted: list[dict],
    gold_signals: list[dict],
    forbidden_signals: list[dict],
) -> SignalMatchResult:
    """Greedy 1:1 matching of predicted signals against gold keyword patterns.

    A predicted signal that keyword-matches a gold signal of the same type is a
    match; same keywords but different type is a type_error (consumes the gold
    so it is not double-counted, tracked in the confusion matrix). Predictions
    matching a forbidden pattern are FPs and trap hits.
    """
    result = SignalMatchResult()
    consumed: set[int] = set()

    for p in predicted:
        ptype = (p.get("type") or "").strip().lower()
        content = p.get("content") or ""

        # Prefer a same-type gold match, then any-type keyword match.
        candidate_idx = None
        for same_type_first in (True, False):
            for i, g in enumerate(gold_signals):
                if i in consumed:
                    continue
                if same_type_first != (g.get("type") == ptype):
                    continue
                if _keywords_match(content, g):
                    candidate_idx = i
                    break
            if candidate_idx is not None:
                break

        if candidate_idx is not None:
            gold = gold_signals[candidate_idx]
            consumed.add(candidate_idx)
            record = {"predicted": dict(p), "gold_id": gold.get("gold_id")}
            result._bump_confusion(gold.get("type", "?"), ptype or "?")
            if gold.get("type") == ptype:
                result.matched.append(record)
            else:
                record["gold_type"] = gold.get("type")
                result.type_errors.append(record)
            continue

        fp = {"type": ptype, "content": content}
        for trap in forbidden_signals:
            trap_type = trap.get("type")
            if trap_type in (None, "", ptype) and _keywords_match(content, trap):
                fp["trap"] = trap.get("reason", "")
                result.trap_hits.append(fp)
                break
        result.false_positives.append(fp)

    for i, g in enumerate(gold_signals):
        if i not in consumed and g.get("required", True):
            result.missed_required.append(
                {"gold_id": g.get("gold_id"), "type": g.get("type")}
            )

    return result


# ---------------------------------------------------------------------------
# Summary keyword checks (deterministic MUST items)
# ---------------------------------------------------------------------------


def check_summary_keywords(text: str, gold_summary: dict) -> dict:
    """Deterministic keyword constraints for the summary task.

    Returns {"mention_failures": [...], "forbidden_hits": [...]}.
    """
    lowered = (text or "").lower()
    mention_failures = []
    for group in gold_summary.get("must_mention_keywords") or []:
        options = group if isinstance(group, list) else [group]
        if not any(opt.lower() in lowered for opt in options):
            mention_failures.append(options)
    forbidden_hits = [
        kw
        for kw in gold_summary.get("must_not_mention_keywords") or []
        if kw.lower() in lowered
    ]
    return {"mention_failures": mention_failures, "forbidden_hits": forbidden_hits}


def check_profile_keywords(text: str, gold_profiles: dict) -> dict:
    """Deterministic keyword constraints for the profiles task.

    Returns {"mention_failures": [...], "forbidden_attribution_hits": [...]}.

    - ``must_mention_keywords``: facts genuinely grounded to the subject; each
      any-of group must be satisfied.
    - ``forbidden_attribution_keywords``: terms tied to a *co-participant's*
      activity that must NOT appear on this subject's profile. A hit here is a
      misattribution — the bug this task exists to catch.
    """
    lowered = (text or "").lower()
    mention_failures = []
    for group in gold_profiles.get("must_mention_keywords") or []:
        options = group if isinstance(group, list) else [group]
        if not any(opt.lower() in lowered for opt in options):
            mention_failures.append(options)
    forbidden_attribution_hits = [
        kw
        for kw in gold_profiles.get("forbidden_attribution_keywords") or []
        if kw.lower() in lowered
    ]
    return {
        "mention_failures": mention_failures,
        "forbidden_attribution_hits": forbidden_attribution_hits,
    }


def check_title(title: str, gold_summary: dict) -> dict:
    """Deterministic title constraints. Returns failure dict (empty = pass)."""
    failures = {}
    words = (title or "").split()
    max_words = gold_summary.get("title_max_words")
    if max_words and len(words) > max_words:
        failures["too_long"] = {"words": len(words), "max": max_words}
    must_any = gold_summary.get("title_must_contain_any") or []
    lowered = (title or "").lower()
    if must_any and not any(t.lower() in lowered for t in must_any):
        failures["missing_topic"] = must_any
    if not (title or "").strip() or (title or "").strip().lower() in (
        "untitled meeting",
        "untitled",
    ):
        failures["untitled"] = True
    return failures
