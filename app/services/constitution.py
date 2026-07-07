"""Constitution export service — Issue #954, Task 8 (R4.1).

Renders all active/stale/superseded decision views as a structured Markdown
artifact and optionally commits it via git_ops.

Public API:
    CONSTITUTION_RELATIVE_PATH    — repo-relative path of the output file
    render_constitution(...)      — pure function, returns Markdown string
    render_current_constitution() — build current Markdown in-memory (no I/O)
    export_constitution(...)      — async: render + write + optional git commit
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime

from app.config import settings
from app.git_ops import git_ops
from app.services.artifact_markdown import (
    content_heading,
    inline_text,
    signals_link,
)
from app.services.decision_states import STALE_AGE_DAYS
from app.services.decision_view import decision_to_view, load_decision_signals
from app.services.signal_store import signal_store

logger = logging.getLogger(__name__)

CONSTITUTION_RELATIVE_PATH = "constitution/constitution.md"

# States to include in the constitution (confirmed temporary/zombie/conflicting
# are also rendered; unconfirmed variants are counted in decisions_pending_review)
_INCLUDED_STATES = {
    "active",
    "stale",
    "superseded",
    "temporary",
    "zombie",
    "conflicting",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _authority(view: dict) -> str:
    if view["can_use_as_instruction"]:
        return "instruction-grade"
    elif view["can_use_as_evidence"]:
        return "evidence-grade"
    return "blocked"


def _date_str(iso_timestamp: str | None) -> str:
    """Return YYYY-MM-DD from an ISO timestamp, or '(unknown)' on failure."""
    if not iso_timestamp:
        return "(unknown)"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "(unknown)"


def _enrich_rationale(views: list[dict], audit_store) -> dict[str, str]:
    """Return a map from view id → rationale string.

    Priority:
      1. view["metadata"].get("rationale") if truthy
      2. First reasoning from audit_store.read_for_signal(id)
      3. _(no recorded rationale)_
    """
    result: dict[str, str] = {}
    for v in views:
        vid = v["id"]
        # 1. metadata rationale
        meta_rationale = v.get("metadata", {}).get("rationale", "")
        if meta_rationale:
            result[vid] = meta_rationale
            continue
        # 2. audit reasoning
        if audit_store is not None:
            try:
                records = audit_store.read_for_signal(vid)
                for rec in records:
                    reasoning = getattr(rec, "reasoning", None)
                    if reasoning:
                        result[vid] = reasoning
                        break
            except Exception:
                logger.warning(
                    "[CONSTITUTION] audit read failed for %s", vid, exc_info=True
                )
        if vid not in result:
            result[vid] = "_(no recorded rationale)_"
    return result


def _render_decision_entry(view: dict, rationale: str) -> str:
    """Render a single decision as a Markdown entry block."""
    heading = content_heading(view["content"])
    owner = view["owner"] or "Unassigned"
    state = view["state"]
    state_reason = inline_text(view.get("state_reason", ""))
    source_meeting_id = view["source_meeting_id"]
    source_meeting_title = view.get("source_meeting_title") or source_meeting_id
    decided_date = _date_str(view.get("source_timestamp"))
    link = signals_link(source_meeting_id)
    authority = _authority(view)

    lines = [
        f"#### {heading}",
        f"- **Owner:** {owner}",
        f"- **State:** {state} ({state_reason})",
        f"- **Decided:** {decided_date} — [{source_meeting_title}]({link})",
        f"- **Rationale:** {inline_text(rationale)}",
        f"- **Authority:** {authority}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public: render_constitution
# ---------------------------------------------------------------------------


def render_constitution(
    decision_views: list[dict],
    *,
    tenant_id: str | None,
    now: datetime | None = None,
    stale_age_days: int = STALE_AGE_DAYS,
    audit_store=None,
) -> str:
    """Render decision views as a structured Markdown constitution artifact.

    Args:
        decision_views: List of view dicts from decision_to_view(). All states
                        accepted; rejected/candidate are silently excluded.
                        Stale views are only included in the Stale section if
                        review_status == "confirmed"; stale-but-unconfirmed views
                        are counted in the pending backlog instead.
        tenant_id: Tenant identifier for frontmatter. Use None for single-tenant;
                   renders as "DEFAULT".
        now: Reference time for frontmatter generated_at (defaults to UTC now).
        stale_age_days: Threshold used in frontmatter metadata.
        audit_store: Optional SignalAuditStore for rationale enrichment.

    Returns:
        Markdown string with YAML frontmatter.
    """
    if now is None:
        now = datetime.now(UTC)

    # Filter to relevant states only
    included = [v for v in decision_views if v["state"] in _INCLUDED_STATES]

    active = [v for v in included if v["state"] == "active"]
    # Stale section only shows confirmed-stale decisions; unconfirmed-stale are pending
    stale_confirmed = [
        v
        for v in included
        if v["state"] == "stale" and v.get("review_status") == "confirmed"
    ]
    stale_unconfirmed = [
        v
        for v in included
        if v["state"] == "stale" and v.get("review_status") != "confirmed"
    ]
    # Temporary: confirmed → Active section; unconfirmed → pending backlog
    temporary_confirmed = [
        v
        for v in included
        if v["state"] == "temporary" and v.get("review_status") == "confirmed"
    ]
    temporary_unconfirmed = [
        v
        for v in included
        if v["state"] == "temporary" and v.get("review_status") != "confirmed"
    ]
    # Zombie: confirmed → Stale Decisions section; unconfirmed → pending backlog
    zombie_confirmed = [
        v
        for v in included
        if v["state"] == "zombie" and v.get("review_status") == "confirmed"
    ]
    zombie_unconfirmed = [
        v
        for v in included
        if v["state"] == "zombie" and v.get("review_status") != "confirmed"
    ]
    # Conflicting: confirmed → Conflicting Decisions section; unconfirmed → pending
    conflicting_confirmed = [
        v
        for v in included
        if v["state"] == "conflicting" and v.get("review_status") == "confirmed"
    ]
    conflicting_unconfirmed = [
        v
        for v in included
        if v["state"] == "conflicting" and v.get("review_status") != "confirmed"
    ]
    superseded = [v for v in included if v["state"] == "superseded"]

    # Pending backlog: candidates + stale-but-unconfirmed + unconfirmed temporary/zombie/conflicting
    candidate_views = [v for v in decision_views if v["state"] == "candidate"]
    decisions_pending_review = (
        len(candidate_views)
        + len(stale_unconfirmed)
        + len(temporary_unconfirmed)
        + len(zombie_unconfirmed)
        + len(conflicting_unconfirmed)
    )

    # Active section: active + confirmed-temporary (sorted by client then timestamp)
    active_section_views = active + temporary_confirmed

    # Stale section: confirmed-stale + confirmed-zombie (newest first)
    stale_section_views = stale_confirmed + zombie_confirmed
    stale_section_views.sort(key=lambda v: v.get("source_timestamp", ""), reverse=True)
    superseded.sort(key=lambda v: v.get("source_timestamp", ""), reverse=True)

    tenant_label = tenant_id if tenant_id is not None else "DEFAULT"

    # Frontmatter — decisions_total counts all rendered entries:
    # active + confirmed-temporary + confirmed-stale + confirmed-zombie +
    # confirmed-conflicting + superseded.
    # Unconfirmed decisions are reported via decisions_pending_review.
    # decisions_active counts only state==active; decisions_stale counts only
    # state==stale (confirmed); decisions_temporary/zombie/conflicting count
    # their respective confirmed entries.
    fm_lines = [
        "---",
        "artifact: constitution",
        "version: 0",
        f"tenant_id: {tenant_label}",
        f"generated_at: {now.isoformat()}",
        f"stale_threshold_days: {stale_age_days}",
        (
            f"decisions_total: "
            f"{len(active) + len(temporary_confirmed) + len(stale_confirmed) + len(zombie_confirmed) + len(conflicting_confirmed) + len(superseded)}"
        ),
        f"decisions_active: {len(active)}",
        f"decisions_temporary: {len(temporary_confirmed)}",
        f"decisions_stale: {len(stale_confirmed)}",
        f"decisions_zombie: {len(zombie_confirmed)}",
        f"decisions_conflicting: {len(conflicting_confirmed)}",
        f"decisions_superseded: {len(superseded)}",
        f"decisions_pending_review: {decisions_pending_review}",
        "---",
    ]

    parts: list[str] = ["\n".join(fm_lines)]

    # Header
    parts.append(
        "\n# Constitution\n\n"
        "> Confirmed decisions for this account, computed from governance review\n"
        "> states. Regenerate via POST /api/decisions/constitution/export."
    )

    # Pending backlog notice — shown immediately after intro when there are pending decisions
    if decisions_pending_review > 0:
        parts.append(
            f"\n**{decisions_pending_review} decision"
            f"{'s are' if decisions_pending_review != 1 else ' is'} awaiting review"
            " and are not yet part of this constitution."
            " Confirm them in the app (/decisions) to promote them here.**"
        )

    # Empty corpus shortcut — nothing rendered means no confirmed active/temporary/
    # stale/zombie/conflicting/superseded entries (unconfirmed-only is still "empty")
    if not (
        active_section_views
        or stale_section_views
        or conflicting_confirmed
        or superseded
    ):
        parts.append("\n_No confirmed decisions yet._")
        return "\n".join(parts)

    # Rationale enrichment for all rendered views
    rendered_views = (
        active_section_views + stale_section_views + conflicting_confirmed + superseded
    )
    rationale_map = _enrich_rationale(rendered_views, audit_store)

    # --- Active Decisions (active + confirmed-temporary) ---
    if active_section_views:
        parts.append("\n## Active Decisions")
        # Group by client_id; None → "General"
        groups: dict[str, list[dict]] = defaultdict(list)
        for v in active_section_views:
            key = v["client_id"] or "General"
            groups[key].append(v)

        for group_key in sorted(groups.keys()):
            parts.append(f"\n### {group_key}")
            group_views = sorted(
                groups[group_key],
                key=lambda v: v.get("source_timestamp", ""),
                reverse=True,
            )
            for v in group_views:
                entry = _render_decision_entry(
                    v, rationale_map.get(v["id"], "_(no recorded rationale)_")
                )
                parts.append(f"\n{entry}")

    # --- Conflicting Decisions (confirmed-conflicting) ---
    if conflicting_confirmed:
        # Build a lookup from signal id → source_meeting_id for cross-linking
        all_views_by_id = {v["id"]: v for v in decision_views}

        parts.append("\n## Conflicting Decisions")
        for v in conflicting_confirmed:
            entry = _render_decision_entry(
                v, rationale_map.get(v["id"], "_(no recorded rationale)_")
            )
            # Append conflict links for each id in conflicts_with
            conflicts_with_ids = v.get("metadata", {}).get("conflicts_with", [])
            conflict_lines = []
            for other_id in conflicts_with_ids:
                other_view = all_views_by_id.get(other_id)
                if other_view:
                    other_meeting_id = other_view.get("source_meeting_id", "")
                    link = signals_link(other_meeting_id) if other_meeting_id else ""
                    if link:
                        conflict_lines.append(
                            f"- **⚠ Conflicts with:** [`{other_id}`]({link})"
                        )
                    else:
                        conflict_lines.append(f"- **⚠ Conflicts with:** `{other_id}`")
                else:
                    conflict_lines.append(f"- **⚠ Conflicts with:** `{other_id}`")
            if conflict_lines:
                entry = entry + "\n" + "\n".join(conflict_lines)
            parts.append(f"\n{entry}")

    # --- Stale Decisions (confirmed-stale + confirmed-zombie) ---
    if stale_section_views:
        parts.append("\n## Stale Decisions")
        for v in stale_section_views:
            entry = _render_decision_entry(
                v, rationale_map.get(v["id"], "_(no recorded rationale)_")
            )
            parts.append(f"\n{entry}")

    # --- Superseded ---
    if superseded:
        parts.append("\n## Superseded")
        for v in superseded:
            heading = content_heading(v["content"])
            superseded_by_id = v.get("superseded_by") or "(unknown)"
            sig_link = signals_link(v["source_meeting_id"])
            parts.append(
                f"\n- ~~{heading}~~ → superseded by [`{superseded_by_id}`]({sig_link})"
            )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public: export_constitution
# ---------------------------------------------------------------------------


def _current_decision_views() -> tuple[list[dict], datetime]:
    """Load all decision signals and project them to constitution views.

    Shared by ``export_constitution`` (which also writes/commits) and
    ``render_current_constitution`` (which only renders). Returns the projected
    views plus the ``now`` timestamp they were computed against, so callers that
    render get the same reference time used for age/staleness.
    """
    now = datetime.now(UTC)
    signals = load_decision_signals(store=signal_store)
    views = [decision_to_view(s, now=now) for s in signals]
    return views, now


def render_current_constitution() -> str:
    """Build the current constitution Markdown in-memory (no disk/git write).

    Always reflects the latest decision signals, so consumers never hit a stale
    or missing persisted artifact. This is the read path behind the
    ``get_constitution`` MCP tool.
    """
    views, now = _current_decision_views()
    return render_constitution(views, tenant_id=None, now=now)


async def export_constitution(*, commit: bool = True) -> dict:
    """Render the constitution, write to repo, and optionally commit via git.

    Args:
        commit: If True (default), stage and push via git_ops.commit_and_push.
                If False, only write the file.

    Returns:
        dict with keys:
            path: CONSTITUTION_RELATIVE_PATH (relative to repo root)
            committed: bool — True if git commit succeeded
            counts_by_state: dict[state, int] — counts of included states
    """
    # Load and project decisions (shared with render_current_constitution)
    views, now = _current_decision_views()

    # Compute counts for return value
    counts_by_state: dict[str, int] = {}
    for v in views:
        s = v["state"]
        counts_by_state[s] = counts_by_state.get(s, 0) + 1

    # Render
    text = render_constitution(views, tenant_id=None, now=now)

    # Write to repo (atomic: write to .tmp then replace)
    full_path = os.path.join(git_ops.repo_path, CONSTITUTION_RELATIVE_PATH)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    def _write_atomic(final_path: str, content: str) -> None:
        dir_path = os.path.dirname(final_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, final_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    await asyncio.to_thread(_write_atomic, full_path, text)

    logger.info("[CONSTITUTION] Written to %s", full_path)

    # Commit
    committed = False
    if commit:
        try:
            await git_ops.commit_and_push(
                [CONSTITUTION_RELATIVE_PATH],
                f"{settings.BOT_COMMIT_PREFIX} Export constitution artifact",
            )
            committed = True
        except Exception:
            logger.warning(
                "[CONSTITUTION] git commit failed; file written but not committed",
                exc_info=True,
            )

    return {
        "path": CONSTITUTION_RELATIVE_PATH,
        "committed": committed,
        "counts_by_state": counts_by_state,
    }
