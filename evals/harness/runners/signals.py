"""Signals eval runner — exercises the production SignalPromoter LLM path.

Live mode builds an Observation exactly the way the ingest pipeline does
(IngestOrchestrator._build_observation_body) and calls SignalPromoter.promote
with a real ClaudeClient. Offline mode re-scores a recorded llm_response
through the same parser, skipping when the prompt has changed since recording.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ..consistency import signal_decision_language_mismatch
from ..loader import get_replay  # noqa: F401  (re-exported for tests)
from ..matching import match_signals
from ..scoring import score_signals
from .base import TaskResult, replay_or_none

logger = logging.getLogger(__name__)


def _entities_mentioned_from_gold(fixture: dict) -> dict:
    """Build the entities_mentioned dict the promoter expects, from gold
    entities when labeled, else from participants only."""
    mentioned: dict[str, list[str]] = {}
    for g in fixture["gold"].get("entities") or []:
        mentioned.setdefault(g["type"], []).append(g["canonical_name"])
    if "person" not in mentioned:
        mentioned["person"] = list(fixture["meeting"].get("participants") or [])
    return mentioned


def build_observation(fixture: dict):
    """Construct an Observation matching what the ingest pipeline produces."""
    from app.models.observation import Observation
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    meeting = fixture["meeting"]
    title = meeting.get("title_context") or "Eval meeting"
    participants = meeting.get("participants") or []
    body = IngestOrchestrator._build_observation_body(
        title, meeting["transcript"], participants
    )
    occurred = datetime.fromisoformat(meeting.get("date", "2026-01-01")).replace(
        tzinfo=UTC
    )
    return Observation(
        observation_id=f"eval-{uuid.uuid5(uuid.NAMESPACE_DNS, fixture['id'])}",
        external_id=f"eval-{fixture['id']}",
        source="eval",
        observed_at=occurred,
        entities_mentioned=_entities_mentioned_from_gold(fixture),
        content=body,
        raw_content=meeting["transcript"],
        is_finalized=True,
        status="completed",
        title=title,
        occurred_at=occurred,
        participants=participants,
        update_count=1,
    )


class SignalsRunner:
    name = "signals"
    prompt_name = "signal_promote"

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult:
        gold = fixture["gold"]
        gold_signals = gold.get("signals") or []
        forbidden = gold.get("forbidden_signals") or []

        if offline:
            from app.services.prompt_loader import prompt_sha

            replay_text, reason = replay_or_none(
                fixture, self.name, prompt_sha(self.prompt_name)
            )
            if replay_text is None:
                return TaskResult.skip(fixture["id"], reason)
            predicted, raw_output = self._parse_replay(replay_text), replay_text
        else:
            predicted, raw_output = await self._run_live(fixture, client)
            if predicted is None:
                return TaskResult.skip(fixture["id"], "promoter returned no signals")

        match = match_signals(predicted, gold_signals, forbidden)
        scores = score_signals(match)
        consistency = signal_decision_language_mismatch(predicted)
        scores["consistency_violations"] = len(consistency)
        return TaskResult(
            fixture_id=fixture["id"],
            raw_output=raw_output,
            scores=scores,
            details={
                "predicted": predicted,
                "matched": match.matched,
                "type_errors": match.type_errors,
                "false_positives": match.false_positives,
                "trap_hits": match.trap_hits,
                "missed_required": match.missed_required,
                "consistency": consistency,
                "raw_output": raw_output,
            },
        )

    async def _run_live(self, fixture: dict, client: Any):
        from app.services.signal_promoter import SignalPromoter

        observation = build_observation(fixture)
        promoter = SignalPromoter(claude_client=client, knowledge_graph=None)
        meeting_signals = await promoter.promote(observation)
        if meeting_signals is None:
            # Sentinel: propagate None so the caller's skip path is reachable
            # (an empty list is a real "no signals found" result, not a skip).
            return None, ""
        predicted = [
            {
                "type": s.type,
                "content": s.content,
                "confidence": s.confidence,
                "owner": s.owner.name if s.owner else None,
            }
            for s in meeting_signals.signals
        ]
        raw_output = "\n".join(f"[{p['type']}] {p['content']}" for p in predicted)
        return predicted, raw_output

    @staticmethod
    def _parse_replay(replay_text: str) -> list[dict]:
        """Parse a recorded raw LLM response with the production parser."""
        from app.services.signal_promoter import SignalPromoter

        raw = SignalPromoter._parse_llm_signals(replay_text)
        if raw is None:
            return []
        return [
            {
                "type": (r.get("type") or "").strip().lower(),
                "content": r.get("content") or "",
                "confidence": r.get("confidence"),
                "owner": r.get("owner"),
            }
            for r in raw
            if isinstance(r, dict)
        ]
