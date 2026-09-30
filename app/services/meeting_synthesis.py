"""Meeting synthesis — the structured summary written into a meeting document.

Runs app/prompts/meeting_finalize.xml over a transcript and parses its JSON
envelope. Used by the ingest SYNTHESIZE phase and by
scripts/backfill_meeting_synthesis.py; the summary eval task
(evals/harness/runners/summary.py) grades the same prompt with the same
parser.

The summary is presentation, not extraction input: signals are still
promoted from the transcript body (Observation.content), so adding or
regenerating a summary never changes what a meeting's signals are.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.services.prompt_loader import load_prompt, prompt_sha

logger = logging.getLogger(__name__)

PROMPT_NAME = "meeting_finalize"
OPERATION = "meeting_summary"

# Content types whose documents get a synthesized summary. Email threads,
# Slack threads and documents have their own shape; the finalize prompt's
# sections (Decisions, Action Items, Next Steps) are written for meetings.
SYNTHESIS_CONTENT_TYPES = {"call_transcript"}

# Long calls fit comfortably in context; this only guards against a
# pathological paste. Beyond it the tail of the transcript is dropped.
MAX_TRANSCRIPT_CHARS = 400_000

# H2 sections the finalization JSON envelope must contain, in order. The
# regex-fallback signal extractor (SignalPromoter._extract_signals_regex)
# keys off these exact headings — keep them in sync.
FINALIZATION_SECTIONS = [
    "Summary",
    "Key Discussion Points",
    "Decisions",
    "Action Items",
    "Next Steps",
    "Insights",
]

MAX_KEY_POINTS = 8


def parse_finalization_response(response: str) -> dict:
    """Parse the meeting-finalization JSON envelope.

    Returns {"summary": str, "title": str|None, "purpose": str, "entities":
    list}. The purpose, when present, is also prepended to the summary in
    italics. On any parse/validation failure, falls back to the whole
    response as the summary with no title and a logged warning —
    finalization must never lose the meeting over a malformed envelope.
    """
    raw = (response or "").strip()
    fallback = {"summary": raw, "title": None, "purpose": "", "entities": []}
    if not raw:
        return fallback

    cleaned = raw
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    # strict=False tolerates literal newlines/control chars inside JSON
    # strings — models routinely emit summary_markdown with real newlines.
    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # Try to find a JSON object inside surrounding prose
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning("[FINALIZE] Response is not JSON; using raw text as summary")
            return fallback
        try:
            data = json.loads(match.group(), strict=False)
        except json.JSONDecodeError:
            logger.warning("[FINALIZE] Could not parse JSON envelope; using raw text")
            return fallback

    if not isinstance(data, dict):
        return fallback

    summary = (data.get("summary_markdown") or "").strip()
    if not summary:
        logger.warning("[FINALIZE] Envelope missing summary_markdown; using raw text")
        return fallback

    missing = [s for s in FINALIZATION_SECTIONS if f"## {s}" not in summary]
    if missing:
        # Required H2 sections drive downstream section-based extraction, and
        # the prompt contract says an empty section must still appear with
        # "None" content. Don't silently accept a summary that omits headings —
        # repair it so the section contract holds for downstream consumers
        # (without discarding an otherwise-good summary).
        logger.warning(
            "[FINALIZE] Envelope summary missing required sections %s; "
            "appending empty headings to honor the section contract",
            missing,
        )
        summary = summary.rstrip() + "\n\n" + "\n\n".join(
            f"## {s}\nNone" for s in missing
        )

    title = (data.get("title") or "").strip().rstrip(".") or None
    # Prompt contract caps titles at 8 words; reject longer model output.
    if title and (len(title.split()) > 8 or title.lower() in ("untitled meeting", "meeting summary")):
        logger.warning("[FINALIZE] Rejecting invalid title: %r", title)
        title = None

    purpose = (data.get("purpose") or "").strip()
    if purpose:
        summary = f"*{purpose}*\n\n{summary}"

    entities = []
    for item in data.get("entities_mentioned") or []:
        if isinstance(item, dict) and item.get("type") and item.get("name"):
            etype = str(item["type"]).strip().lower()
            ename = str(item["name"]).strip()
            # entity type becomes a persisted frontmatter bucket key
            # (meeting_state.entities_mentioned), so reject model output that
            # isn't a plausible type token (empty, oversized, or
            # non-identifier-like) instead of trusting it blindly.
            if (
                not ename
                or not etype
                or len(etype) > 40
                or not re.fullmatch(r"[a-z][a-z0-9_ -]*", etype)
            ):
                logger.warning(
                    "[FINALIZE] Dropping entity with invalid type/name: %r", item
                )
                continue
            entities.append({"type": etype, "name": ename})

    return {"summary": summary, "title": title, "purpose": purpose, "entities": entities}


def extract_key_points(summary: str, limit: int = MAX_KEY_POINTS) -> list[str]:
    """Bullets under '## Key Discussion Points', for the frontmatter."""
    match = re.search(
        r"^## Key Discussion Points\s*\n(.*?)(?=^## |\Z)", summary, re.MULTILINE | re.DOTALL
    )
    if not match:
        return []
    points = []
    for line in match.group(1).splitlines():
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line.strip())
        if not bullet:
            continue
        item = bullet.group(1).replace("**", "").strip()
        if item and item.lower() != "none":
            points.append(item)
        if len(points) >= limit:
            break
    return points


def prompt_version() -> str:
    """Short version tag written to the document's frontmatter."""
    sha = prompt_sha(PROMPT_NAME) or "unknown"
    return f"{PROMPT_NAME}/{sha[:12]}"


