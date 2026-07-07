from app.models.signal import EntityRef, Signal
from app.services.signal_promoter import SignalPromoter


def _sig(entities, sid="s"):
    return Signal(id=sid, type="action_item", content="do the thing",
                  source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
                  entities=entities)


def test_client_id_from_own_entities(monkeypatch):
    # Make client-type detection domain-independent for the test
    monkeypatch.setattr(SignalPromoter, "_client_type_ids", staticmethod(lambda: {"client"}))
    promoter = SignalPromoter()
    sig = _sig([EntityRef(id="client-acme-corp", type="client", name="Acme Corp"),
                EntityRef(id="stakeholder-jane-doe", type="stakeholder", name="Jane Doe")])
    scoped = promoter._apply_client_scope([sig])
    assert scoped[0].client_id == "client-acme-corp"


def test_meeting_fallback_client_for_signal_without_client_ref(monkeypatch):
    monkeypatch.setattr(SignalPromoter, "_client_type_ids", staticmethod(lambda: {"client"}))
    promoter = SignalPromoter()
    with_client = _sig([EntityRef(id="client-acme-corp", type="client", name="Acme Corp")], sid="a")
    without = _sig([EntityRef(id="stakeholder-jane-doe", type="stakeholder", name="Jane Doe")], sid="b")
    scoped = promoter._apply_client_scope([with_client, without])
    # both scoped to the meeting's single dominant client
    assert all(s.client_id == "client-acme-corp" for s in scoped)


def test_no_fallback_when_multiple_clients(monkeypatch):
    monkeypatch.setattr(SignalPromoter, "_client_type_ids", staticmethod(lambda: {"client"}))
    promoter = SignalPromoter()
    a = _sig([EntityRef(id="client-acme-corp", type="client", name="Acme Corp")], sid="a")
    b = _sig([EntityRef(id="client-globex", type="client", name="Globex")], sid="b")
    c = _sig([EntityRef(id="stakeholder-jane-doe", type="stakeholder", name="Jane")], sid="c")
    scoped = promoter._apply_client_scope([a, b, c])
    assert scoped[0].client_id == "client-acme-corp"
    assert scoped[1].client_id == "client-globex"
    # ambiguous meeting (2 clients) → no fallback applied to the client-less signal
    assert scoped[2].client_id is None


def test_signal_with_multiple_clients_is_ambiguous(monkeypatch):
    monkeypatch.setattr(SignalPromoter, "_client_type_ids", staticmethod(lambda: {"client"}))
    promoter = SignalPromoter()
    sig = _sig([EntityRef(id="client-acme-corp", type="client", name="Acme"),
                EntityRef(id="client-globex", type="client", name="Globex")])
    scoped = promoter._apply_client_scope([sig])
    assert scoped[0].client_id is None
