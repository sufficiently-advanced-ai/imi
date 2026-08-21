"""Readiness contract: lifecycle mark_ready() and the /health/startup probe."""


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.lifecycle import LifecycleManager, LifecycleState
from app.routes import health as health_routes


@pytest.mark.asyncio
async def test_startup_does_not_mark_ready_until_mark_ready():
    lm = LifecycleManager()
    await lm.startup()
    assert lm.state == LifecycleState.RUNNING
    assert lm.is_ready() is False
    assert await lm.wait_until_ready(timeout=0.01) is False
    lm.mark_ready(detail="mode=stateful")
    assert lm.is_ready() is True
    assert await lm.wait_until_ready(timeout=0.01) is True
    assert lm.get_status()["startup_complete"] is True
    lm.mark_ready()  # idempotent


def _client(monkeypatch, lm):
    monkeypatch.setattr(health_routes, "_startup_state", lambda: {
        "ready": lm.is_ready(), "state": lm.state.value, "startup": getattr(lm, "startup_detail", None),
    })
    app = FastAPI()
    app.include_router(health_routes.router)
    return TestClient(app)


def test_health_startup_is_503_then_200(monkeypatch):
    lm = LifecycleManager()
    client = _client(monkeypatch, lm)

    r = client.get("/health/startup")
    assert r.status_code == 503
    assert r.json()["ready"] is False
    assert r.headers["cache-control"].startswith("no-cache")

    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "starting"

    lm.startup_detail = "stateful"
    lm.mark_ready()
    r = client.get("/health/startup")
    assert r.status_code == 200
    assert r.json()["ready"] is True
    assert r.json()["startup"] == "stateful"
