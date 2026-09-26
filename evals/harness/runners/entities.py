"""Entities eval runner — exercises the shipping transcript extraction prompt.

Runs the production salient-entity extraction (prompt build, model call,
parser, and salience post-filter from app/services/salient_entity_extractor)
WITHOUT a resolver or registry — passing mentions are dropped, exactly like
an ingest where the mentioned entity doesn't already exist, so the eval
scores what would become graph nodes. Scoring is alias-driven matching plus a
canonicalization sub-metric (duplicate emissions of the same gold entity).

With EVAL_ENTITY_DECISIONS=1 (scripts/run_evals.py --decisions) the promoted
entities are additionally judged by the entity_admission decision model (Jev),
forced to mode "on", exactly as ingest would for new entities — so a run
with and without the flag measures what the decision layer adds.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..matching import match_entities
from ..scoring import score_entities
from .base import TaskResult, replay_or_none, response_text

logger = logging.getLogger(__name__)

# Entity types for the consulting_firm domain (config/domains/consulting_firm.yaml).
# Fixtures may override with gold-implied types but these are the prompt's menu.
DEFAULT_ENTITY_TYPES = ["person", "account", "project", "team"]


_DOMAIN_FILE = Path(__file__).resolve().parents[3] / "config" / "domains" / "consulting_firm.yaml"


def entity_type_descriptions() -> dict[str, str]:
    """Type descriptions from the consulting_firm domain — production renders
    these into the prompt (salient_entity_extractor), so the eval does too."""
    try:
        import yaml

        entities = (yaml.safe_load(_DOMAIN_FILE.read_text()) or {})["domain"]["entities"]
        return {t: (e or {}).get("description", "") or "" for t, e in entities.items()}
    except Exception as e:  # descriptions are context, never a hard dependency
        logger.warning("entity type descriptions unavailable: %s", e)
        return {}


def _existing_by_type(fixture: dict | None) -> dict[str, list[str]] | None:
    """meeting.existing_entities ([{type, name, context?}]) as the prompt's
    {type: [names]} — simulates a KB that already has these entities."""
    existing = ((fixture or {}).get("meeting") or {}).get("existing_entities") or []
    out: dict[str, list[str]] = {}
    for e in existing:
        out.setdefault(e["type"], []).append(e["name"])
    return out or None


def build_extraction_prompt(transcript: str, entity_types: list[str], fixture: dict | None = None) -> str:
    """Build the shipping extraction prompt the way the pipeline does. No
    existing-entities context (a fresh ingest) unless the fixture declares
    meeting.existing_entities."""
    from app.services.salient_entity_extractor import build_salient_extraction_prompt

    return build_salient_extraction_prompt(
        transcript, entity_types, _existing_by_type(fixture), entity_type_descriptions()
    )


def parse_promoted(text: str, entity_types: list[str]) -> list[dict]:
    """Production parser + salience post-filter (resolver=None)."""
    from app.services.salient_entity_extractor import (
        filter_salient_entities,
        parse_salient_entities,
    )

    labeled = parse_salient_entities(text or "", entity_types)
    return filter_salient_entities(labeled, resolver=None)


def parse_extraction_response(text: str, entity_types: list[str]) -> list[dict]:
    """Parse + salience-filter model output into [{name, type}] for scoring."""
    return [
        {"name": e["canonical_name"], "type": e["type"]}
        for e in parse_promoted(text, entity_types)
    ]


class _ForceOn:
    """Decision client view that reports mode "on" for every operation."""

    def __init__(self, client):
        self._client = client

    def mode(self, operation):
        return "on"

    async def decide(self, *args, **kwargs):
        return await self._client.decide(*args, **kwargs)


async def apply_admission(promoted: list[dict], fixture: dict, entity_types: list[str]) -> list[dict]:
    """Run the entity_admission decision layer over promoted entities and
    return [{name, type}] after drops/retypes."""
    from app.services.entity_admission import judge_entities
    from app.services.inference.decisions import get_decision_client

    descriptions = entity_type_descriptions()
    types = {t: descriptions.get(t, "") for t in entity_types}
    meeting = fixture.get("meeting") or {}
    verdicts = await judge_entities(
        [
            {"type": e["type"], "name": e["canonical_name"], "evidence": e.get("evidence"),
             "role": e.get("role"), "aliases_heard": e.get("aliases_heard")}
            for e in promoted
        ],
        types,
        meeting={"title": meeting.get("title_context"), "participants": meeting.get("participants")},
        client=_ForceOn(get_decision_client()),
    )
    admitted = []
    for e in promoted:
        verdict = verdicts.get((e["type"], e["canonical_name"]))
        if verdict is None:
            admitted.append({**e})
        elif verdict.action == "retype":
            admitted.append({**e, "type": verdict.new_type})

    # entity_link: every link verified (mentioned? the existing KB entity? a
    # fuller name?) against the fixture's simulated KB (meeting.existing_entities).
    from app.services.entity_linking import judge_links

    existing = {
        (x["type"], x["name"].lower()): x for x in meeting.get("existing_entities") or []
    }
    links = []
    for i, e in enumerate(admitted):
        link = {"id": f"e{i}", "type": e["type"], "name": e["canonical_name"],
                "names": e.get("aliases_heard") or [], "evidence": e.get("evidence"),
                "role": e.get("role")}
        hit = existing.get((e["type"], e["canonical_name"].lower()))
        if hit:
            link["candidate"] = {"name": hit["name"], "context": hit.get("context") or {}}
        links.append(link)
    link_verdicts = await judge_links(
        links, meeting.get("transcript", ""),
        meeting={"title": meeting.get("title_context"), "participants": meeting.get("participants")},
        client=_ForceOn(get_decision_client()),
    )
    out = []
    for i, e in enumerate(admitted):
        v = link_verdicts.get(f"e{i}")
        if v is None:
            out.append({"name": e["canonical_name"], "type": e["type"]})
        elif v.action in ("split", "rename"):
            out.append({"name": v.name, "type": e["type"]})
    return out


class EntitiesRunner:
    name = "entities"
    prompt_name = "transcript_entity_extract"

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult:
        gold = fixture["gold"]
        gold_entities = gold.get("entities") or []
        forbidden = gold.get("forbidden_entities") or []
        entity_types = sorted(
            set(DEFAULT_ENTITY_TYPES)
            | {g["type"] for g in gold_entities}
            | {f["type"] for f in forbidden if f.get("type")}
        )

        if offline:
            from app.services.prompt_loader import prompt_sha

            replay_text, reason = replay_or_none(
                fixture, self.name, prompt_sha(self.prompt_name)
            )
            if replay_text is None:
                return TaskResult.skip(fixture["id"], reason)
            raw_output = replay_text
        else:
            from app.config import settings

            prompt = build_extraction_prompt(
                fixture["meeting"]["transcript"], entity_types, fixture
            )
            response = await client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=3000,
                temperature=0.2,  # mirrors salient_entity_extractor
                operation="eval_entity_extraction",
            )
            raw_output = response_text(response) or ""

        if os.environ.get("EVAL_ENTITY_DECISIONS") == "1" and not offline:
            extracted = await apply_admission(
                parse_promoted(raw_output, entity_types), fixture, entity_types
            )
        else:
            extracted = parse_extraction_response(raw_output, entity_types)
        match = match_entities(extracted, gold_entities, forbidden)
        scores = score_entities(match)
        return TaskResult(
            fixture_id=fixture["id"],
            raw_output=raw_output,
            scores=scores,
            details={
                "extracted": extracted,
                "matched": match.matched,
                "duplicates": match.duplicates,
                "false_positives": match.false_positives,
                "trap_hits": match.trap_hits,
                "missed_required": match.missed_required,
                "raw_output": raw_output,
            },
        )
