"""Meeting synthesis: the finalize envelope parser, the summarized document
format (summary body + transcript once, extraction body rebuilt on parse),
the ingest SYNTHESIZE phase, and the backfill script's frontmatter guard."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.observation import Observation, build_observation_body
from app.services import meeting_synthesis as ms
from app.services.orchestrators.ingest_orchestrator import PHASES, IngestOrchestrator

SUMMARY = (
    "## Summary\nSarah and David planned the Northwind work.\n\n"
    "## Key Discussion Points\n- **Northwind** is next\n- Snowflake-based AI\n\n"
    "## Decisions\n- Start Northwind tomorrow\n\n"
    "## Action Items\n- [David] send the PDF\n\n"
    "## Next Steps\nNone\n\n"
    "## Insights\nNone"
)
TRANSCRIPT = "Sarah Chen: How we doing?\nDavid Kim: Good. Northwind next?"


def _envelope(**kw):
    data = {"title": "Northwind kickoff with David", "purpose": "Plan the Northwind work.",
            "summary_markdown": SUMMARY, "entities_mentioned": []}
    data.update(kw)
    return json.dumps(data)


def _response(text):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _obs(**kw):
    participants = kw.pop("participants", ["Sarah Chen", "David Kim"])
    title = kw.pop("title", "idea")
    base = dict(
        observation_id="ingest-1", external_id="ingest-abc", entities_mentioned={},
        observed_at=datetime(2026, 9, 28, 17, 30, tzinfo=UTC),
        occurred_at=datetime(2026, 9, 28, 17, 30, tzinfo=UTC),
        title=title, participants=participants, raw_content=TRANSCRIPT,
        content=build_observation_body(title, TRANSCRIPT, participants),
    )
    base.update(kw)
    return Observation(**base)


# ---- parser -------------------------------------------------------------------


def test_parser_returns_purpose_and_prefixes_it():
    parsed = ms.parse_finalization_response(_envelope())
    assert parsed["purpose"] == "Plan the Northwind work."
    assert parsed["summary"].startswith("*Plan the Northwind work.*\n\n## Summary")
    assert parsed["title"] == "Northwind kickoff with David"


def test_parser_repairs_missing_sections():
    parsed = ms.parse_finalization_response(_envelope(summary_markdown="## Summary\nShort."))
    assert all(f"## {s}" in parsed["summary"] for s in ms.FINALIZATION_SECTIONS)


def test_eval_harness_uses_the_production_parser():
    from evals.harness import finalization_parsing

    assert finalization_parsing.parse_finalization_response is ms.parse_finalization_response


def test_key_points_are_the_discussion_bullets():
    assert ms.extract_key_points(SUMMARY) == ["Northwind is next", "Snowflake-based AI"]
    assert ms.extract_key_points("## Key Discussion Points\nNone\n\n## Decisions\n- x") == []


@pytest.mark.asyncio
async def test_synthesize_meeting_parses_the_response():
    client = SimpleNamespace(generate_message=AsyncMock(return_value=_response(_envelope())))
    out = await ms.synthesize_meeting(client, TRANSCRIPT, title="idea", participants=["David"])
    assert out.key_points == ["Northwind is next", "Snowflake-based AI"]
    assert out.prompt.startswith("meeting_finalize/")
    kwargs = client.generate_message.await_args.kwargs
    assert kwargs["operation"] == "meeting_summary"
    assert TRANSCRIPT in kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_unparseable_response_is_not_a_summary():
    client = SimpleNamespace(generate_message=AsyncMock(return_value=_response("Sorry, no.")))
    assert await ms.synthesize_meeting(client, TRANSCRIPT) is None
    assert await ms.synthesize_meeting(client, "   ") is None


# ---- document format --------------------------------------------------------


def _summarized():
    obs = _obs()
    obs.summary = "*Plan the Northwind work.*\n\n" + SUMMARY
    obs.purpose = "Plan the Northwind work."
    obs.summary_prompt = "meeting_finalize/abc"
    obs.key_points = ["Northwind is next"]
    return obs


def test_summarized_document_keeps_the_transcript_once():
    md = _summarized().to_markdown()
    assert md.count("Northwind next?") == 1
    assert "## Discussion" not in md
    assert "summary_prompt: meeting_finalize/abc" in md
    assert "- David Kim\n\n*Plan the Northwind work.*" in md


def test_parse_rebuilds_the_extraction_body_from_the_transcript():
    obs = _summarized()
    parsed = Observation.from_markdown(obs.to_markdown())
    # Signals are promoted from content: it must be exactly what BUILD_MEETING built.
    assert parsed.content == obs.content
    assert parsed.summary == obs.summary
    assert parsed.purpose == "Plan the Northwind work."
    assert parsed.key_points == ["Northwind is next"]
    # And a second round trip is stable.
    assert Observation.from_markdown(parsed.to_markdown()).to_markdown() == parsed.to_markdown()


def test_unsummarized_document_is_unchanged():
    md = _obs().to_markdown()
    assert "## Discussion" in md and "summary_prompt" not in md
    parsed = Observation.from_markdown(md)
    assert parsed.summary is None and parsed.content.startswith("# idea")


def test_meeting_reader_serves_the_summary_as_body():
    from app.models.meeting.state import MeetingState

    state = MeetingState.from_markdown(_summarized().to_markdown())
    assert "## Key Discussion Points" in state.body
    assert state.transcript.strip() == TRANSCRIPT


# ---- SYNTHESIZE phase -------------------------------------------------------


def _orch(response_text=None, error=None):
    gen = AsyncMock(side_effect=error) if error else AsyncMock(return_value=_response(response_text))
    return IngestOrchestrator(classifier=None, claude_client=SimpleNamespace(generate_message=gen),
                              graph=None, signal_writer=None, git_ops=None, tools={}), gen


def test_phase_sits_between_build_and_extraction():
    assert PHASES.index("BUILD_MEETING") < PHASES.index("SYNTHESIZE") < PHASES.index("EXTRACT_ENTITIES")


@pytest.mark.asyncio
async def test_phase_writes_summary_and_keeps_extraction_body():
    orch, _ = _orch(_envelope())
    obs = _obs()
    before = obs.content
    assert await orch._phase_synthesize(SimpleNamespace(title="idea"), obs, "call_transcript")
    assert obs.summary.startswith("*Plan the Northwind work.*")
    assert obs.key_points == ["Northwind is next", "Snowflake-based AI"]
    assert obs.title == "idea"  # caller's title wins
    assert obs.content == before


@pytest.mark.asyncio
async def test_model_title_replaces_only_the_placeholder():
    orch, _ = _orch(_envelope())
    obs = _obs(title="Ingested call_transcript")
    await orch._phase_synthesize(SimpleNamespace(title=None), obs, "call_transcript")
    assert obs.title == "Northwind kickoff with David"
    assert obs.content.startswith("# Northwind kickoff with David")
    assert Observation.from_markdown(obs.to_markdown()).content == obs.content


@pytest.mark.asyncio
@pytest.mark.parametrize("lane,ctype", [("library", "call_transcript"), ("record", "email_thread")])
async def test_phase_skips_non_meetings(lane, ctype):
    orch, gen = _orch(_envelope())
    obs = _obs(lane=lane)
    assert not await orch._phase_synthesize(SimpleNamespace(title="t"), obs, ctype)
    gen.assert_not_called()
    assert obs.summary is None


@pytest.mark.asyncio
async def test_phase_failure_is_not_fatal():
    orch, _ = _orch(error=RuntimeError("endpoint down"))
    obs = _obs()
    assert not await orch._phase_synthesize(SimpleNamespace(title="t"), obs, "call_transcript")
    assert obs.summary is None


# ---- backfill guard ---------------------------------------------------------


def _backfill():
    path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_meeting_synthesis.py"
    spec = importlib.util.spec_from_file_location("backfill_meeting_synthesis", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_refuses_to_drop_unmodelled_frontmatter():
    bf = _backfill()
    old = _obs().to_markdown().replace("status: completed", "status: completed\nduration: 42")
    new = _summarized().to_markdown()
    assert bf.frontmatter_loss(old, new) == ["duration"]
    assert bf.frontmatter_loss(_obs().to_markdown(), new) == []


def test_backfill_eligibility():
    bf = _backfill()
    assert bf.eligible(_obs(), force=False) is None
    assert bf.eligible(_summarized(), force=False) == "already summarized"
    assert bf.eligible(_summarized(), force=True) is None
    assert bf.eligible(_obs(lane="library"), force=False) == "library lane"
    assert bf.eligible(_obs(raw_content=None), force=False) == "no transcript"
