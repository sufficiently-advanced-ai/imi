"""SignalPromoter accepts the source-agnostic Observation (open-core P1a)."""

from datetime import UTC, datetime

import pytest

from app.models.observation import Observation
from app.services.signal_promoter import SignalPromoter


@pytest.fixture
def observation():
    return Observation(
        observation_id="ingest-obs1",
        external_id="ingest-bot1",
        observed_at=datetime(2026, 6, 4, 15, 0, tzinfo=UTC),
        title="Planning call",
        participants=["Sarah Chen"],
        entities_mentioned={"person": ["Sarah Chen"]},
        content=(
            "## Decisions\n- Decision: adopt the new pipeline\n\n"
            "## Action Items\n- [ ] Sarah Chen to draft the rollout plan\n"
        ),
    )


@pytest.mark.asyncio
async def test_promote_accepts_observation(observation):
    promoter = SignalPromoter(claude_client=None, knowledge_graph=None)
    result = await promoter.promote(observation)
    assert result is not None
    assert result.meeting_id == "ingest-obs1"
    assert result.bot_id == "ingest-bot1"
    assert result.meeting_title == "Planning call"
    assert result.signal_count == len(result.signals)
    # regex fallback should find both section types in the content
    assert any(s.type == "decision" for s in result.signals)
    assert any(s.type == "action_item" for s in result.signals)
    # source link uses the external id (same value bot_id carried before)
    assert all(s.source_meeting_id == "ingest-bot1" for s in result.signals)


def test_promoter_module_has_no_meetingstate_import():
    import app.services.signal_promoter as sp

    assert "MeetingState" not in dir(sp), (
        "signal_promoter must not import MeetingState (open-core P1a seam)"
    )
