"""Issue #953: live-ingested signals must carry tenant_id (multi-tenant scoping).

Live ingestion builds Signal objects without tenant_id (model default None), so
Signal nodes land in the shared Neo4j with tenant_id NULL. The fix stamps the
current tenant id at the persistence chokepoints when running under a
non-default tenant context, while preserving single-tenant behavior exactly
(tenant_id stays None under the default tenant).

Spec ref: multi-tenancy-spec §5.2 — scoping enforced centrally in the graph
write layer.
"""

import json

import pytest

from app.core.middleware.request_context import DEFAULT_TENANT_ID, current_tenant_id
from app.models.signal import MeetingSignals, Signal
from app.services.graph.signal_graph_writer import SignalGraphWriter
from app.services.signal_store import SignalStore


class _FakeClient:
    def __init__(self):
        self.writes = []

    async def execute_write(self, query, params):
        self.writes.append((query, params))
        return []


def _signal(sig_id="s-1", tenant_id=None):
    return Signal(
        id=sig_id,
        type="decision",
        content="We will use pgvector.",
        source_meeting_id="b1",
        source_timestamp="2026-06-11T10:00:00+00:00",
        tenant_id=tenant_id,
    )


def _meeting(signals):
    return MeetingSignals(
        meeting_id="m1", bot_id="b1", signals=signals, signal_count=len(signals)
    )


@pytest.fixture
def _reset_tenant_context():
    """Restore the default tenant context after each test."""
    yield lambda tid: current_tenant_id.set(tid)
    current_tenant_id.set(DEFAULT_TENANT_ID)


# ---------------------------------------------------------------------------
# SignalStore.save — primary stamping chokepoint
# ---------------------------------------------------------------------------




def test_save_leaves_tenant_id_none_under_default_tenant(tmp_path, _reset_tenant_context):
    """Single-tenant regression: default context must not change tenant_id."""
    _reset_tenant_context(DEFAULT_TENANT_ID)
    store = SignalStore(signals_dir=tmp_path)

    ms = _meeting([_signal()])
    path = store.save(ms)

    assert ms.signals[0].tenant_id is None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["signals"][0]["tenant_id"] is None


def test_save_does_not_overwrite_existing_tenant_id(tmp_path, _reset_tenant_context):
    """An explicitly set tenant_id survives save() under a different context."""
    _reset_tenant_context("tenant-other")
    store = SignalStore(signals_dir=tmp_path)

    ms = _meeting([_signal(tenant_id="tenant-original")])
    store.save(ms)

    assert ms.signals[0].tenant_id == "tenant-original"


# ---------------------------------------------------------------------------
# bind_current_tenant — tenant propagation into FastAPI BackgroundTasks
# ---------------------------------------------------------------------------
#
# TenantContextMiddleware resets the ContextVar in its `finally` BEFORE
# FastAPI background tasks run, so stamping inside a background task would
# resolve "default" and write tenant_id=None — the exact bug being fixed.
# bind_current_tenant captures the tenant at enqueue time (review finding).


@pytest.mark.asyncio
async def test_bind_current_tenant_propagates_into_task(_reset_tenant_context):
    from app.core.tenancy.context import bind_current_tenant

    seen = {}

    async def task(arg):
        seen["tenant"] = current_tenant_id.get()
        seen["arg"] = arg

    _reset_tenant_context("tenant-acme")
    bound = bind_current_tenant(task)

    # Simulate the middleware reset before the background task executes.
    current_tenant_id.set(DEFAULT_TENANT_ID)
    await bound("payload")

    assert seen["tenant"] == "tenant-acme"
    assert seen["arg"] == "payload"


@pytest.mark.asyncio
async def test_bind_current_tenant_restores_context_after_task(_reset_tenant_context):
    from app.core.tenancy.context import bind_current_tenant

    async def task():
        return None

    _reset_tenant_context("tenant-acme")
    bound = bind_current_tenant(task)
    current_tenant_id.set(DEFAULT_TENANT_ID)
    await bound()

    assert current_tenant_id.get() == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_bind_current_tenant_restores_context_when_task_raises(
    _reset_tenant_context,
):
    """The finally-reset must run even when the bound task fails."""
    from app.core.tenancy.context import bind_current_tenant

    async def task():
        raise RuntimeError("boom")

    _reset_tenant_context("tenant-acme")
    bound = bind_current_tenant(task)
    current_tenant_id.set(DEFAULT_TENANT_ID)

    with pytest.raises(RuntimeError, match="boom"):
        await bound()

    assert current_tenant_id.get() == DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# SignalGraphWriter — central enforcement fallback (spec §5.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_writer_stamps_tenant_under_nondefault_tenant(_reset_tenant_context):
    """Writer resolves the current tenant when signal.tenant_id is None."""
    _reset_tenant_context("tenant-acme")
    client = _FakeClient()
    writer = SignalGraphWriter(client)

    await writer.write_meeting_signals(_meeting([_signal()]))

    upserts = [(q, p) for q, p in client.writes if "MERGE (s:Signal" in q]
    assert upserts, "No UPSERT_SIGNAL write call found"
    assert upserts[0][1]["tenant_id"] == "tenant-acme"


@pytest.mark.asyncio
async def test_graph_writer_keeps_none_under_default_tenant(_reset_tenant_context):
    """Single-tenant regression: node tenant_id stays None under default context."""
    _reset_tenant_context(DEFAULT_TENANT_ID)
    client = _FakeClient()
    writer = SignalGraphWriter(client)

    await writer.write_meeting_signals(_meeting([_signal()]))

    upserts = [(q, p) for q, p in client.writes if "MERGE (s:Signal" in q]
    assert upserts, "No UPSERT_SIGNAL write call found"
    assert upserts[0][1]["tenant_id"] is None


@pytest.mark.asyncio
async def test_graph_writer_prefers_explicit_tenant_id(_reset_tenant_context):
    """An explicit signal.tenant_id wins over the ambient context."""
    _reset_tenant_context("tenant-other")
    client = _FakeClient()
    writer = SignalGraphWriter(client)

    await writer.write_meeting_signals(_meeting([_signal(tenant_id="tenant-original")]))

    upserts = [(q, p) for q, p in client.writes if "MERGE (s:Signal" in q]
    assert upserts, "No UPSERT_SIGNAL write call found"
    assert upserts[0][1]["tenant_id"] == "tenant-original"
