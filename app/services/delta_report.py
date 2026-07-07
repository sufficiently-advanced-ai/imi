"""Delta report — "what your brain learned" artifact (Task 19).

Builds a structured summary of what was extracted during a single ingestion:
  - New decisions
  - Proposed supersessions (with one-click confirm links to Task 18 API)
  - Commitments opened / closed
  - Entities touched

The builder is pure (no I/O, deterministic with injected generated_at). The
renderer converts the model to a markdown artifact committed to
deltas/delta-{bot_id}.md by the DELTA_REPORT orchestrator phase.

Task 20 wires SSE — the phase stores the report object on self so SSE emitters
can access it without re-running the build.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from .artifact_markdown import inline_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeltaItem(BaseModel):
    """A single signal surfaced in the delta report."""

    signal_id: str
    content: str
    entities: list[str] = []
    owner: str | None = None
    due_date: str | None = None


class SupersessionProposal(BaseModel):
    """A proposed supersession lifted from a decision's metadata."""

    new_signal_id: str
    old_signal_id: str
    old_content: str
    reason: str
    confidence: float
    status: str


class ConflictCandidate(BaseModel):
    """A potential semantic conflict lifted from a decision's metadata."""

    new_signal_id: str
    other_signal_id: str
    other_content: str
    rationale: str
    confidence: float
    status: str


