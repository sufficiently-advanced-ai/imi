"""Decision audit artifact — Issue #954, Task 9 (R4.2).

Renders a summary audit document for all decisions (stale + superseded sections
with source links) and optionally commits it via git_ops.

Public API:
    AUDIT_ARTIFACT_DIR     — directory under repo root where the file is written
    render_decision_audit  — pure function, returns Markdown string
    export_decision_audit  — async: compute + render + write + optional git commit
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import UTC, datetime

from app.config import settings
from app.git_ops import git_ops
from app.services.artifact_markdown import (
    content_heading,
    inline_text,
    signals_link,
)
from app.services.decision_view import (
    compute_decision_stats,
    decision_to_view,
    load_decision_signals,
)
from app.services.signal_store import signal_store

logger = logging.getLogger(__name__)

AUDIT_ARTIFACT_DIR = "constitution"  # co-located with constitution.md by design (governance artifacts live together)


# ---------------------------------------------------------------------------
# Public: render_decision_audit
# ---------------------------------------------------------------------------


def render_decision_audit(
    stats: dict,
    decision_views: list[dict],
    *,
    tenant_id: str | None,
    now: datetime | None = None,
) -> str:
    """Render decision stats + filtered views as a Markdown audit artifact.

    Args:
        stats: Dict from compute_decision_stats() with keys: meetings, decisions,
               counts_by_state, stale, superseded, headline.
        decision_views: List of view dicts from decision_to_view(). Only stale
                        and superseded entries appear in the body sections.
        tenant_id: Tenant identifier for frontmatter. None → "DEFAULT".
        now: Reference time for frontmatter generated_at (defaults to UTC now).

    Returns:
        Markdown string with YAML frontmatter.
    """
    if now is None:
        now = datetime.now(UTC)

    tenant_label = tenant_id if tenant_id is not None else "DEFAULT"
    date_str = now.strftime("%Y-%m-%d")

    # Frontmatter
    fm_lines = [
        "---",
        "artifact: decision-audit",
        "version: 0",
        f"tenant_id: {tenant_label}",
        f"generated_at: {now.isoformat()}",
        "---",
    ]

    parts: list[str] = ["\n".join(fm_lines)]

    # H1 heading with date
    parts.append(f"\n# Decision Audit — {date_str}")

    # Headline — bold with trailing period
    headline = stats.get("headline", "")
    if not headline.endswith("."):
        headline = headline + "."
    parts.append(f"\n**{headline}**")

    # State table — sorted by state name, zero-count states omitted
    counts_by_state: dict[str, int] = stats.get("counts_by_state", {})
    table_rows = sorted(
        ((state, count) for state, count in counts_by_state.items() if count > 0),
        key=lambda pair: pair[0],
    )

    table_lines = ["| State | Count |", "|-------|-------|"]
    for state_name, count in table_rows:
        table_lines.append(f"| {state_name} | {count} |")
    parts.append("\n" + "\n".join(table_lines))

    # Separate stale and superseded from views, sort newest-first
    stale_views = sorted(
        [v for v in decision_views if v["state"] == "stale"],
        key=lambda v: v.get("source_timestamp", ""),
        reverse=True,
    )
    superseded_views = sorted(
        [v for v in decision_views if v["state"] == "superseded"],
        key=lambda v: v.get("source_timestamp", ""),
        reverse=True,
    )

    # --- Stale section ---
    parts.append("\n## Stale")
    if stale_views:
        for v in stale_views:
            heading = inline_text(content_heading(v["content"]))
            link = signals_link(v["source_meeting_id"])
            state_reason = inline_text(v.get("state_reason", ""))
            owner = v.get("owner") or "Unassigned"
            parts.append(f"- [{heading}]({link}) — {state_reason}, owner {owner}")
    else:
        parts.append("_None_")

    # --- Superseded section ---
    parts.append("\n## Superseded")
    if superseded_views:
        for v in superseded_views:
            heading = inline_text(content_heading(v["content"]))
            link = signals_link(v["source_meeting_id"])
            superseded_by = v.get("superseded_by") or "(unknown)"
            parts.append(f"- [{heading}]({link}) — superseded by `{superseded_by}`")
    else:
        parts.append("_None_")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public: export_decision_audit
# ---------------------------------------------------------------------------


async def export_decision_audit(
    *, commit: bool = True, now: datetime | None = None
) -> dict:
    """Compute stats + views, render, atomic-write, and optionally commit via git.

    Args:
        commit: If True (default), stage and push via git_ops.commit_and_push.
                If False, only write the file.
        now: Reference time (defaults to UTC now). Exposed for testing.

    Returns:
        dict with keys:
            path: repo-relative path to the written file
            committed: bool — True if git commit succeeded
            headline: str — the headline from stats
    """
    if now is None:
        now = datetime.now(UTC)

    date_str = now.strftime("%Y-%m-%d")
    relative_path = f"{AUDIT_ARTIFACT_DIR}/decision-audit-{date_str}.md"

    # Load signals and compute stats + views
    signals = load_decision_signals(store=signal_store)
    views = [decision_to_view(s, now=now) for s in signals]
    stats = compute_decision_stats(store=signal_store, now=now)

    # Render
    text = render_decision_audit(stats, views, tenant_id=None, now=now)

    # Atomic write
    full_path = os.path.join(git_ops.repo_path, relative_path)
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

    logger.info("[AUDIT] Written to %s", full_path)

    # Commit
    committed = False
    if commit:
        try:
            commit_msg = f"{settings.BOT_COMMIT_PREFIX} Decision audit {date_str}"
            await git_ops.commit_and_push([relative_path], commit_msg)
            committed = True
        except Exception:
            logger.warning(
                "[AUDIT] git commit failed; file written but not committed",
                exc_info=True,
            )

    return {
        "path": relative_path,
        "committed": committed,
        "headline": stats.get("headline", ""),
    }
