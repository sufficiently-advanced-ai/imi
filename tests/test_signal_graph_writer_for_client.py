import pytest
from app.models.signal import MeetingSignals, Signal
from app.services.graph.signal_graph_writer import SignalGraphWriter


class _FakeClient:
    def __init__(self):
        self.writes = []
    async def execute_write(self, query, params):
        self.writes.append((query, params))
        return []


# ---------------------------------------------------------------------------
# T5: Governance field mirroring onto :Signal nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_signal_node_includes_governance_params():
    """_write_signal_node passes provenance/review/authority/tenant params to Neo4j."""
    client = _FakeClient()
    writer = SignalGraphWriter(client)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        Signal(
            id="s-gov-1",
            type="decision",
            content="Adopt governance ladder.",
            source_meeting_id="b1",
            source_timestamp="2026-06-05T10:00:00+00:00",
            provenance_status="observed",
            review_status="pending",
            can_use_as_evidence=True,
            can_use_as_instruction=False,
            tenant_id="tenant-abc",
        ),
    ])
    await writer.write_meeting_signals(ms)

    # Find the UPSERT_SIGNAL write call
    upsert_calls = [(q, p) for q, p in client.writes if "MERGE (s:Signal" in q]
    assert upsert_calls, "No UPSERT_SIGNAL write call found"
    _, params = upsert_calls[0]

    assert params.get("provenance_status") == "observed"
    assert params.get("review_status") == "pending"
    assert params.get("can_use_as_evidence") is True
    assert params.get("can_use_as_instruction") is False
    assert params.get("tenant_id") == "tenant-abc"


@pytest.mark.asyncio
async def test_update_signal_properties_mirrors_review_status():
    """update_signal_properties forwards review_status + provenance to the SET props."""
    client = _FakeClient()
    client.writes = [{"id": "s-gov-2"}]  # Simulate a found record

    class _FakeClientWithReturn:
        def __init__(self):
            self.writes = []
        async def execute_write(self, query, params):
            self.writes.append((query, params))
            return [{"id": params.get("id")}]  # Simulate found

    client2 = _FakeClientWithReturn()
    writer = SignalGraphWriter(client2)
    result = await writer.update_signal_properties(
        "s-gov-2",
        review_status="confirmed",
        provenance_status="user_confirmed",
        can_use_as_evidence=True,
        can_use_as_instruction=True,
    )
    assert result is True

    assert client2.writes, "No write calls issued"
    _, params = client2.writes[0]
    props = params.get("props", {})
    assert props.get("review_status") == "confirmed"
    assert props.get("provenance_status") == "user_confirmed"
    assert props.get("can_use_as_evidence") is True
    assert props.get("can_use_as_instruction") is True


@pytest.mark.asyncio
async def test_governance_fields_in_upsert_cypher():
    """The UPSERT_SIGNAL Cypher SET clause includes all five governance fields."""
    from app.services.graph.signal_graph_writer import _UPSERT_SIGNAL

    for field in (
        "provenance_status",
        "review_status",
        "can_use_as_evidence",
        "can_use_as_instruction",
        "tenant_id",
    ):
        assert f"${ field}" in _UPSERT_SIGNAL, (
            f"UPSERT_SIGNAL Cypher is missing governance field: {field}"
        )


@pytest.mark.asyncio
async def test_for_client_edge_written_when_client_id_present():
    client = _FakeClient()
    writer = SignalGraphWriter(client)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        Signal(id="s1", type="action_item", content="Send the deck",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00",
               client_id="client-acme-corp"),
    ])
    await writer.write_meeting_signals(ms)
    # A FOR_CLIENT merge was issued targeting the client entity
    assert any("FOR_CLIENT" in q and p.get("client_id") == "client-acme-corp"
               for q, p in client.writes)
    # client_id stored as a Signal property on the upsert
    assert any(p.get("client_id") == "client-acme-corp"
               for q, p in client.writes if "MERGE (s:Signal" in q)


@pytest.mark.asyncio
async def test_no_for_client_edge_when_client_id_absent():
    client = _FakeClient()
    writer = SignalGraphWriter(client)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        Signal(id="s2", type="insight", content="Market shift",
               source_meeting_id="b1", source_timestamp="2026-05-20T10:00:00+00:00"),
    ])
    await writer.write_meeting_signals(ms)
    assert not any("FOR_CLIENT" in q for q, p in client.writes)


@pytest.mark.asyncio
async def test_write_conflicts_with_edge_none_tenant_uses_ambient():
    """write_conflicts_with_edge with tenant_id=None falls back to ambient_tenant_id."""
    from unittest.mock import patch

    client = _FakeClient()
    writer = SignalGraphWriter(client)

    with patch(
        "app.services.graph.signal_graph_writer.ambient_tenant_id",
        return_value="ambient-tenant-xyz",
    ):
        await writer.write_conflicts_with_edge(
            "sig-aaa",
            "sig-zzz",
            confirmed_at="2026-06-12T00:00:00+00:00",
            tenant_id=None,
        )

    # Find the CONFLICTS_WITH write call
    conflicts_writes = [(q, p) for q, p in client.writes if "CONFLICTS_WITH" in q]
    assert conflicts_writes, "No CONFLICTS_WITH write issued"
    _, params = conflicts_writes[0]
    assert params.get("tenant_id") == "ambient-tenant-xyz", (
        "tenant_id should fall back to ambient_tenant_id() when not provided"
    )
