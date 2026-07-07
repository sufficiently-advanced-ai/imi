"""Issue #909 — First-class SUPERSEDES edges in the graph.

Task 15: write_supersedes_edge on SignalGraphWriter
Task 16: wiring into chat_tools.update_signal supersede review path
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import Signal


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_signal(**overrides) -> Signal:
    fields: dict = dict(
        id="sig-old-1",
        type="decision",
        content="Original decision.",
        source_meeting_id="bot-909",
        source_timestamp="2026-06-01T00:00:00+00:00",
        tenant_id="tenant-acme",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


class _FakeClient:
    """Minimal Neo4j client substitute that records calls."""

    def __init__(self, *, return_row: bool = True, raise_exc: Exception | None = None):
        self.writes: list[tuple[str, dict]] = []
        self._return_row = return_row
        self._raise_exc = raise_exc

    async def execute_write(self, query: str, params: dict):
        self.writes.append((query, params))
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._return_row:
            # Simulate one row returned (node existed, edge merged)
            return [{"id": params.get("old_id", "x")}]
        return []  # empty → nodes not found


# ===========================================================================
# Task 15 — write_supersedes_edge
# ===========================================================================


class TestWriteSupersedes:
    """Unit tests for SignalGraphWriter.write_supersedes_edge."""

    # ----------------------------------------------------------------
    # 15-A  Cypher content: must contain MERGE + :SUPERSEDES
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cypher_contains_merge_and_supersedes(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_supersedes_edge(
            "sig-new-1",
            "sig-old-1",
            superseded_at="2026-06-11T10:00:00+00:00",
        )
        assert client.writes, "No execute_write calls issued"
        query, _ = client.writes[0]
        assert "MERGE" in query, "Cypher must use MERGE for idempotency"
        assert "SUPERSEDES" in query, "Cypher must create :SUPERSEDES relationship"

    # ----------------------------------------------------------------
    # 15-B  Params: new_id, old_id, superseded_at, actor, tenant_id all sent
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_params_include_all_required_keys(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_supersedes_edge(
            "sig-new-1",
            "sig-old-1",
            superseded_at="2026-06-11T10:00:00+00:00",
            actor="reviewer-bob",
            tenant_id="tenant-acme",
        )
        _, params = client.writes[0]
        assert params["new_id"] == "sig-new-1"
        assert params["old_id"] == "sig-old-1"
        assert params["superseded_at"] == "2026-06-11T10:00:00+00:00"
        assert params["actor"] == "reviewer-bob"
        assert params["tenant_id"] == "tenant-acme"

    # ----------------------------------------------------------------
    # 15-C  Returns True when the fake yields a row (both nodes existed)
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_true_when_row_returned(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient(return_row=True)
        writer = SignalGraphWriter(client)
        result = await writer.write_supersedes_edge(
            "sig-new-1",
            "sig-old-1",
            superseded_at="2026-06-11T10:00:00+00:00",
        )
        assert result is True

    # ----------------------------------------------------------------
    # 15-D  Returns False when no row is returned (node not found)
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row_returned(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient(return_row=False)
        writer = SignalGraphWriter(client)
        result = await writer.write_supersedes_edge(
            "sig-new-missing",
            "sig-old-missing",
            superseded_at="2026-06-11T10:00:00+00:00",
        )
        assert result is False

    # ----------------------------------------------------------------
    # 15-E  Returns False + never raises when the client raises
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_false_and_does_not_raise_on_client_error(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient(raise_exc=RuntimeError("Neo4j connection refused"))
        writer = SignalGraphWriter(client)
        # Must not raise
        result = await writer.write_supersedes_edge(
            "sig-new-1",
            "sig-old-1",
            superseded_at="2026-06-11T10:00:00+00:00",
        )
        assert result is False

    # ----------------------------------------------------------------
    # 15-F  Optional params default to None when not provided
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_optional_params_default_to_none(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = _FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_supersedes_edge(
            "sig-new-1",
            "sig-old-1",
            superseded_at="2026-06-11T10:00:00+00:00",
        )
        _, params = client.writes[0]
        assert params["actor"] is None
        assert params["tenant_id"] is None


# ===========================================================================
# Task 16 — governance wiring in chat_tools.update_signal
# ===========================================================================


class TestUpdateSignalSupersedes:
    """End-to-end tests for the SUPERSEDES edge wiring in update_signal."""

    def _build_patches(
        self, fake_signal, tmp_path, *, spy_supersedes: list | None = None
    ):
        """Return the context-manager stack used by most tests.

        spy_supersedes: if provided, write_supersedes_edge calls are appended here.
        spy_update_props: write calls for update_signal_properties go here.
        """
        from app.models.signal import MeetingSignals
        from app.services.signal_audit import SignalAuditStore

        fake_container = MeetingSignals(
            meeting_id="bot-909",
            bot_id="bot-909",
            signals=[fake_signal],
            signal_count=1,
        )

        mock_store = MagicMock()
        mock_store.find_signal_by_id.return_value = (fake_signal, fake_container)
        mock_store.replace_signal = MagicMock()

        audit_store = SignalAuditStore(
            audit_dir=tmp_path / "signals" / "audit",
            repo_root=tmp_path,
        )

        supersedes_calls: list[dict] = [] if spy_supersedes is None else spy_supersedes
        update_calls: list[dict] = []

        async def _spy_update_props(signal_id: str, **kwargs):
            update_calls.append({"signal_id": signal_id, **kwargs})
            return True

        async def _spy_supersedes(
            new_id: str,
            old_id: str,
            *,
            superseded_at: str,
            actor: str | None = None,
            tenant_id: str | None = None,
        ):
            # Mirrors write_supersedes_edge's exact signature so a drift in
            # positional order or keyword-only params fails this test.
            supersedes_calls.append(
                {
                    "new_id": new_id,
                    "old_id": old_id,
                    "superseded_at": superseded_at,
                    "actor": actor,
                    "tenant_id": tenant_id,
                }
            )
            return True

        mock_writer = MagicMock()
        mock_writer.update_signal_properties = _spy_update_props
        mock_writer.write_supersedes_edge = _spy_supersedes

        mock_neo4j_client = MagicMock()  # truthy → if client: branch runs

        return {
            "mock_store": mock_store,
            "audit_store": audit_store,
            "mock_writer": mock_writer,
            "mock_neo4j_client": mock_neo4j_client,
            "supersedes_calls": supersedes_calls,
            "update_calls": update_calls,
        }

    # ----------------------------------------------------------------
    # 16-A  supersede → write_supersedes_edge called with correct args
    # ----------------------------------------------------------------


    # ----------------------------------------------------------------
    # 16-B  superseded_at == the signal's valid_to (set by apply_review)
    # ----------------------------------------------------------------


    # ----------------------------------------------------------------
    # 16-C  actor and tenant_id are forwarded
    # ----------------------------------------------------------------


    # ----------------------------------------------------------------
    # 16-D  Non-supersede review (confirm) → write_supersedes_edge NOT called
    # ----------------------------------------------------------------


    # ----------------------------------------------------------------
    # 16-E  writer.write_supersedes_edge raising → still returns success (best-effort)
    # ----------------------------------------------------------------

