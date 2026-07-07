import pytest
from app.models.signal import MeetingSignals, Signal
from app.services import chat_tools


@pytest.mark.asyncio
async def test_search_signals_filters_by_client(monkeypatch):
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        Signal(id="a", type="action_item", content="acme task", status="open",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
               client_id="client-acme-corp"),
        Signal(id="b", type="action_item", content="globex task", status="open",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
               client_id="client-globex"),
    ])
    monkeypatch.setattr(chat_tools.SignalStore, "load_all", lambda self: [ms])
    monkeypatch.setattr(chat_tools, "get_knowledge_graph", lambda: None)

    acme = await chat_tools.search_signals(client_id="client-acme-corp")
    assert {s["id"] for s in acme} == {"a"}
    assert acme[0]["client_id"] == "client-acme-corp"

    # No client_id → both returned
    all_sigs = await chat_tools.search_signals()
    assert {s["id"] for s in all_sigs} == {"a", "b"}
