"""Pure decision lifecycle state computation (Issue #954).

Computes the temporal lifecycle state of decision signals, orthogonal to the
governance axis (provenance_status, review_status) managed by signal_governance.py.

A decision's lifecycle progresses through states: candidate → active → stale,
superseded, rejected, conflicting, temporary, or zombie. States are computed
deterministically from the signal's fields — they are never stored, always derived.

State precedence (highest to lowest):
  1. superseded  — if superseded_by is set OR provenance_status == "superseded"
  2. rejected    — if review_status == "rejected"
  3. conflicting — if metadata.conflicts_with is a non-empty list (Sprint 4, R3.5)
  4. zombie      — if metadata.revisit_date is set AND the date is in the past
  5. temporary   — if metadata.revisit_date is set AND the date is in the future
  6. stale       — if review_status == "stale" (manual) OR age > STALE_AGE_DAYS
  7. active      — if review_status == "confirmed" AND not stale
  8. candidate   — everything else (default)

See:
  - docs/prd/decision-state-and-world-model-prd.md
  - app/models/signal.py (~line 66) for temporal-lifecycle comment
  - app/services/signal_governance.py for governance axis patterns
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.signal import Signal

# Age threshold (days) above which a confirmed decision becomes stale.
STALE_AGE_DAYS = 90

# Complete set of possible decision lifecycle states.
DECISION_STATES = (
    "candidate",
    "active",
    "stale",
    "superseded",
    "rejected",
    "temporary",
    "zombie",
    "conflicting",
)

# States that may be emitted in the public API and decision_view service.
# temporary and zombie added Sprint 3 (R2.3); conflicting added Sprint 4 (R3.5).
EMITTED_STATES = (
    "candidate",
    "active",
    "stale",
    "superseded",
    "rejected",
    "temporary",
    "zombie",
    "conflicting",
)


def decision_age_days(signal: Signal, *, now: datetime | None = None) -> int | None:
    """Compute the age of a decision signal in days.

    Parses signal.source_timestamp (ISO 8601 format) as the primary source of
    truth. Falls back to signal.created_at if source_timestamp is unparseable.
    Returns None if both timestamps fail to parse.

    Assumes UTC for timezone-naive timestamps (defensive handling).

    Args:
        signal: The signal to compute age for.
        now: Reference time (defaults to datetime.now(UTC) if None).

    Returns:
        Age in days, or None if both timestamps are unparseable.
        Can be negative for timestamps in the future.
    """
    if now is None:
        now = datetime.now(UTC)

    # Try primary source: source_timestamp
    age_days = _parse_and_age(signal.source_timestamp, now)
    if age_days is not None:
        return age_days

    # Fall back to created_at
    age_days = _parse_and_age(signal.created_at, now)
    return age_days


def _parse_and_age(timestamp_str: str, now: datetime) -> int | None:
    """Parse an ISO timestamp string and compute age in days.

    Returns None if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(timestamp_str)
        # Defensively assume UTC for naive timestamps
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = now - dt
        return delta.days
    except (ValueError, TypeError):
        return None


def compute_decision_state(
    signal: Signal,
    *,
    now: datetime | None = None,
    stale_age_days: int = STALE_AGE_DAYS,
) -> tuple[str, str]:
    """Compute the lifecycle state of a decision signal.

    Returns a tuple of (state, state_reason) where state is one of DECISION_STATES
    and state_reason is a short human-readable explanation of the state assignment.

    State precedence (applied in order):
      1. superseded  — if superseded_by is set OR provenance_status == "superseded"
         (merged with successor → superseded via higher precedence)
      2. rejected    — if review_status == "rejected"
      3. conflicting — if metadata.conflicts_with is a non-empty list (Sprint 4, R3.5)
      4. zombie      — if metadata.revisit_date is set AND date is in the past
      5. temporary   — if metadata.revisit_date is set AND date is in the future
      6. stale       — if review_status == "stale" (manual) OR age_days > stale_age_days
      7. active      — if review_status == "confirmed"
      8. candidate   — merged without successor (anomalous; flagged in reason) or default
         (merged with successor → superseded via precedence #1)

    Args:
        signal: The signal to evaluate.
        now: Reference time (defaults to datetime.now(UTC) if None).
        stale_age_days: Age threshold for stale determination (default 90).

    Returns:
        A tuple of (state: str, state_reason: str).
    """
    if now is None:
        now = datetime.now(UTC)

    # 1. Check superseded (highest precedence)
    if signal.superseded_by or signal.provenance_status == "superseded":
        reason = (
            f"superseded_by={signal.superseded_by}"
            if signal.superseded_by
            else "provenance_status=superseded"
        )
        return ("superseded", reason)

    # 2. Check rejected
    if signal.review_status == "rejected":
        return ("rejected", "review_status=rejected")

    # 3. Check conflicting — metadata.conflicts_with is a non-empty list (Sprint 4, R3.5)
    conflicts_with = signal.metadata.get("conflicts_with") if signal.metadata else None
    if conflicts_with:  # non-empty list only; empty list / None falls through
        ids = [str(cid) for cid in conflicts_with]
        short_ids = ", ".join(cid[:8] for cid in ids)
        return (
            "conflicting",
            f"conflicts with {len(ids)} decision(s): {short_ids}",
        )

    # 4. Check temporary / zombie via metadata.revisit_date
    #    Precedence: zombie > temporary > stale (both beat age-based staleness)
    revisit_str = signal.metadata.get("revisit_date") if signal.metadata else None
    if revisit_str:
        try:
            # Accept date-only (YYYY-MM-DD) or full ISO datetime
            if "T" not in revisit_str and " " not in revisit_str:
                revisit_dt = datetime.fromisoformat(revisit_str + "T00:00:00+00:00")
            else:
                revisit_dt = datetime.fromisoformat(revisit_str)
                if revisit_dt.tzinfo is None:
                    revisit_dt = revisit_dt.replace(tzinfo=UTC)
            if revisit_dt < now:
                return ("zombie", f"revisit_date {revisit_str} passed without action")
            else:
                return ("temporary", f"temporary until {revisit_str}")
        except (ValueError, TypeError):
            pass  # unparseable → fall through to existing ladder

    # 5. Check stale (manual or age-based)
    if signal.review_status == "stale":
        return ("stale", "review_status=stale (manual)")

    age_days = decision_age_days(signal, now=now)
    if age_days is not None and age_days > stale_age_days:
        return ("stale", f"age {age_days}d > {stale_age_days}d threshold")

    # 6. Check active
    if signal.review_status == "confirmed":
        return ("active", "review_status=confirmed")

    # 7. Explicit merged handling (anomalous: merged without successor)
    if signal.review_status == "merged":
        return ("candidate", "review_status=merged without successor pointer")

    # 8. Default to candidate
    return ("candidate", "pending review")
