"""Metric computation over match results.

Zero-division conventions follow scripts/eval_conflict_precision.py:
precision = 1.0 when there are no positive predictions (no FP possible),
recall = 1.0 when nothing was required (nothing to miss).
"""

from __future__ import annotations

from .matching import (
    EntityMatchResult,
    SignalMatchResult,
    TripleMatchResult,
)


def precision_recall(tp: int, fp: int, fn: int) -> dict:
    predicted_positive = tp + fp
    actual_positive = tp + fn
    return {
        "precision": tp / predicted_positive if predicted_positive else 1.0,
        "recall": tp / actual_positive if actual_positive else 1.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def score_entities(match: EntityMatchResult) -> dict:
    """Entity metrics: P/R over gold matches, plus canonicalization rate
    (1.0 = no duplicate emissions of the same gold entity) and trap hits."""
    tp = match.unique_gold_matched
    scores = precision_recall(
        tp=tp,
        fp=len(match.false_positives),
        fn=len(match.missed_required),
    )
    total_gold_mapped = len(match.matched)  # includes duplicate emissions
    scores["canonicalization_rate"] = (
        match.unique_gold_matched / total_gold_mapped if total_gold_mapped else 1.0
    )
    scores["duplicate_emissions"] = len(match.duplicates)
    scores["trap_hits"] = len(match.trap_hits)
    return scores


def score_relationships(match: TripleMatchResult) -> dict:
    scores = precision_recall(
        tp=len(match.matched),
        fp=len(match.false_positives),
        fn=len(match.missed_required),
    )
    scores["trap_hits"] = len(match.trap_hits)
    scores["unknown_predicates"] = len(match.unknown_predicates)
    return scores


def score_signals(match: SignalMatchResult) -> dict:
    """Per-type and micro-averaged P/R plus type confusion and trap hits.

    type_errors count as FP for the predicted type and FN for the gold type
    (the concept was found but mislabeled — both sides of the confusion shown
    in the confusion matrix).
    """
    types = ("decision", "action_item", "key_point", "insight")
    per_type = {}
    for t in types:
        tp = sum(1 for m in match.matched if m["predicted"].get("type") == t)
        fp = sum(1 for f in match.false_positives if f.get("type") == t)
        fp += sum(1 for e in match.type_errors if e["predicted"].get("type") == t)
        fn = sum(1 for m in match.missed_required if m.get("type") == t)
        fn += sum(1 for e in match.type_errors if e.get("gold_type") == t)
        per_type[t] = precision_recall(tp, fp, fn)

    micro = precision_recall(
        tp=len(match.matched),
        fp=len(match.false_positives) + len(match.type_errors),
        fn=len(match.missed_required) + len(match.type_errors),
    )
    return {
        **micro,
        "per_type": per_type,
        "type_errors": len(match.type_errors),
        "trap_hits": len(match.trap_hits),
        "confusion": match.confusion,
    }


def aggregate_micro(per_fixture: dict[str, dict]) -> dict:
    """Micro-average P/R across fixtures by summing tp/fp/fn counts.

    Count-style extras (trap_hits, duplicate_emissions, type_errors) are
    summed; rate-style extras (canonicalization_rate) are averaged.
    """
    tp = sum(s.get("tp", 0) for s in per_fixture.values())
    fp = sum(s.get("fp", 0) for s in per_fixture.values())
    fn = sum(s.get("fn", 0) for s in per_fixture.values())
    out = precision_recall(tp, fp, fn)
    for count_key in (
        "trap_hits",
        "duplicate_emissions",
        "type_errors",
        "unknown_predicates",
        "consistency_violations",
    ):
        if any(count_key in s for s in per_fixture.values()):
            out[count_key] = sum(s.get(count_key, 0) for s in per_fixture.values())
    rates = [
        s["canonicalization_rate"]
        for s in per_fixture.values()
        if "canonicalization_rate" in s
    ]
    if rates:
        out["canonicalization_rate"] = sum(rates) / len(rates)
    return out
