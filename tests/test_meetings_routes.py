"""Meetings API: list ordered by event time, filters, paging, content with
summary/transcript/visible signals, and the legacy-body rules."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.observation import Observation, build_observation_body
from app.models.signal import MeetingSignals, Signal
from app.routes import meetings


def _write_meeting(d, bot, title, when, summary=None, participants=("Sarah Chen", "David Kim")):
    transcript = f"Sarah Chen: about {title}"
    obs = Observation(
        observation_id=f"obs-{bot}", external_id=bot, entities_mentioned={"account": ["Northwind"]},
        observed_at=datetime(2026, 9, 30, tzinfo=UTC), occurred_at=when,
        recorded_at=datetime(2026, 9, 30, tzinfo=UTC), time_source="explicit",
        title=title, participants=list(participants), raw_content=transcript,
        content=build_observation_body(title, transcript, list(participants)),
    )
    if summary:
        obs.summary, obs.purpose, obs.summary_prompt = summary, "Why we met.", "meeting_finalize/x"
        obs.key_points = ["Northwind is next"]
    (d / f"meeting-{bot}.md").write_text(obs.to_markdown())


def _write_signals(d, bot, *signals):
    ms = MeetingSignals(meeting_id=bot, bot_id=bot, meeting_title="t", extracted_at="2026-09-30",
                        signal_count=len(signals), signals=list(signals))
    (d / f"meeting-{bot}.json").write_text(ms.model_dump_json())


def _sig(sid, type_="decision", **meta):
    return Signal(id=sid, type=type_, content=f"signal {sid} content", source_meeting_id="b",
                  source_timestamp="2026-09-28T00:00:00+00:00", metadata=meta)


@pytest.fixture
def client(tmp_path, monkeypatch):
    mdir, sdir = tmp_path / "meetings", tmp_path / "signals"
    mdir.mkdir()
    sdir.mkdir()
    monkeypatch.setattr(meetings, "MEETINGS_DIR", mdir)
    monkeypatch.setattr(meetings, "SIGNALS_DIR", sdir)
    meetings._cache.clear()
    # Ingested later but happened earlier: order must follow start_time.
    _write_meeting(mdir, "ingest-old", "Globex check in", datetime(2026, 9, 24, 20, tzinfo=UTC))
    _write_meeting(mdir, "ingest-new", "Northwind planning", datetime(2026, 9, 28, 17, tzinfo=UTC),
                   summary="## Summary\nPlanned Northwind.\n\n## Decisions\n- Start Northwind")
    _write_signals(sdir, "ingest-new", _sig("a"), _sig("b", "action_item"),
                   _sig("c", duplicate_of="a"))
    app = FastAPI()
    app.include_router(meetings.router)
    return TestClient(app)


def test_list_is_newest_event_first_with_counts(client):
    body = client.get("/api/meetings/history/list").json()
    assert [i["bot_id"] for i in body["items"]] == ["ingest-new", "ingest-old"]
    new = body["items"][0]
    assert new["summarized"] and new["purpose"] == "Why we met."
    # c is shown under a: hidden from the counts
    assert new["signal_counts"] == {"decision": 1, "action_item": 1, "key_point": 0, "insight": 0}
    assert body["total"] == 2 and body["next_cursor"] is None


def test_filters_and_paging(client):
    assert client.get("/api/meetings/history/list?q=globex").json()["total"] == 1
    assert client.get("/api/meetings/history/list?q=northwind").json()["total"] == 2  # entity match
    r = client.get("/api/meetings/history/list?start_date=2026-09-25").json()
    assert [i["bot_id"] for i in r["items"]] == ["ingest-new"]
    page = client.get("/api/meetings/history/list?page_size=1").json()
    assert page["next_cursor"] == "1"
    rest = client.get("/api/meetings/history/list?page_size=1&cursor=1").json()
    assert rest["items"][0]["bot_id"] == "ingest-old"
    assert client.get("/api/meetings/history/list?start_date=nope").status_code == 422


def test_stats(client):
    s = client.get("/api/meetings/history/stats").json()
    assert s["total_meetings"] == 2 and s["meetings_summarized"] == 1
    assert s["total_signals"] == 2
    assert s["last_meeting"].startswith("2026-09-28")


def test_content_has_summary_transcript_and_visible_signals(client):
    c = client.get("/api/meetings/ingest-new/content").json()
    assert c["body"].startswith("## Summary") and c["summarized"]
    assert c["transcript"] == "Sarah Chen: about Northwind planning"
    assert [s["id"] for s in c["signals"]] == ["a", "b"]
    assert c["entity_counts"]["decisions"] == 1 and c["entity_counts"]["accounts"] == 1


def test_unsummarized_ingest_does_not_repeat_the_transcript_as_body(client):
    c = client.get("/api/meetings/ingest-old/content").json()
    assert c["body"] == "" and not c["summarized"]
    assert c["transcript"]


def test_legacy_body_is_shown(client, tmp_path):
    md = ("---\nmeeting_id: m\nbot_id: legacy\nupdated_at: 2026-03-06T16:57:26\ntitle: Demo\n"
          "entities_mentioned: {}\n---\n\n# Comprehensive Meeting Summary\n\n## Key Points\n- x\n"
          "\n## Full Transcript\n\nhello")
    (tmp_path / "meetings" / "meeting-legacy.md").write_text(md)
    c = client.get("/api/meetings/legacy/content").json()
    assert c["body"].startswith("# Comprehensive Meeting Summary")


def test_content_rejects_bad_ids_and_missing(client):
    assert client.get("/api/meetings/..%2Fetc/content").status_code in (400, 404)
    assert client.get("/api/meetings/nope/content").status_code == 404


def test_stats_fall_back_to_observed_at_for_undated_meetings(client, tmp_path):
    md = ("---\nmeeting_id: m\nbot_id: undated\nupdated_at: 2026-10-05T09:00:00+00:00\ntitle: Undated\n"
          "entities_mentioned: {}\n---\n\n# Undated\n\n## Full Transcript\n\nhi")
    (tmp_path / "meetings" / "meeting-undated.md").write_text(md)
    s = client.get("/api/meetings/history/stats").json()
    assert s["last_meeting"] == "2026-10-05T09:00:00+00:00"
