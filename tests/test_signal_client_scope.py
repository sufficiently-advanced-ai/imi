from app.models.signal import Signal


def test_signal_carries_client_id():
    s = Signal(id="x", type="action_item", content="Send proposal",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
               client_id="client-acme-corp")
    assert s.client_id == "client-acme-corp"
    assert Signal.model_validate(s.model_dump()).client_id == "client-acme-corp"


def test_signal_client_id_optional():
    s = Signal(id="x", type="insight", content="Market is shifting",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00")
    assert s.client_id is None
