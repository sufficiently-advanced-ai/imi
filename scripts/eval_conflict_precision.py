#!/usr/bin/env python3
"""Precision evaluation gate for the LLM conflict detector (S4-5).

Runs judge_conflict() over a labeled fixture of 16 decision pairs
and computes precision / recall / FP-rate.  Exits 1 if precision
falls below settings.CONFLICT_MIN_PRECISION.

Usage (inside container)
-------------------------
    python scripts/eval_conflict_precision.py
    python scripts/eval_conflict_precision.py --fixture /custom/path.json

Exit codes
----------
    0 — precision >= settings.CONFLICT_MIN_PRECISION
    1 — precision below gate, or fixture/runtime error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# sys.path bootstrap — run directly *or* via pytest importlib import
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default fixture path relative to repo root
_DEFAULT_FIXTURE = Path(_ROOT) / "tests" / "fixtures" / "conflict_eval_pairs.json"


# ---------------------------------------------------------------------------
# Public functions (importable for testing)
# ---------------------------------------------------------------------------


def load_pairs(path: str | Path) -> list[dict]:
    """Load and return the labeled evaluation pairs from *path*.

    Validates that each pair has the required keys.  Raises ValueError
    on malformed input.
    """
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as fh:
            pairs = json.load(fh)
    except FileNotFoundError as exc:
        raise ValueError(f"Fixture file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fixture is not valid JSON: {exc}") from exc

    if not isinstance(pairs, list):
        raise ValueError(f"Fixture must be a JSON array, got {type(pairs)}")

    required_top = {"id", "expected_contradiction", "a", "b"}
    required_side = {"content", "meeting_title", "speaker", "timestamp"}

    for i, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(
                f"Pair at index {i} must be a dict, got {type(pair).__name__}"
            )
        missing_top = required_top - set(pair)
        if missing_top:
            raise ValueError(
                f"Pair at index {i} missing keys: {sorted(missing_top)}"
            )
        for side in ("a", "b"):
            if not isinstance(pair[side], dict):
                raise ValueError(
                    f"Pair at index {i}, side '{side}' must be a dict, "
                    f"got {type(pair[side]).__name__}"
                )
            missing_side = required_side - set(pair[side])
            if missing_side:
                raise ValueError(
                    f"Pair at index {i}, side '{side}' missing keys: {sorted(missing_side)}"
                )

    return pairs


def pair_to_signals(pair: dict):
    """Convert a fixture pair to a tuple of (Signal_a, Signal_b).

    Synthetic Signals are type=decision, each with a distinct meeting id.
    owner is built from the speaker string.  entities is empty — the judge
    does not require entity overlap (that is the selector's job).
    """
    from app.models.signal import Signal, EntityRef

    def _make_signal(side: dict, pair_id: str, slot: str) -> "Signal":
        speaker = side["speaker"]
        # Deterministic but distinct meeting id per side
        meeting_id = f"eval-{pair_id}-{slot}"
        owner = EntityRef(
            id=f"person-{speaker.lower().replace(' ', '-')}",
            type="person",
            name=speaker,
        )
        return Signal(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{pair_id}-{slot}")),
            type="decision",
            content=side["content"],
            source_meeting_id=meeting_id,
            source_meeting_title=side["meeting_title"],
            source_timestamp=side["timestamp"],
            owner=owner,
            entities=[],
            review_status="confirmed",
            provenance_status="generated",
        )

    sig_a = _make_signal(pair["a"], pair["id"], "a")
    sig_b = _make_signal(pair["b"], pair["id"], "b")
    return sig_a, sig_b


async def evaluate(pairs: list[dict], client=None) -> dict:
    """Run judge_conflict over *pairs* and return metrics.

    If *client* is None, uses get_claude_client() (real LLM).

    Returns a dict with:
        precision, recall, fp_rate,
        tp, fp, tn, fn, parse_failures,
        results: list of per-pair dicts with id, expected, predicted,
                 confidence, rationale
    """
    from app.services.conflict_detector import judge_conflict
    from app.config import settings

    if client is None:
        from app.services.claude_client import get_claude_client

        client = get_claude_client()

    threshold = settings.CONFLICT_CONFIDENCE_THRESHOLD

    tp = fp = tn = fn = parse_failures = 0
    results = []

    for pair in pairs:
        sig_a, sig_b = pair_to_signals(pair)
        expected = bool(pair["expected_contradiction"])

        try:
            verdict = await judge_conflict(sig_a, sig_b, client)
        except Exception as exc:
            # Exception during judging counts as a parse/judge failure:
            # predicted=False so it does not inflate FP; evaluation continues
            # over remaining pairs so we always get metrics for the full set.
            logger.warning(
                "evaluate: judge_conflict raised for pair %s: %s",
                pair.get("id", "?"),
                exc,
            )
            parse_failures += 1
            predicted = False
            confidence = 0.0
            rationale = f"<exception: {exc}>"
            if expected and predicted:
                tp += 1
            elif not expected and predicted:
                fp += 1
            elif expected and not predicted:
                fn += 1
            else:
                tn += 1
            results.append(
                {
                    "id": pair["id"],
                    "expected": expected,
                    "predicted": predicted,
                    "confidence": confidence,
                    "rationale": rationale,
                }
            )
            continue

        if verdict is None:
            parse_failures += 1
            predicted = False
            confidence = 0.0
            rationale = "<parse failure>"
        else:
            predicted = bool(
                verdict.contradicts and verdict.confidence >= threshold
            )
            confidence = verdict.confidence
            rationale = verdict.rationale

        if expected and predicted:
            tp += 1
        elif not expected and predicted:
            fp += 1
        elif expected and not predicted:
            fn += 1
        else:
            tn += 1

        results.append(
            {
                "id": pair["id"],
                "expected": expected,
                "predicted": predicted,
                "confidence": confidence,
                "rationale": rationale,
            }
        )

    # Zero-division guards:
    # - precision = 1.0 when no positive predictions (no FP possible)
    # - recall    = 1.0 when no positives expected  (nothing to miss)
    predicted_positive = tp + fp
    actual_positive = tp + fn
    actual_negative = tn + fp

    precision = tp / predicted_positive if predicted_positive > 0 else 1.0
    recall = tp / actual_positive if actual_positive > 0 else 1.0
    fp_rate = fp / actual_negative if actual_negative > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "fp_rate": fp_rate,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "parse_failures": parse_failures,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the precision gate.  Returns exit code (0 = pass, 1 = fail)."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Evaluate conflict-detector precision over labeled pairs"
    )
    parser.add_argument(
        "--fixture",
        metavar="PATH",
        default=str(_DEFAULT_FIXTURE),
        help="Path to the labeled pairs JSON fixture",
    )
    args = parser.parse_args(argv)

    from app.config import settings

    try:
        pairs = load_pairs(args.fixture)
    except Exception as exc:
        print(f"ERROR: could not load fixture: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(pairs)} pairs from {args.fixture}")

    metrics = asyncio.run(evaluate(pairs))

    # Per-pair output
    print()
    print(f"{'ID':<10} {'EXP':<6} {'PRED':<6} {'CONF':>6}  RATIONALE")
    print("-" * 80)
    for r in metrics["results"]:
        mark = "OK" if r["expected"] == r["predicted"] else "MISS"
        print(
            f"{r['id']:<10} {str(r['expected']):<6} {str(r['predicted']):<6} "
            f"{r['confidence']:>6.2f}  [{mark}] {r['rationale'][:60]}"
        )

    # Summary table
    print()
    print("=" * 50)
    print(f"  TP={metrics['tp']}  FP={metrics['fp']}  TN={metrics['tn']}  FN={metrics['fn']}")
    print(f"  Parse failures: {metrics['parse_failures']}")
    print(f"  Precision : {metrics['precision']:.3f}  (gate: >= {settings.CONFLICT_MIN_PRECISION:.3f})")
    print(f"  Recall    : {metrics['recall']:.3f}")
    print(f"  FP rate   : {metrics['fp_rate']:.3f}")
    print("=" * 50)

    if metrics["precision"] >= settings.CONFLICT_MIN_PRECISION:
        print(f"PASS — precision {metrics['precision']:.3f} >= {settings.CONFLICT_MIN_PRECISION:.3f}")
        return 0
    else:
        print(
            f"FAIL — precision {metrics['precision']:.3f} < {settings.CONFLICT_MIN_PRECISION:.3f}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
