"""Summary eval runner — exercises the shipping meeting-finalization prompt.

Live mode formats app/prompts/meeting_finalize.xml the way the production
finalization path does (default Sonnet model, temp 0.3,
max_tokens 4000) with a minimal fresh meeting state, then grades the output
against evals/rubrics/summary_rubric.md: deterministic MUST checks here,
judgment MUST/SHOULD items via harness.judge. Item ids mirror the rubric.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..consistency import summary_names_grounded
from ..judge import judge_rubric_items
from ..matching import check_summary_keywords, check_title
from .base import TaskResult, replay_or_none, response_text

logger = logging.getLogger(__name__)

# Judge-graded rubric items — ids and wording mirror evals/rubrics/summary_rubric.md
MUST_JUDGE_ITEMS = [
    {
        "id": "M4",
        "text": "Every item presented as a decision in the generated output is "
        "supported by the transcript — no hallucinated decisions. Quote the "
        "supporting transcript line as evidence.",
    },
    {
        "id": "M5",
        "text": "Personal or banter content (jokes, appearance, off-topic remarks) "
        "does not appear under Decisions or Action Items in the generated output.",
    },
]
SHOULD_JUDGE_ITEMS = [
    {
        "id": "S1",
        "text": "The title (if any) names the dominant topic or client rather than "
        "a generic label like 'Weekly Sync' or 'Meeting Summary'.",
    },
    {
        "id": "S2",
        "text": "The summary opens with a 2-6 sentence narrative overview rather "
        "than jumping straight into bullet fragments.",
    },
    {
        "id": "S3",
        "text": "Action items name an owner whenever the transcript states one.",
    },
    {
        "id": "S4",
        "text": "Insights are synthesis (implications, risks, connections), not "
        "restatements of discussion points.",
    },
]


def parse_output(output: str) -> tuple[str, str]:
    """Parse the finalization output with the same parser the full pipeline
    uses (vendored in harness.finalization_parsing): returns (title, summary).
    Legacy free-text outputs fall back to heading-derived titles the same
    way production does."""
    from evals.harness.finalization_parsing import parse_finalization_response

    parsed = parse_finalization_response(output)
    title = parsed.get("title") or ""
    summary = parsed.get("summary") or ""
    if not title:
        for line in summary.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# Meeting Summary:"):
                title = stripped.replace("# Meeting Summary:", "").strip()
                break
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break
            # Skip preamble/body text and keep scanning: a heading-derived
            # title may appear after narrative preamble in legacy outputs.
            continue
    return title, summary


def has_section(output: str, *names: str) -> bool:
    """A section exists as a markdown heading or bold label naming it."""
    for name in names:
        if re.search(
            rf"^(#{{1,4}}\s+.*{name}|\*\*[^*\n]*{name}[^*\n]*\*\*)",
            output or "",
            re.IGNORECASE | re.MULTILINE,
        ):
            return True
    return False


class SummaryRunner:
    name = "summary"
    prompt_name = "meeting_finalize"

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult:
        gold_summary = fixture["gold"]["summary"]
        meeting = fixture["meeting"]
        transcript = meeting["transcript"]

        if offline:
            from app.services.prompt_loader import prompt_sha

            replay_text, reason = replay_or_none(
                fixture, self.name, prompt_sha(self.prompt_name)
            )
            if replay_text is None:
                return TaskResult.skip(fixture["id"], reason)
            output = replay_text
        else:
            try:
                output = await self._run_live(fixture, client)
            except Exception as e:
                # A transient API error on one fixture must not abort the
                # whole run; record a skip and keep going.
                return TaskResult.skip(fixture["id"], f"finalization call failed: {e}")
            if not output.strip():
                return TaskResult.skip(fixture["id"], "empty finalization output")

        # --- Deterministic MUST items -----------------------------------
        title, summary = parse_output(output)
        graded_text = f"{title}\n{summary}" if title else summary
        title_failures = check_title(title, gold_summary)
        keyword_checks = check_summary_keywords(graded_text, gold_summary)

        must: dict[str, bool] = {
            "M1": not title_failures,
            "M2": has_section(summary, "decision")
            and has_section(summary, "action item", "next step"),
            "M3": not keyword_checks["mention_failures"]
            and not keyword_checks["forbidden_hits"],
        }

        # --- Judge items --------------------------------------------------
        judge_verdicts: dict = {}
        should: dict[str, bool] = {}
        if offline or client is None:
            judge_skipped = True
        else:
            judge_skipped = False
            try:
                judge_verdicts = await judge_rubric_items(
                    client, transcript, output, MUST_JUDGE_ITEMS + SHOULD_JUDGE_ITEMS
                )
            except Exception as e:
                return TaskResult.skip(fixture["id"], f"judge call failed: {e}")
            for item in MUST_JUDGE_ITEMS:
                must[item["id"]] = judge_verdicts[item["id"]]["pass"]
            for item in SHOULD_JUDGE_ITEMS:
                should[item["id"]] = judge_verdicts[item["id"]]["pass"]

        consistency = summary_names_grounded(
            summary, fixture["gold"].get("entities"), transcript
        )

        scores = {
            "must_pass_rate": sum(must.values()) / len(must),
            "must_failed": sorted(k for k, v in must.items() if not v),
            "consistency_violations": len(consistency),
        }
        if should:
            scores["should_pass_rate"] = sum(should.values()) / len(should)

        return TaskResult(
            fixture_id=fixture["id"],
            raw_output=output,
            scores={k: v for k, v in scores.items() if not isinstance(v, list)},
            details={
                "title": title,
                "title_failures": title_failures,
                "keyword_checks": keyword_checks,
                "must": must,
                "should": should,
                "judge_skipped": judge_skipped,
                "judge_verdicts": judge_verdicts,
                "must_failed": scores["must_failed"],
                "consistency": consistency,
                "raw_output": output,
            },
        )

    async def _run_live(self, fixture: dict, client: Any) -> str:
        from app.services.prompt_loader import load_prompt

        meeting = fixture["meeting"]
        # Minimal fresh-state stand-in for MeetingState.body. Deliberately
        # titled "Untitled Meeting": 61% of production meetings arrive with no
        # Recall title, and deriving one from content is exactly what this
        # task measures — leaking the fixture's title_context would mask it.
        state_body = (
            "# Untitled Meeting\n\n"
            f"Participants: {', '.join(meeting.get('participants') or [])}"
        )
        prompt = load_prompt("meeting_finalize").format(
            current_state_body=state_body,
            full_transcript=meeting["transcript"],
        )
        # Mirrors StateSync.finalize_meeting_with_transcript: default model
        # (settings.CLAUDE_SONNET_MODEL), temperature 0.3, max_tokens 4000.
        response = await client.generate_message(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0.3,
            operation="eval_meeting_finalize",
        )
        return response_text(response) or ""
