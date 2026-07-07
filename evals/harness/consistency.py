"""Deterministic, non-LLM consistency checks.

Nate's four-layer eval architecture ("Your AI Agent Knows the Answer. It
Recommends the Opposite.") calls for a layer of cheap, deterministic validation
that runs *outside* the model — if-then rules that catch reasoning/output
inconsistencies the probabilistic scorers miss, at near-zero marginal cost.

These functions are pure (no I/O, no API). Each returns a list of violation
dicts. Runners attach the list to ``TaskResult.details["consistency"]`` and a
scalar count to ``scores["consistency_violations"]``. The count is reported and
trended but is **not gated** (not in ``report.GATED_METRICS``) until it has been
calibrated against real runs — a brand-new heuristic with unknown false-positive
behavior must not block merges on noise.

Bias: these checks favor *low false positives* (a quiet signal you can trust)
over recall. Promote one to a gated metric only after watching its trend.
"""

from __future__ import annotations

import re

# Explicit decision language. A signal the model typed as a non-decision whose
# content contains one of these is a likely mis-type.
_DECISION_PHRASES = (
    "we'll go with",
    "we will go with",
    "we're going with",
    "we are going with",
    "we decided",
    "we've decided",
    "we have decided",
    "let's go with",
    "let's ship",
    "locking in",
    "lock it in",
    "final call",
    "signed off",
    "sign-off",
    "agreed to",
    "we agree to",
    "approved",
)

_NONDECISION_TYPES = ("opinion", "key_point", "insight")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def signal_decision_language_mismatch(predicted_signals: list[dict]) -> list[dict]:
    """Signals typed opinion/key_point/insight whose content uses explicit
    decision language — a likely decision mis-typed as something softer, which
    keyword-based recall gold may not catch."""
    out: list[dict] = []
    for s in predicted_signals or []:
        if _norm(s.get("type")) not in _NONDECISION_TYPES:
            continue
        content = _norm(s.get("content"))
        phrase = next((p for p in _DECISION_PHRASES if p in content), None)
        if phrase:
            out.append(
                {
                    "rule": "decision_language_in_nondecision",
                    "type": s.get("type"),
                    "content": s.get("content"),
                    "decision_phrase": phrase,
                }
            )
    return out


def relationship_endpoints_known(
    predicted_triples: list[dict], gold_entities: list[dict]
) -> list[dict]:
    """Predicted triples whose subject or object resolves to no known entity.

    The relationships runner is handed the gold entity set, so an endpoint
    outside that set is invented — a dangling/hallucinated triple, distinct from
    a wrong-predicate FP.
    """
    known: set[str] = set()
    for g in gold_entities or []:
        known.add(_norm(g.get("canonical_id")))
        known.add(_norm(g.get("canonical_name")))
        for alias in g.get("aliases") or []:
            known.add(_norm(alias))
    known.discard("")
    out: list[dict] = []
    for t in predicted_triples or []:
        for role in ("subject", "object"):
            val = _norm(t.get(role))
            if val and val not in known:
                out.append(
                    {
                        "rule": "relationship_endpoint_not_in_entity_set",
                        "triple": {
                            k: t.get(k) for k in ("subject", "predicate", "object")
                        },
                        "role": role,
                        "unknown_endpoint": t.get(role),
                    }
                )
    return out


# Common section labels that look like proper nouns but are not entities.
_HEADER_STOPWORDS = {
    "action items",
    "action item",
    "next steps",
    "next step",
    "key points",
    "key point",
    "meeting summary",
    "decisions made",
    "open questions",
    "follow up",
    "follow ups",
}

_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\b")


def summary_names_grounded(
    summary_text: str, gold_entities: list[dict], transcript: str
) -> list[dict]:
    """Multi-word capitalized names asserted in the summary whose tokens appear
    in neither the transcript nor the gold entities — a possible hallucinated
    name in the narrative.

    Intentionally conservative: a candidate is flagged only when *none* of its
    tokens occur anywhere in the transcript, which filters section headers and
    paraphrased real names and keeps the signal trustworthy.
    """
    hay = _norm(transcript)
    known: set[str] = set()
    for g in gold_entities or []:
        known.add(_norm(g.get("canonical_name")))
        for alias in g.get("aliases") or []:
            known.add(_norm(alias))
    known.discard("")

    out: list[dict] = []
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.findall(summary_text or ""):
        cand = _norm(match)
        if cand in seen or cand in _HEADER_STOPWORDS or cand in known:
            continue
        seen.add(cand)
        tokens = cand.split()
        # Skip if any token shows up in the transcript at all (conservative).
        if any(re.search(rf"\b{re.escape(tok)}\b", hay) for tok in tokens):
            continue
        out.append({"rule": "summary_name_not_in_transcript_or_gold", "name": match})
    return out