@dataclass
class MeetingSynthesis:
    summary: str  # markdown, the six FINALIZATION_SECTIONS (purpose line first)
    purpose: str = ""
    title: str | None = None  # model-suggested; callers decide whether to use it
    key_points: list[str] = field(default_factory=list)
    prompt: str = ""  # prompt_version() at generation time


def _state_body(title: str | None, participants: list[str], occurred: str | None) -> str:
    lines = [f"# {title or 'Untitled Meeting'}", ""]
    if occurred:
        lines.append(f"Date: {occurred}")
    lines.append(f"Participants: {', '.join(participants) or 'unknown'}")
    return "\n".join(lines)


def _response_text(response) -> str:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


async def synthesize_meeting(
    claude_client,
    transcript: str,
    *,
    title: str | None = None,
    participants: list[str] | None = None,
    occurred: str | None = None,
) -> MeetingSynthesis | None:
    """Summarize one meeting transcript. Returns None when there is nothing
    to summarize or the model returned no usable envelope; raises on a
    transport error so callers can decide whether that is fatal."""
    transcript = (transcript or "").strip()
    if not transcript or claude_client is None:
        return None
    prompt = load_prompt(PROMPT_NAME).format(
        current_state_body=_state_body(title, participants or [], occurred),
        full_transcript=transcript[:MAX_TRANSCRIPT_CHARS],
    )
    # Same parameters as the summary eval (evals/harness/runners/summary.py).
    response = await claude_client.generate_message(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
        temperature=0.3,
        operation=OPERATION,
    )
    parsed = parse_finalization_response(_response_text(response))
    summary = parsed["summary"].strip()
    if not summary or not all(f"## {s}" in summary for s in FINALIZATION_SECTIONS):
        # The fallback (unparseable envelope) is raw model text — don't write
        # that into a document as if it were a structured summary.
        logger.warning("[SYNTHESIZE] No usable summary envelope; leaving the meeting unsummarized")
        return None
    return MeetingSynthesis(
        summary=summary,
        purpose=parsed.get("purpose") or "",
        title=parsed.get("title"),
        key_points=extract_key_points(summary),
        prompt=prompt_version(),
    )
