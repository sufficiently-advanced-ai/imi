"""Profiles eval runner — exercises the entity narrative-profile prompts.

Measures attribution correctness for the person/project/team profile prompts
(app/prompts/{type}_update.xml). The bug this task guards against: a subject's
profile absorbing a *co-participant's* activities just because they shared a
meeting. Each fixture supplies a transcript containing several people's work,
plus a <grounded_facts> block listing only the subject's own signals; a correct
prompt builds a profile from the grounded facts and the transcript WITHOUT
attributing other participants' work to the subject.

Live mode replicates the production substitution in
DomainAwareEntityProcessor._generate_entity_profile (raw {type}_update.xml with
{{var}} replacement, default model, max_tokens 2048). Grading mirrors
evals/rubrics/profiles_rubric.md: deterministic MUST checks here, judgment
MUST/SHOULD items via harness.judge. Item ids mirror the rubric.
"""

from __future__ import annotations

import logging
from typing import Any

from ..judge import judge_rubric_items
from ..matching import check_profile_keywords
from .base import TaskResult, replay_or_none, response_text
from .summary import has_section

logger = logging.getLogger(__name__)

# Section whose presence PM2 checks, per entity type (the "activity" section
# most prone to attribution leakage).
_REQUIRED_SECTION: dict[str, tuple[str, ...]] = {
    "person": ("recent activit",),
    "project": ("recent development",),
    "team": ("recent activit",),
}

# Judge-graded rubric items — ids/wording mirror evals/rubrics/profiles_rubric.md
MUST_JUDGE_ITEMS = [
    {
        "id": "PM3",
        "text": "The generated output is a profile for a SINGLE subject (named "
        "in the output). No activity, decision, action item, or responsibility "
        "that the transcript attributes to a DIFFERENT participant may be "
        "attributed to this subject. If any is, fail and quote both the "
        "transcript line showing the true owner and the offending profile line.",
    },
    {
        "id": "PM4",
        "text": "Every activity, role, or relationship stated about the subject "
        "in the profile is supported by the transcript — no hallucinated facts. "
        "Quote the supporting transcript line as evidence.",
    },
]
SHOULD_JUDGE_ITEMS = [
    {
        "id": "PS1",
        "text": "Activities listed for the subject are the subject's own "
        "contributions, not the meeting's general agenda or another attendee's "
        "work.",
    },
    {
        "id": "PS2",
        "text": "Relationships or roles named for the subject (reports to, leads, "
        "works with) are supported by the transcript, not inferred from mere "
        "co-attendance.",
    },
]


class ProfilesRunner:
    name = "profiles"
    # Representative prompt for the flat prompt_shas[task] recorded by
    # run_evals. The runner selects the type-specific prompt per fixture; the
    # offline replay SHA below keys off that type-specific prompt.
    prompt_name = "person_update"

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult:
        gold = fixture["gold"]["profiles"]
        entity_type = gold["entity_type"]
        entity_id = gold["entity_id"]
        meeting = fixture["meeting"]
        transcript = meeting["transcript"]

        if offline:
            from app.services.prompt_loader import prompt_sha

            replay_text, reason = replay_or_none(
                fixture, self.name, prompt_sha(f"{entity_type}_update")
            )
            if replay_text is None:
                return TaskResult.skip(fixture["id"], reason)
            output = replay_text
        else:
            try:
                output = await self._run_live(fixture, client, gold)
            except Exception as e:
                return TaskResult.skip(fixture["id"], f"profile call failed: {e}")
            if not output.strip():
                return TaskResult.skip(fixture["id"], "empty profile output")

        # --- Deterministic MUST items -----------------------------------
        keyword_checks = check_profile_keywords(output, gold)
        required_sections = _REQUIRED_SECTION.get(entity_type, ())
        must: dict[str, bool] = {
            # PM1: grounded facts present, and zero co-participant misattributions
            "PM1": not keyword_checks["mention_failures"]
            and not keyword_checks["forbidden_attribution_hits"],
            # PM2: the type's activity section exists
            "PM2": (not required_sections) or has_section(output, *required_sections),
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

        # Attribution violations: deterministic forbidden hits + a failed PM3
        # (judge) when the judge ran. Trended, not gated (must_pass_rate gates).
        attribution_violations = len(keyword_checks["forbidden_attribution_hits"])
        if not judge_skipped and not must.get("PM3", True):
            attribution_violations += 1

        scores = {
            "must_pass_rate": sum(must.values()) / len(must),
            "attribution_violations": attribution_violations,
        }
        if should:
            scores["should_pass_rate"] = sum(should.values()) / len(should)

        return TaskResult(
            fixture_id=fixture["id"],
            raw_output=output,
            scores=scores,
            details={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "keyword_checks": keyword_checks,
                "must": must,
                "should": should,
                "judge_skipped": judge_skipped,
                "judge_verdicts": judge_verdicts,
                "must_failed": sorted(k for k, v in must.items() if not v),
                "raw_output": output,
            },
        )

    async def _run_live(self, fixture: dict, client: Any, gold: dict) -> str:
        """Replicate DomainAwareEntityProcessor._generate_entity_profile.

        Loads the RAW {type}_update.xml (production reads the whole file, not the
        <instructions> body) and substitutes the same {{vars}}. The fixture
        supplies grounded_facts (the subject's own signals/relationships) and an
        optional existing_profile; the transcript stands in for trigger files.
        """
        import html

        from app.services.prompt_loader import prompt_path

        entity_type = gold["entity_type"]
        entity_id = gold["entity_id"]
        template = prompt_path(f"{entity_type}_update").read_text(encoding="utf-8")

        # Mirror production substitution + content escaping (XML-tag safety).
        prompt = template.replace("{{entity_id}}", entity_id)
        prompt = prompt.replace(
            "{{existing_profile}}", html.escape(gold.get("existing_profile", ""))
        )
        prompt = prompt.replace(
            "{{trigger_files}}", html.escape(fixture["meeting"]["transcript"])
        )
        prompt = prompt.replace("{{recent_digests}}", "")
        prompt = prompt.replace(
            "{{grounded_facts}}", html.escape(gold.get("grounded_facts", ""))
        )

        # Mirrors production: default model, max_tokens 2048, no temperature
        # override. (System string is generic here — it is not what we measure.)
        response = await client.generate_message(
            messages=[{"role": "user", "content": prompt}],
            system="You are updating entity profiles for a knowledge management system.",
            max_tokens=2048,
            operation="eval_profile_generation",
        )
        return response_text(response) or ""
