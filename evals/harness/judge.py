"""LLM-as-judge for rubric items that can't be checked deterministically.

Sonnet at temperature 0, one call per fixture, returns per-item boolean
verdicts with quoted evidence. Judge items live in the rubric markdown files
(evals/rubrics/) and are mirrored by id in the runners that use them.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
You are grading a generated meeting summary against a rubric. For each rubric
item below, decide pass or fail based ONLY on the provided transcript and the
generated output.

<transcript>
{transcript}
</transcript>

<generated_output>
{output}
</generated_output>

<rubric_items>
{items}
</rubric_items>

For every rubric item, return a verdict. Quote the exact text from the
generated output (or note its absence) as evidence.

Verify before answering: re-read each verdict and confirm the quoted evidence
actually appears in the generated output. If an item's evidence does not hold
up, change the verdict.

Return ONLY a JSON object, no markdown fences, shaped exactly like:
{{"verdicts": {{"<item_id>": {{"pass": true, "evidence": "..."}}}}}}
"""


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    fence = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE
    )
    return fence.group(1).strip() if fence else cleaned


async def judge_rubric_items(
    client: Any,
    transcript: str,
    output: str,
    items: list[dict],
) -> dict:
    """Grade rubric items (list of {id, text}) against a generated output.

    Returns {item_id: {"pass": bool, "evidence": str}}. Items the judge
    fails to return verdicts for are marked failed with a note — a silent
    pass on a missing verdict would hide judge drift.
    """
    from app.config import settings

    items_text = "\n".join(f"- [{it['id']}] {it['text']}" for it in items)
    prompt = _JUDGE_PROMPT.format(
        transcript=transcript, output=output, items=items_text
    )

    response = await client.generate_message(
        messages=[{"role": "user", "content": prompt}],
        model=settings.CLAUDE_SONNET_MODEL,
        max_tokens=2000,
        temperature=0.0,
        operation="eval_rubric_judge",
    )

    from .runners.base import response_text

    text = response_text(response) or ""
    if not text:
        logger.warning(
            "[EVAL/judge] Empty text extracted from judge response (type=%s)",
            type(response).__name__,
        )

    verdicts: dict = {}
    try:
        parsed = json.loads(_strip_fences(text))
        verdicts = parsed.get("verdicts", {})
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("[EVAL/judge] Could not parse judge response: %s", e)

    results = {}
    for it in items:
        v = verdicts.get(it["id"])
        if isinstance(v, dict) and isinstance(v.get("pass"), bool):
            results[it["id"]] = {
                "pass": v["pass"],
                "evidence": str(v.get("evidence", "")),
            }
        else:
            results[it["id"]] = {
                "pass": False,
                "evidence": "<no verdict returned by judge>",
            }
    return results
