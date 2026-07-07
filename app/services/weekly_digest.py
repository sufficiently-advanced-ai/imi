"""Weekly standing digest service (Sprint 3, Task S3-4).

Renders a weekly snapshot of decision health: counts diff vs last week,
active-decision churn, state transitions, and aging commitments.

Public API:
    WEEKLY_DIGEST_DIR        — repo-relative directory for output files
    COMMITMENT_AGING_DAYS    — default threshold for surfacing old open action items
    WeeklyDigest             — Pydantic model for digest data
    build_weekly_digest(...) — pure builder from pre-loaded data
    render_weekly_digest(...)— pure Markdown renderer
    export_weekly_digest(...)— async: build + write + optional git commit
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.config import settings
from app.git_ops import GitRevisionReadError, git_ops
from app.services.artifact_markdown import inline_text
from app.services.constitution import CONSTITUTION_RELATIVE_PATH, render_constitution
from app.services.signal_store import signal_store
from app.services.staleness_evaluator import TRANSITIONS_RELATIVE_PATH

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.signal import Signal

logger = logging.getLogger(__name__)

WEEKLY_DIGEST_DIR = "digests"
COMMITMENT_AGING_DAYS = 7
AGING_COMMITMENTS_CAP = 20

# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class WeeklyDigest(BaseModel):
    generated_at: str
    period_days: int = 7
    counts_now: dict[str, int]
    counts_then: dict[str, int] | None  # None = first digest
    active_added: list[str]
    active_removed: list[str]
    transitions: list[dict]
    aging_commitments: list[dict]  # {signal_id, content, owner, age_days}
    aging_overflow: int = 0  # count of aging commitments past cap
    pending_review: int


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(md: str) -> dict[str, int] | None:
    """Extract integer counts from YAML frontmatter of a constitution Markdown file.

    Returns None if the document has no frontmatter or on any parse failure.
    """
    if not md:
        return None

    # Frontmatter is delimited by leading '---' ... '---'
    match = re.match(r"^---\s*\n(.*?)\n---", md, re.DOTALL)
    if not match:
        return None

    fm_text = match.group(1)
    counts: dict[str, int] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        for key in (
            "decisions_active",
            "decisions_stale",
            "decisions_superseded",
            "decisions_pending_review",
        ):
            prefix = f"{key}:"
            if line.startswith(prefix):
                raw = line[len(prefix) :].strip()
                try:
                    counts[key] = int(raw)
                except ValueError:
                    pass

    return counts if counts else None


def _extract_active_headings(constitution_md: str) -> set[str]:
    """Return the set of '#### ' heading texts from the Active Decisions section only."""
    headings: list[str] = []
    in_active = False
    for line in constitution_md.splitlines():
        if line.startswith("## Active Decisions"):
            in_active = True
            continue
        # Stop at next ## section (Stale, Superseded, etc.)
        if in_active and line.startswith("## ") and "Active" not in line:
            break
        if in_active and line.startswith("#### "):
            headings.append(line[5:].strip())
    return set(headings)


def _write_atomic(final_path: str, content: str) -> None:
    """Write content to final_path atomically via tempfile + os.replace."""
    dir_path = os.path.dirname(final_path)
    os.makedirs(dir_path, exist_ok=True)
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


# ---------------------------------------------------------------------------
# Public: build_weekly_digest (pure)
# ---------------------------------------------------------------------------


def build_weekly_digest(
    *,
    views: list[dict],
    all_signals: list[Signal],
    prev_constitution_md: str | None,
    transitions: list[dict],
    now: datetime,
    aging_days: int = COMMITMENT_AGING_DAYS,
) -> WeeklyDigest:
    """Build a WeeklyDigest from pre-loaded data.

    Args:
        views: Decision view dicts (from decision_to_view).
        all_signals: All signals (across all types) for aging commitments.
        prev_constitution_md: Constitution Markdown from ~7 days ago; None = first digest.
        transitions: All transition dicts from state-transitions.jsonl (pre-window-filtered
                     is fine — this function applies the 7-day window filter itself).
        now: Reference time.
        aging_days: Threshold for surfacing open action_items in aging_commitments.

    Returns:
        WeeklyDigest instance.
    """
    window_start = now - timedelta(days=7)

    # --- counts_now: render fresh constitution and parse frontmatter ---
    fresh_md = render_constitution(views, tenant_id=None, now=now)
    counts_now = _parse_frontmatter(fresh_md) or {
        "decisions_active": 0,
        "decisions_stale": 0,
        "decisions_superseded": 0,
        "decisions_pending_review": 0,
    }

    # --- counts_then: from prev constitution frontmatter ---
    counts_then: dict[str, int] | None = None
    if prev_constitution_md is not None:
        counts_then = _parse_frontmatter(prev_constitution_md)

    # --- active heading diff ---
    active_added: list[str] = []
    active_removed: list[str] = []
    if prev_constitution_md is not None:
        now_headings = _extract_active_headings(fresh_md)
        prev_headings = _extract_active_headings(prev_constitution_md)
        active_added = sorted(now_headings - prev_headings)
        active_removed = sorted(prev_headings - now_headings)

    # --- transitions window filter ---
    filtered_transitions: list[dict] = []
    for t in transitions:
        try:
            t_at = datetime.fromisoformat(t["at"])
            if t_at.tzinfo is None:
                t_at = t_at.replace(tzinfo=UTC)
            if t_at >= window_start:
                filtered_transitions.append(t)
        except (ValueError, KeyError, TypeError):
            # Skip malformed transition entries
            pass

    # --- aging commitments ---
    aging_commitments: list[dict] = []
    for sig in all_signals:
        if sig.type != "action_item":
            continue
        # Only open (or in_progress) action items
        if sig.status in ("done", "completed"):
            continue
        # Compute age from created_at
        try:
            created = datetime.fromisoformat(sig.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
        except (ValueError, TypeError, AttributeError):
            continue
        age_days = (now - created).days
        if age_days < aging_days:
            continue
        owner_name = sig.owner.name if sig.owner is not None else "Unassigned"
        aging_commitments.append(
            {
                "signal_id": sig.id,
                "content": sig.content,
                "owner": owner_name,
                "age_days": age_days,
            }
        )

    # Sort aging commitments oldest first (highest age_days first)
    aging_commitments.sort(key=lambda c: c["age_days"], reverse=True)

    # Cap and track overflow
    aging_overflow = max(0, len(aging_commitments) - AGING_COMMITMENTS_CAP)
    aging_commitments = aging_commitments[:AGING_COMMITMENTS_CAP]

    # --- pending_review ---
    pending_review = counts_now.get("decisions_pending_review", 0)

    return WeeklyDigest(
        generated_at=now.isoformat(),
        period_days=7,
        counts_now=counts_now,
        counts_then=counts_then,
        active_added=active_added,
        active_removed=active_removed,
        transitions=filtered_transitions,
        aging_commitments=aging_commitments,
        aging_overflow=aging_overflow,
        pending_review=pending_review,
    )


# ---------------------------------------------------------------------------
# Public: render_weekly_digest (pure)
# ---------------------------------------------------------------------------


def render_weekly_digest(d: WeeklyDigest) -> str:
    """Render a WeeklyDigest to Markdown.

    Sections:
      - "# Weekly digest — YYYY-MM-DD"
      - "## Since last week" — counts table OR first-digest line; added/removed bullets
      - "## State changes"  — one bullet per transition (omitted when empty)
      - "## Awaiting review" — "**N decisions awaiting review.**" (omitted when 0)
      - "## Commitments aging" — one bullet per aging commitment (omitted when empty)
    """
    try:
        date_str = datetime.fromisoformat(d.generated_at).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = d.generated_at[:10]

    parts: list[str] = [f"# Weekly digest — {date_str}"]

    # --- Since last week ---
    parts.append("\n## Since last week")

    if d.counts_then is None:
        parts.append("\n_First weekly digest — no prior constitution to compare._")
    else:
        # Counts table: state | then | now | Δ
        state_keys = [
            ("decisions_active", "active"),
            ("decisions_stale", "stale"),
            ("decisions_superseded", "superseded"),
            ("decisions_pending_review", "pending review"),
        ]
        table_rows = ["| state | then | now | Δ |", "| --- | --- | --- | --- |"]
        for key, label in state_keys:
            then_val = d.counts_then.get(key, 0)
            now_val = d.counts_now.get(key, 0)
            delta = now_val - then_val
            delta_str = f"+{delta}" if delta > 0 else str(delta)
            table_rows.append(f"| {label} | {then_val} | {now_val} | {delta_str} |")
        parts.append("\n" + "\n".join(table_rows))

    # --- Active heading churn ---
    if d.active_added or d.active_removed:
        parts.append("")
        if d.active_added:
            parts.append(f"**Now active:** {', '.join(d.active_added)}")
            for heading in d.active_added:
                parts.append(f"+ {inline_text(heading)}")
        if d.active_removed:
            for heading in d.active_removed:
                parts.append(f"− {inline_text(heading)}")

    # --- State changes ---
    parts.append("\n## State changes")
    if d.transitions:
        for t in d.transitions:
            sid = t.get("signal_id", "")[:8]
            from_state = t.get("from", "?")
            to_state = t.get("to", "?")
            reason = inline_text(t.get("reason", ""))
            parts.append(f"- `{sid}` {from_state} → {to_state} ({reason})")
    else:
        parts.append("_None_")

    # --- Awaiting review ---
    if d.pending_review > 0:
        parts.append("\n## Awaiting review")
        n = d.pending_review
        parts.append(f"**{n} decision{'s' if n != 1 else ''} awaiting review.**")

    # --- Commitments aging ---
    parts.append("\n## Commitments aging")
    if d.aging_commitments:
        for c in d.aging_commitments:
            content = inline_text(c["content"])
            owner = c.get("owner", "Unassigned")
            age_days = c["age_days"]
            parts.append(f"- {content} — owner {owner}, open {age_days}d")
        # Append overflow line if there are more commitments past cap
        if d.aging_overflow > 0:
            parts.append(
                f"_…and {d.aging_overflow} more open commitments past threshold._"
            )
    else:
        parts.append("_None_")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public: export_weekly_digest (async, I/O)
# ---------------------------------------------------------------------------


async def export_weekly_digest(
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict:
    """Build, render, and persist the weekly digest.

    Algorithm:
      1. prev: get_revision_before(now-7d, CONSTITUTION_RELATIVE_PATH);
         read_file_at_revision if rev exists
      2. transitions: read {repo}/constitution/state-transitions.jsonl if present
      3. Load views and all_signals from signal_store
      4. build_weekly_digest + render_weekly_digest
      5. Atomic write digests/weekly-{now:%Y-%m-%d}.md
      6. commit_and_push if commit=True; git failure → committed=False
      7. Return {path, committed, summary}

    Args:
        now: Reference time (defaults to UTC now).
        commit: If True, attempt git commit.

    Returns:
        dict with keys:
            path: repo-relative path of written file
            committed: bool
            summary: {transitions: n, active_added: n, active_removed: n, aging: n}
    """
    if now is None:
        now = datetime.now(UTC)

    _git = git_ops

    # --- 1. Fetch previous constitution (7 days ago) ---
    week_ago = (now - timedelta(days=7)).isoformat()
    prev_constitution_md: str | None = None
    try:
        rev = await _git.get_revision_before(week_ago, CONSTITUTION_RELATIVE_PATH)
        if rev:
            prev_constitution_md = await _git.read_file_at_revision(
                CONSTITUTION_RELATIVE_PATH, rev
            )
    except GitRevisionReadError:
        logger.warning(
            "[WEEKLY_DIGEST] prev constitution unavailable (git failure), "
            "rendering as first digest",
            exc_info=True,
        )
    except Exception:
        logger.warning(
            "[WEEKLY_DIGEST] Failed to fetch previous constitution", exc_info=True
        )

    # --- 2. Load transitions from JSONL ---
    transitions: list[dict] = []
    transitions_full_path = os.path.join(_git.repo_path, TRANSITIONS_RELATIVE_PATH)
    if os.path.isfile(transitions_full_path):
        try:
            with open(transitions_full_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            transitions.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception:
            logger.warning(
                "[WEEKLY_DIGEST] Failed to read transitions JSONL", exc_info=True
            )

    # --- 3. Load signals ---
    from app.services.decision_view import decision_to_view, load_decision_signals

    decision_signals = load_decision_signals(store=signal_store)
    views = [decision_to_view(s, now=now) for s in decision_signals]

    # All signals (for aging commitments)
    all_signals: list = []
    for meeting_signals in signal_store.load_all():
        all_signals.extend(meeting_signals.signals)

    # --- 4. Build + render ---
    digest = build_weekly_digest(
        views=views,
        all_signals=all_signals,
        prev_constitution_md=prev_constitution_md,
        transitions=transitions,
        now=now,
        aging_days=settings.COMMITMENT_AGING_DAYS,
    )
    text = render_weekly_digest(digest)

    # --- 5. Write file atomically ---
    date_str = now.strftime("%Y-%m-%d")
    relative_path = f"{WEEKLY_DIGEST_DIR}/weekly-{date_str}.md"
    full_path = os.path.join(_git.repo_path, relative_path)
    await asyncio.to_thread(_write_atomic, full_path, text)
    logger.info("[WEEKLY_DIGEST] Written to %s", full_path)

    # --- 6. Commit ---
    committed = False
    if commit:
        try:
            await _git.commit_and_push(
                [relative_path],
                f"{settings.BOT_COMMIT_PREFIX} Weekly digest {date_str}",
            )
            committed = True
        except Exception:
            logger.warning(
                "[WEEKLY_DIGEST] git commit failed; file written but not committed",
                exc_info=True,
            )

    # --- 7. Return summary ---
    return {
        "path": relative_path,
        "committed": committed,
        "summary": {
            "transitions": len(digest.transitions),
            "active_added": len(digest.active_added),
            "active_removed": len(digest.active_removed),
            "aging": len(digest.aging_commitments),
        },
    }
