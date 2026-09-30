"""Supersession candidate matching — R2.4.

Given a newly ingested decision signal, scan the standing signal pool and
return the signals most likely to be superseded, ranked by entity overlap.

This module is deliberately **pure** (no I/O): the caller is responsible for
loading the standing pool from the SignalStore.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.models.signal import Signal
from app.utils.event_time import signal_event_time


def _non_person_entity_ids(signal: Signal) -> frozenset[str]:
    """Return the set of entity IDs linked to *signal*, excluding person entities.

    Person entities (ids that start with "person-") are excluded because meeting
    participants appear on almost every signal and would create spurious high-
    confidence matches that flood the candidate list.
    """
    return frozenset(e.id for e in signal.entities if not e.id.startswith("person-"))


def find_supersession_candidates(
    new_signal: Signal,
    standing: Iterable[Signal],
    *,
    max_candidates: int = 3,
) -> list[dict]:
    """Return signals from *standing* that *new_signal* may supersede.

    Rules
    -----
    * Returns [] immediately if *new_signal* is not a decision.
    * Only decision-type signals are considered in the standing pool.
    * Candidates are excluded when:
      - ``provenance_status == "superseded"``
      - ``review_status == "rejected"``
      - ``source_meeting_id`` equals *new_signal*'s (same meeting — intra-meeting
        supersession is handled at promotion time, not here)
      - ``id`` equals *new_signal*'s id (self-match guard)
      - its event time is later than *new_signal*'s (ADR-004 §6): a decision
        backfilled after a newer one cannot supersede it. Those pairs are
        returned, reversed, by ``find_superseding_candidates``.
    * Subject overlap is measured via Jaccard similarity over **non-person**
      entity ID sets.  Candidates with zero shared entities are excluded.
    * Results are sorted by confidence descending and capped at *max_candidates*.

    Parameters
    ----------
    new_signal:
        The newly ingested signal whose potential predecessors we want.
    standing:
        Iterable of persisted signals to scan (typically all signals from
        ``SignalStore.load_all()`` flattened).
    max_candidates:
        Maximum number of candidates to return.

    Returns
    -------
    list[dict]
        Each entry has the keys:
        ``old_signal_id``, ``old_content``, ``matched_entities`` (list of IDs),
        ``reason`` (human-readable string), ``confidence`` (0–1 float),
        ``status`` ("pending"), ``proposed_at`` (ISO timestamp string).
    """
    if new_signal.type != "decision":
        return []

    new_entities = _non_person_entity_ids(new_signal)
    new_time = signal_event_time(new_signal)

    candidates: list[dict] = []
    proposed_at = datetime.now(UTC).isoformat()

    for sig in standing:
        if _is_later(sig, new_time):
            continue
        # Must be a decision
        if sig.type != "decision":
            continue
        # Skip superseded / rejected / same meeting / self
        if sig.provenance_status == "superseded":
            continue
        if sig.review_status == "rejected":
            continue
        if (
            sig.source_meeting_id
            and new_signal.source_meeting_id
            and sig.source_meeting_id == new_signal.source_meeting_id
        ):
            continue
        if sig.id == new_signal.id:
            continue

        sig_entities = _non_person_entity_ids(sig)

        shared = new_entities & sig_entities
        if not shared:
            continue

        union = new_entities | sig_entities
        # union cannot be empty here because shared is non-empty
        confidence = round(len(shared) / len(union), 2)

        # Build human-readable reason from entity IDs.
        # IDs are slugs like "project-imi"; convert to readable names from
        # the entity refs when available, otherwise derive from the slug.
        id_to_name: dict[str, str] = {}
        for e in new_signal.entities:
            id_to_name[e.id] = e.name
        for e in sig.entities:
            id_to_name.setdefault(e.id, e.name)

        shared_names = sorted(id_to_name.get(eid, eid) for eid in shared)
        reason = "Shared entities: " + ", ".join(shared_names)

        candidates.append(
            {
                "old_signal_id": sig.id,
                "old_content": sig.content,
                "matched_entities": sorted(shared),
                "reason": reason,
                "confidence": confidence,
                "status": "pending",
                "proposed_at": proposed_at,
            }
        )

    # Sort by confidence descending, cap at max_candidates
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:max_candidates]


def _is_later(signal: Signal, than) -> bool:
    """True when *signal*'s event time is strictly after *than*. Unknown
    times never count as later, which keeps the pre-ADR-004 behaviour."""
    when = signal_event_time(signal)
    return when is not None and than is not None and when > than


def find_superseding_candidates(
    new_signal: Signal,
    standing: Iterable[Signal],
    *,
    max_candidates: int = 3,
) -> list[tuple[Signal, dict]]:
    """Standing decisions that may supersede *new_signal* (ADR-004 §6).

    The reverse of ``find_supersession_candidates``: *new_signal* was ingested
    late but happened earlier, so a standing decision with a later event time
    is the successor. Returns ``(newer_signal, candidate)`` pairs; each
    candidate belongs on the newer signal's metadata and names *new_signal*
    as the old one, so the review queue reads the same in both directions.
    """
    if new_signal.type != "decision":
        return []
    new_time = signal_event_time(new_signal)
    if new_time is None:
        return []

    pairs: list[tuple[Signal, dict]] = []
    for sig in standing:
        if not _is_later(sig, new_time):
            continue
        if sig.provenance_status == "superseded" or sig.review_status == "rejected":
            continue
        found = find_supersession_candidates(sig, [new_signal], max_candidates=1)
        if found:
            pairs.append((sig, {**found[0], "backfilled": True}))

    pairs.sort(key=lambda p: p[1]["confidence"], reverse=True)
    return pairs[:max_candidates]