class DeltaReport(BaseModel):
    """Full delta summary for one ingestion job."""

    job_id: str
    bot_id: str
    meeting_title: str | None
    generated_at: str  # ISO-8601 string; injected by caller for determinism

    new_decisions: list[DeltaItem]
    proposed_supersessions: list[SupersessionProposal]
    potential_conflicts: list[ConflictCandidate] = []
    commitments_opened: list[DeltaItem]
    commitments_closed: list[DeltaItem]
    entities_touched: list[dict]  # {id, name, type} deduped by id
    counts: dict[str, int]  # one entry per section


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_delta_report(
    job_id: str,
    bot_id: str,
    meeting_title: str | None,
    meeting_signals,  # MeetingSignals | None
    *,
    generated_at: str,
) -> DeltaReport:
    """Build a DeltaReport from a completed MeetingSignals batch.

    Parameters
    ----------
    job_id:
        Ingestion job identifier.
    bot_id:
        Bot / meeting identifier (used for filenames and links).
    meeting_title:
        Human-readable title of the ingested meeting; may be None.
    meeting_signals:
        MeetingSignals produced by PROMOTE_SIGNALS, or None when promotion
        produced no output.
    generated_at:
        ISO-8601 timestamp injected by the caller (keeps the builder
        deterministic and easy to test without time mocking).
    """
    new_decisions: list[DeltaItem] = []
    proposed_supersessions: list[SupersessionProposal] = []
    potential_conflicts: list[ConflictCandidate] = []
    commitments_opened: list[DeltaItem] = []
    commitments_closed: list[DeltaItem] = []
    entities_by_id: dict[str, dict] = {}

    signals = meeting_signals.signals if meeting_signals else []

    for sig in signals:
        # Collect entities (deduplicated by id)
        for ent in sig.entities:
            if ent.id not in entities_by_id:
                entities_by_id[ent.id] = {
                    "id": ent.id,
                    "name": ent.name,
                    "type": ent.type,
                }

        entity_names = [e.name for e in sig.entities]

        if sig.type == "decision":
            new_decisions.append(
                DeltaItem(
                    signal_id=sig.id,
                    content=sig.content,
                    entities=entity_names,
                )
            )
            # Lift supersession candidates stored by DETECT_SUPERSESSION phase
            candidates = sig.metadata.get("supersession_candidates", [])
            for cand in candidates:
                proposed_supersessions.append(
                    SupersessionProposal(
                        new_signal_id=sig.id,
                        old_signal_id=cand["old_signal_id"],
                        old_content=cand["old_content"],
                        reason=cand.get("reason", ""),
                        confidence=cand.get("confidence", 0.0),
                        status=cand.get("status", "pending"),
                    )
                )
            # Lift conflict candidates stored by DETECT_CONFLICTS phase.
            # Validate each entry: malformed entries are skipped with a warning
            # so a single bad entry never aborts the report.
            conflict_cands = sig.metadata.get("conflict_candidates", [])
            for cc in conflict_cands:
                # Required: other_signal_id and other_content must be present strings
                other_signal_id = (
                    cc.get("other_signal_id") if isinstance(cc, dict) else None
                )
                other_content = (
                    cc.get("other_content") if isinstance(cc, dict) else None
                )
                if not isinstance(other_signal_id, str) or not isinstance(
                    other_content, str
                ):
                    logger.warning(
                        "build_delta_report: skipping malformed conflict_candidate "
                        "for signal %s — missing/invalid other_signal_id or other_content: %r",
                        sig.id,
                        cc,
                    )
                    continue
                # Optional: confidence must be numeric (default 0.0), rationale str (default "")
                raw_confidence = cc.get("confidence", 0.0)
                confidence = (
                    float(raw_confidence)
                    if isinstance(raw_confidence, (int, float))
                    else 0.0
                )
                rationale = cc.get("rationale", "")
                if not isinstance(rationale, str):
                    rationale = ""
                potential_conflicts.append(
                    ConflictCandidate(
                        new_signal_id=sig.id,
                        other_signal_id=other_signal_id,
                        other_content=other_content,
                        rationale=rationale,
                        confidence=confidence,
                        status=cc.get("status", "pending"),
                    )
                )

        elif sig.type == "action_item":
            owner_name = sig.owner.name if sig.owner else None
            # Include the owner entity in entities_touched when not already present
            # via sig.entities (owner is stored as a separate EntityRef field).
            if sig.owner and sig.owner.id not in entities_by_id:
                entities_by_id[sig.owner.id] = {
                    "id": sig.owner.id,
                    "name": sig.owner.name,
                    "type": sig.owner.type,
                }
            item = DeltaItem(
                signal_id=sig.id,
                content=sig.content,
                entities=entity_names,
                owner=owner_name,
                due_date=sig.due_date,
            )
            # status == "done" → closed; anything else (open, in_progress, None) → opened
            if sig.status == "done":
                commitments_closed.append(item)
            else:
                commitments_opened.append(item)

    entities_touched = list(entities_by_id.values())

    counts = {
        "new_decisions": len(new_decisions),
        "proposed_supersessions": len(proposed_supersessions),
        "potential_conflicts": len(potential_conflicts),
        "commitments_opened": len(commitments_opened),
        "commitments_closed": len(commitments_closed),
        "entities_touched": len(entities_touched),
    }

    return DeltaReport(
        job_id=job_id,
        bot_id=bot_id,
        meeting_title=meeting_title,
        generated_at=generated_at,
        new_decisions=new_decisions,
        proposed_supersessions=proposed_supersessions,
        potential_conflicts=potential_conflicts,
        commitments_opened=commitments_opened,
        commitments_closed=commitments_closed,
        entities_touched=entities_touched,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_delta_markdown(report: DeltaReport) -> str:
    """Render a DeltaReport as a markdown string.

    Produces the "What your brain learned" artifact committed to git under
    deltas/delta-{bot_id}.md.
    """
    # Date portion from generated_at (ISO string, take first 10 chars)
    date_part = report.generated_at[:10] if report.generated_at else "unknown"
    title = report.meeting_title or report.bot_id
    lines: list[str] = [
        f"# What your brain learned — {title} ({date_part})",
        "",
        f"_Job: {report.job_id} · Bot: {report.bot_id}_",
        "",
    ]

    # --- New decisions ---
    lines.append("## New decisions")
    lines.append("")
    if report.new_decisions:
        for item in report.new_decisions:
            bullet = f"- {inline_text(item.content)}"
            if item.entities:
                bullet += f" _(entities: {', '.join(item.entities)})_"
            lines.append(bullet)
    else:
        lines.append("_None_")
    lines.append("")

    # --- Proposed supersessions ---
    lines.append("## Proposed supersessions")
    lines.append("")
    if report.proposed_supersessions:
        for prop in report.proposed_supersessions:
            new_text = inline_text(
                _find_decision_content(report, prop.new_signal_id) or prop.new_signal_id
            )
            old_text = inline_text(prop.old_content)
            conf_fmt = f"{prop.confidence:.2f}"
            lines.append(
                f'- "{new_text}" supersedes → "{old_text}"'
                f" (confidence {conf_fmt})"
                f" — confirm via POST /api/supersession/candidates/confirm"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # --- Potential conflicts ---
    lines.append("## Potential conflicts")
    lines.append("")
    if report.potential_conflicts:
        for cc in report.potential_conflicts:
            new_text = inline_text(
                _find_decision_content(report, cc.new_signal_id) or cc.new_signal_id
            )
            other_text = inline_text(cc.other_content)
            conf_fmt = f"{cc.confidence:.2f}"
            # inline_text the rationale; omit the "— {rationale}" fragment when empty
            rationale_text = inline_text(cc.rationale) if cc.rationale else ""
            rationale_fragment = f" — {rationale_text}" if rationale_text else ""
            lines.append(
                f'- "{new_text}" may conflict with "{other_text}"'
                f"{rationale_fragment} (confidence {conf_fmt})"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # --- Commitments opened ---
    lines.append("## Commitments opened")
    lines.append("")
    if report.commitments_opened:
        for item in report.commitments_opened:
            bullet = f"- {inline_text(item.content)}"
            extras = []
            if item.owner:
                extras.append(f"owner: {item.owner}")
            if item.due_date:
                extras.append(f"due: {item.due_date}")
            if extras:
                bullet += f" _({', '.join(extras)})_"
            lines.append(bullet)
    else:
        lines.append("_None_")
    lines.append("")

    # --- Commitments closed ---
    lines.append("## Commitments closed")
    lines.append("")
    if report.commitments_closed:
        for item in report.commitments_closed:
            bullet = f"- {inline_text(item.content)}"
            if item.owner:
                bullet += f" _(owner: {item.owner})_"
            lines.append(bullet)
    else:
        lines.append("_None_")
    lines.append("")

    # --- Entities touched ---
    lines.append("## Entities touched")
    lines.append("")
    if report.entities_touched:
        names = ", ".join(e["name"] for e in report.entities_touched)
        lines.append(names)
    else:
        lines.append("_None_")
    lines.append("")

    return "\n".join(lines)


def _find_decision_content(report: DeltaReport, signal_id: str) -> str | None:
    """Return the content of a decision in the report by signal_id."""
    for item in report.new_decisions:
        if item.signal_id == signal_id:
            return item.content
    return None
