"""Shared pure markdown helpers for governance artifacts (constitution, decision audit, delta reports)."""

# Signals filename pattern (from SignalStore._file_path): meeting-{bot_id}.json
SIGNALS_LINK_TPL = "signals/meeting-{bot_id}.json"


def signals_link(source_meeting_id: str) -> str:
    """Return the repo-relative link to a meeting's signal file."""
    return SIGNALS_LINK_TPL.format(bot_id=source_meeting_id)


def inline_text(text: str) -> str:
    """Collapse all whitespace/newlines to a single space — safe for Markdown bullets."""
    return " ".join(text.split())


def content_heading(content: str) -> str:
    """Return the first line of content as heading text, stripping leading Markdown markers.

    Returns "(untitled decision)" for empty or whitespace-only content.
    """
    stripped = content.strip() if content else ""
    if not stripped:
        return "(untitled decision)"
    lines = stripped.splitlines()
    if not lines:
        return "(untitled decision)"
    first = lines[0].strip().lstrip("#>- ").strip()
    return first if first else "(untitled decision)"
