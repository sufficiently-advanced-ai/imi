"""Decision view service — read projection over SignalStore (Issue #954, Task 2).

This is the single shared read path for all decision consumers: API routes,
MCP tools, constitution exporter, and audit artifact all call list_decisions.

Decisions are signals (type == "decision"); lifecycle states are computed by
decision_states.py and never stored.  Every function accepts an injectable
``store=`` parameter so tests can bypass the tenant-scoped module-level proxy.

Issue #909 (Sprint 2 Task 17): get_decision lineage now uses
build_chain_from_store from signal_lineage.py as the shared chain-walker,
keeping the same lineage entry shape (superset — adds valid_from/valid_to).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.services.decision_states import (
    DECISION_STATES,
    compute_decision_state,
    decision_age_days,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.signal import Signal
    from app.services.signal_store import SignalStore


def load_decision_signals(store: SignalStore | None = None) -> list[Signal]:
    """Return all signals with type == 'decision' across all meeting files.

    Args:
        store: SignalStore instance.  Defaults to the module-level tenant-scoped
               proxy (``signal_store``) when None.

    Returns:
        Flat list of Signal objects whose ``type`` is ``"decision"``.
    """
    if store is None:
        from app.services.signal_store import signal_store as _default_store

        store = _default_store  # type: ignore[assignment]

    results: list[Signal] = []
    for meeting_signals in store.load_all():
        for sig in meeting_signals.signals:
            if sig.type == "decision":
                results.append(sig)
    return results


def decision_to_view(signal: Signal, *, now: datetime | None = None) -> dict:
    """Flatten a decision Signal into a view dict suitable for API consumers.

    Args:
        signal: A Signal with ``type == "decision"``.
        now: Reference time for age/state computation (defaults to
             ``datetime.now(UTC)``).

    Returns:
        Flat dict with keys: id, content, state, state_reason, review_status,
        provenance_status, can_use_as_evidence, can_use_as_instruction, owner
        (display name or None), owner_id, client_id, source_meeting_id,
        source_meeting_title, source_timestamp, superseded_by, age_days,
        tenant_id, metadata.
    """
    if now is None:
        now = datetime.now(UTC)

    state, state_reason = compute_decision_state(signal, now=now)
    age = decision_age_days(signal, now=now)

    owner_name: str | None = None
    owner_id: str | None = None
    if signal.owner is not None:
        owner_name = signal.owner.name
        owner_id = signal.owner.id

    return {
        "id": signal.id,
        "content": signal.content,
        "state": state,
        "state_reason": state_reason,
        "review_status": signal.review_status,
        "provenance_status": signal.provenance_status,
        "can_use_as_evidence": signal.can_use_as_evidence,
        "can_use_as_instruction": signal.can_use_as_instruction,
        "owner": owner_name,
        "owner_id": owner_id,
        "client_id": signal.client_id,
        "source_meeting_id": signal.source_meeting_id,
        "source_meeting_title": signal.source_meeting_title,
        "source_timestamp": signal.source_timestamp,
        "superseded_by": signal.superseded_by,
        "age_days": age,
        "tenant_id": signal.tenant_id,
        "metadata": signal.metadata,
    }


def list_decisions(
    *,
    state: str | None = None,
    owner_id: str | None = None,
    client_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_results: int = 50,
    store: SignalStore | None = None,
    now: datetime | None = None,
) -> dict:
    """Return a paged, filtered view of all persisted decision signals.

    Args:
        state: Lifecycle state filter. Must be a member of DECISION_STATES if
               provided; raises ValueError otherwise. Reserved states that are
               never emitted (zombie, temporary, conflicting) are accepted but
               always return an empty list.
        owner_id: Filter by signal.owner.id (EntityRef slug).
        client_id: Filter by signal.client_id.
        date_from: ISO timestamp lower bound (inclusive) compared against
                   source_timestamp. Signals with unparseable timestamps are
                   skipped rather than raising.
        date_to: ISO timestamp upper bound (inclusive) compared against
                 source_timestamp. Same defensive behaviour.
        max_results: Maximum entries to include in ``decisions`` list.
                     ``total`` and ``counts_by_state`` always reflect the full
                     matching set before truncation.
        store: SignalStore instance. Defaults to the module-level tenant-scoped
               proxy when None.
        now: Reference time for state and age computation.

    Returns:
        Dict with keys:
            decisions: list of view dicts, newest source_timestamp first,
                       truncated to max_results.
            total: int — number of matching decisions before truncation.
            counts_by_state: dict[state_str, int] — counts of all matching
                             decisions per state, computed before truncation.

    Raises:
        ValueError: If ``state`` is not None and not a member of DECISION_STATES.
    """
    if state is not None and state not in DECISION_STATES:
        raise ValueError(
            f"Unknown decision state: {state!r}. Valid states: {', '.join(DECISION_STATES)}"
        )

    if now is None:
        now = datetime.now(UTC)

    # --- Parse date bounds defensively ---
    dt_from: datetime | None = None
    dt_to: datetime | None = None
    if date_from is not None:
        try:
            dt_from = _parse_iso(date_from)
        except (ValueError, TypeError):
            pass  # no lower bound if unparseable
    if date_to is not None:
        try:
            dt_to = _parse_iso(date_to)
        except (ValueError, TypeError):
            pass  # no upper bound if unparseable

    signals = load_decision_signals(store=store)

    # --- Sort newest source_timestamp first ---
    signals.sort(key=lambda s: s.source_timestamp, reverse=True)

    # --- Build views and apply filters ---
    matched: list[dict] = []
    counts: dict[str, int] = {}

    for sig in signals:
        view = decision_to_view(sig, now=now)
        sig_state = view["state"]

        # state filter
        if state is not None and sig_state != state:
            continue

        # owner_id filter
        if owner_id is not None and view["owner_id"] != owner_id:
            continue

        # client_id filter
        if client_id is not None and view["client_id"] != client_id:
            continue

        # date_from filter — skip signals with unparseable source_timestamp
        if dt_from is not None:
            ts = _try_parse_iso(view["source_timestamp"])
            if ts is None or ts < dt_from:
                continue

        # date_to filter — skip signals with unparseable source_timestamp
        if dt_to is not None:
            ts = _try_parse_iso(view["source_timestamp"])
            if ts is None or ts > dt_to:
                continue

        # Signal passed all filters — accumulate
        counts[sig_state] = counts.get(sig_state, 0) + 1
        matched.append(view)

    total = len(matched)
    return {
        "decisions": matched[:max_results],
        "total": total,
        "counts_by_state": counts,
    }


def compute_decision_stats(
    *,
    store: SignalStore | None = None,
    now: datetime | None = None,
) -> dict:
    """Compute aggregated decision statistics across all meetings and signals.

    Args:
        store: SignalStore instance. Defaults to the module-level tenant-scoped
               proxy when None.
        now: Reference time for state computation (defaults to
             ``datetime.now(UTC)``).

    Returns:
        Dict with keys:
            meetings: int — number of meeting files (len(store.load_all()))
            decisions: int — total number of decision-type signals
            counts_by_state: dict[state_str, int] — counts per emitted state
            stale: int — count of decisions in 'stale' state
            superseded: int — count of decisions in 'superseded' state
            headline: str — formatted summary, e.g.
                "Across {meetings} meetings: {decisions} decisions, {stale} stale, {superseded} superseded"
    """
    if now is None:
        now = datetime.now(UTC)

    if store is None:
        from app.services.signal_store import signal_store as _default_store

        store = _default_store  # type: ignore[assignment]

    # --- Single load_all scan: count meetings and extract decisions inline ---
    all_meeting_signals = store.load_all()
    meetings_count = len(all_meeting_signals)

    # Collect all decision-type signals in a single pass
    signals = [
        sig
        for ms in all_meeting_signals
        for sig in ms.signals
        if sig.type == "decision"
    ]
    decisions_count = len(signals)

    counts_by_state: dict[str, int] = {}
    stale_count = 0
    superseded_count = 0

    for sig in signals:
        state, _ = compute_decision_state(sig, now=now)
        counts_by_state[state] = counts_by_state.get(state, 0) + 1
        if state == "stale":
            stale_count += 1
        elif state == "superseded":
            superseded_count += 1

    headline = (
        f"Across {meetings_count} meetings: {decisions_count} decisions, "
        f"{stale_count} stale, {superseded_count} superseded"
    )

    return {
        "meetings": meetings_count,
        "decisions": decisions_count,
        "counts_by_state": counts_by_state,
        "stale": stale_count,
        "superseded": superseded_count,
        "headline": headline,
    }


def get_decision(
    decision_id: str,
    *,
    store: SignalStore | None = None,
    audit_store=None,
    now: datetime | None = None,
) -> dict | None:
    """Return a single decision view enriched with lineage, audit history, and governance ladder.

    Args:
        decision_id: The signal id to look up.
        store: SignalStore instance. Defaults to the module-level tenant-scoped proxy.
        audit_store: SignalAuditStore instance. Defaults to a SignalAuditStore()
                     constructed with default paths (mirroring chat_tools.py).
        now: Reference time for state and age computation.

    Returns:
        A dict extending decision_to_view(...) with three extra keys:

        lineage: ordered list of dicts — predecessors (ascending source_timestamp),
            then self, then successors via superseded_by hops. Each entry has:
            {id, content, state, source_timestamp, relation} where relation is
            one of 'predecessor', 'self', 'successor'.

        audit_history: list of audit rows for this signal — each a dict with keys
            {action, gate_response, actor, reasoning, created_at}. Empty list if
            unavailable or on any read error.

        governance_ladder: {
            position: 'instruction' | 'evidence' | 'blocked',
            provenance_status, review_status,
            can_use_as_evidence, can_use_as_instruction
        }

    Returns None if no decision with the given id exists.
    """
    if now is None:
        now = datetime.now(UTC)

    # --- Load all signals once and build an id→Signal index ---
    if store is None:
        from app.services.signal_store import signal_store as _default_store

        store = _default_store  # type: ignore[assignment]

    # Lineage must resolve arbitrary signal ids, so we need an id→signal index
    # over ALL signals, not the decision-filtered list from load_decision_signals.
    all_signals: dict[str, Signal] = {}
    for meeting_signals in store.load_all():
        for sig in meeting_signals.signals:
            all_signals[sig.id] = sig

    target = all_signals.get(decision_id)
    if target is None:
        return None

    # --- Build lineage via shared chain builder (Issue #909 Task 17) ---
    # Build reverse-lookup: id → list of signals whose superseded_by == id
    # (passed into build_chain_from_store as predecessors_of; these are the
    # OLDER signals — see signal_lineage.py for convention documentation).
    from app.services.graph.signal_lineage import build_chain_from_store

    predecessors_of: dict[str, list[Signal]] = {}
    for sig in all_signals.values():
        if sig.superseded_by:
            predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    # build_chain_from_store returns NEWEST→OLDEST; decision_view's established
    # API contract is OLDEST→NEWEST (predecessors ascending + self + successors).
    # Reverse the chain to preserve the existing contract while sharing the walker.
    # The entry shape is a superset of the previous _lineage_entry shape (adds
    # valid_from/valid_to — additive, not breaking).
    lineage = list(
        reversed(
            build_chain_from_store(
                decision_id,
                all_signals,
                predecessors_of,
                now=now,
            )
        )
    )

    # --- Audit history ---
    audit_history: list[dict] = []
    try:
        if audit_store is None:
            from app.services.signal_audit import SignalAuditStore

            audit_store = SignalAuditStore()
        records = audit_store.read_for_signal(decision_id)
        audit_history = [
            {
                "action": r.action,
                "gate_response": r.gate_response,
                "actor": r.actor,
                "reasoning": r.reasoning,
                "created_at": r.created_at,
            }
            for r in records
        ]
    except Exception:
        logger.warning(
            "[DECISION] audit read failed for %s; returning empty history",
            decision_id,
            exc_info=True,
        )

    # --- Governance ladder ---
    if target.can_use_as_instruction:
        position = "instruction"
    elif target.can_use_as_evidence:
        position = "evidence"
    else:
        position = "blocked"

    governance_ladder = {
        "position": position,
        "provenance_status": target.provenance_status,
        "review_status": target.review_status,
        "can_use_as_evidence": target.can_use_as_evidence,
        "can_use_as_instruction": target.can_use_as_instruction,
    }

    # --- Assemble final view ---
    view = decision_to_view(target, now=now)
    view["lineage"] = lineage
    view["audit_history"] = audit_history
    view["governance_ladder"] = governance_ladder
    return view


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp; assumes UTC for naive datetimes."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _try_parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp, returning None on any failure."""
    if ts is None:
        return None
    try:
        return _parse_iso(ts)
    except (ValueError, TypeError):
        return None
