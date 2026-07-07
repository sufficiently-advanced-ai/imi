import pytest
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


@pytest.mark.asyncio
async def test_entities_created_before_signals_written():
    order = []

    class _Graph:
        async def add_node(self, entity_type, name, entity_id=None, properties=None):
            order.append(("add_node", entity_id))
            return True
        async def create_semantic_relationship(self, **kw):
            order.append(("rel", None))
            return True

    class _Writer:
        async def write_meeting_signals(self, ms):
            order.append(("write_signals", None))
            return len(ms.signals)

    orch = IngestOrchestrator(
        classifier=None, claude_client=None, graph=_Graph(),
        signal_writer=_Writer(), git_ops=None, tools={},
    )

    class _State:
        participants = ["Jane Okoye"]
        entities_mentioned = {"client": ["Acme Corp"], "stakeholder": ["Jane Okoye"]}

    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        Signal(id="s1", type="action_item", content="Send the deck",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
               entities=[EntityRef(id="client-acme-corp", type="client", name="Acme Corp")],
               client_id="client-acme-corp"),
    ])

    await orch._phase_enrich_graph(ms, "Acme Corp discussion. Jane Okoye attended.", _State())

    add_idxs = [i for i, (op, _) in enumerate(order) if op == "add_node"]
    sig_idxs = [i for i, (op, _) in enumerate(order) if op == "write_signals"]
    assert add_idxs, "expected add_node calls"
    assert sig_idxs, "expected write_meeting_signals call"
    # Every entity node must be created before signals are written
    assert max(add_idxs) < min(sig_idxs), f"entities must be added before signals; order={order}"
