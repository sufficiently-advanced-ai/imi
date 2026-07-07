"""Relationships eval runner — exercises InferRelationshipsTool.

The tool receives the fixture's GOLD entities as its entity list (the way the
pipeline passes resolved entities), isolating relationship-extraction quality
from entity-extraction errors. Predicted triples are scored by canonical
triple matching; RELATED_TO coercions count as unknown-predicate FPs by
design (see matching.PREDICATE_MAP).
"""

from __future__ import annotations

import logging
from typing import Any

from ..consistency import relationship_endpoints_known
from ..matching import match_relationships
from ..scoring import score_relationships
from .base import TaskResult, replay_or_none

logger = logging.getLogger(__name__)


def _gold_entity_list(fixture: dict) -> list[dict]:
    return [
        {"id": g["canonical_id"], "name": g["canonical_name"], "type": g["type"]}
        for g in fixture["gold"].get("entities") or []
    ]


def _to_predicted(relationships: list[dict]) -> list[dict]:
    return [
        {
            "subject": r.get("source", ""),
            "predicate": r.get("type", ""),
            "object": r.get("target", ""),
            "evidence": r.get("evidence", ""),
        }
        for r in relationships
    ]


class RelationshipsRunner:
    name = "relationships"
    prompt_name = "extract_relationships"

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult:
        gold = fixture["gold"]
        gold_rels = gold.get("relationships") or []
        forbidden = gold.get("forbidden_relationships") or []
        gold_entities = gold.get("entities") or []

        if offline:
            replay_text, reason = replay_or_none(fixture, self.name, None)
            if replay_text is None:
                return TaskResult.skip(fixture["id"], reason)
            predicted, raw_output = self._parse_replay(replay_text), replay_text
        else:
            try:
                relationships, raw_output = await self._run_live(fixture, client)
            except ImportError as e:
                # Only a missing tool import means "unavailable"; genuine
                # runner/tool errors must surface, not be hidden as skips.
                return TaskResult.skip(fixture["id"], f"tool unavailable: {e}")
            predicted = _to_predicted(relationships)

        match = match_relationships(predicted, gold_rels, forbidden, gold_entities)
        scores = score_relationships(match)
        consistency = relationship_endpoints_known(predicted, gold_entities)
        scores["consistency_violations"] = len(consistency)
        return TaskResult(
            fixture_id=fixture["id"],
            raw_output=raw_output,
            scores=scores,
            details={
                "predicted": predicted,
                "matched": match.matched,
                "false_positives": match.false_positives,
                "trap_hits": match.trap_hits,
                "missed_required": match.missed_required,
                "unknown_predicates": match.unknown_predicates,
                "consistency": consistency,
                "raw_output": raw_output,
            },
        )

    async def _run_live(self, fixture: dict, client: Any):
        from app.services.tools.infer_relationships import InferRelationshipsTool

        tool = InferRelationshipsTool(
            claude_client=client, git_ops=None, file_cache=None
        )
        result = await tool.execute(
            {
                "content": fixture["meeting"]["transcript"],
                "entities": _gold_entity_list(fixture),
            }
        )
        if not result.success:
            raise RuntimeError(result.error or "infer_relationships failed")
        relationships = result.data.get("relationships", [])
        raw_output = "\n".join(
            f"{r['source']} -[{r['type']}]-> {r['target']}  ({r.get('evidence', '')})"
            for r in relationships
        )
        return relationships, raw_output

    @staticmethod
    def _parse_replay(replay_text: str) -> list[dict]:
        """Parse a recorded raw YAML response with the production parser."""
        from app.services.tools.yaml_utils import extract_yaml_block, parse_yaml_list

        yaml_content = extract_yaml_block(replay_text)
        raw = parse_yaml_list(yaml_content, "relationships")
        return _to_predicted([r for r in raw if isinstance(r, dict)])
