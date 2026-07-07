"""Meeting-finalization envelope parser used by the summary eval task.

Vendored copy of the production parser for the meeting_finalize.xml JSON
envelope. The community edition ships the prompt but not the meeting
state-sync pipeline that consumes it, so the harness carries the parser to
grade finalization output exactly the way the full pipeline does.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

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


def parse_finalization_response(response: str) -> dict:
    """Parse the meeting-finalization JSON envelope.

    Returns {"summary": str, "title": str|None, "entities": list}. On any
    parse/validation failure, falls back to the legacy behavior (whole
    response as the summary, no title) with a logged warning — finalization
    must never lose the meeting over a malformed envelope.
    """
    raw = (response or "").strip()
    fallback = {"summary": raw, "title": None, "entities": []}
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

    return {"summary": summary, "title": title, "entities": entities}
