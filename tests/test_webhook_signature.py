"""GitHub webhook signature verification and delivery replay handling.

Covers ``POST /api/webhook/github``: X-Hub-Signature-256 is enforced when a
webhook secret is configured (checked over the raw body before JSON parsing),
unsigned deliveries are still accepted when no secret is configured, and a
repeated X-GitHub-Delivery id does not re-run the orchestrator.
"""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.webhook as webhook_module
from app.models import WebhookResponse
from app.routes.webhook import verify_github_signature

SECRET = "test-webhook-secret"
URL = "/api/webhook/github"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _FakeOrchestrator:
    """Stands in for WebhookOrchestrator; records every construction."""

    calls: list = []

    def __init__(self, payload, event):
        _FakeOrchestrator.calls.append((payload, event))

    async def process(self):
        return WebhookResponse(status="processing", processed_files=["a.md"])


@pytest.fixture
def client(monkeypatch):
    _FakeOrchestrator.calls = []
    monkeypatch.setattr(webhook_module, "WebhookOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(webhook_module, "_seen_deliveries", type(webhook_module._seen_deliveries)())
    monkeypatch.setattr(webhook_module, "_unverified_warning_logged", False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", "")

    app = FastAPI()
    app.include_router(webhook_module.router, prefix="/api")
    # Not used as a context manager: startup hooks (task queue) are not needed.
    return TestClient(app)


def _post(client, body: bytes, *, signature=None, delivery="d-1", event="push"):
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    return client.post(URL, content=body, headers=headers)


BODY = json.dumps({"ref": "refs/heads/main", "commits": []}).encode()


# --- pure helper -------------------------------------------------------------

def test_verify_helper_accepts_valid_and_rejects_others():
    assert verify_github_signature(SECRET, BODY, _sign(BODY)) is True
    assert verify_github_signature(SECRET, BODY, _sign(BODY, "other")) is False
    assert verify_github_signature(SECRET, BODY + b" ", _sign(BODY)) is False
    assert verify_github_signature(SECRET, BODY, None) is False
    assert verify_github_signature(SECRET, BODY, "") is False
    assert verify_github_signature("", BODY, _sign(BODY, "")) is False


# --- endpoint with a secret configured ---------------------------------------

def test_valid_signature_accepted(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", SECRET)
    resp = _post(client, BODY, signature=_sign(BODY))
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    assert _FakeOrchestrator.calls == [(json.loads(BODY), "push")]


def test_invalid_signature_rejected_401(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", SECRET)
    resp = _post(client, BODY, signature=_sign(BODY, "wrong-secret"))
    assert resp.status_code == 401
    assert _FakeOrchestrator.calls == []


def test_missing_signature_rejected_401_when_secret_set(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", SECRET)
    resp = _post(client, BODY)
    assert resp.status_code == 401
    assert _FakeOrchestrator.calls == []


def test_secret_from_github_webhook_secret_env(client, monkeypatch):
    # A redacted placeholder in app_config.json falls back to the env var.
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", "[CONFIGURED]")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    assert _post(client, BODY, delivery="d-a").status_code == 401
    assert _post(client, BODY, signature=_sign(BODY), delivery="d-b").status_code == 200


def test_json_only_parsed_after_verification(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", SECRET)
    parsed = []
    real_loads = webhook_module.json.loads

    def spy_loads(*args, **kwargs):
        parsed.append(args[0] if args else kwargs)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(webhook_module.json, "loads", spy_loads)

    # Not even valid JSON: a bad signature must be a 401, not a parse error.
    garbage = b"{not json"
    resp = _post(client, garbage, signature=_sign(garbage, "wrong-secret"))
    assert resp.status_code == 401
    assert garbage not in parsed

    resp = _post(client, BODY, signature=_sign(BODY), delivery="d-2")
    assert resp.status_code == 200
    assert BODY in parsed


# --- endpoint without a secret -----------------------------------------------

def test_accepted_when_secret_unset_and_warns_once(client, caplog):
    with caplog.at_level("WARNING", logger=webhook_module.logger.name):
        assert _post(client, BODY, delivery="d-x").status_code == 200
        assert _post(client, BODY, delivery="d-y").status_code == 200
    warnings = [r for r in caplog.records if "verification is DISABLED" in r.getMessage()]
    assert len(warnings) == 1
    assert len(_FakeOrchestrator.calls) == 2


def test_placeholder_secret_without_env_is_treated_as_unset(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", "${GITHUB_WEBHOOK_SECRET}")
    assert _post(client, BODY).status_code == 200


# --- delivery replay ---------------------------------------------------------

def test_duplicate_delivery_short_circuits(client, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "WEBHOOK_SECRET", SECRET)
    first = _post(client, BODY, signature=_sign(BODY), delivery="dup-1")
    second = _post(client, BODY, signature=_sign(BODY), delivery="dup-1")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "already_processed"
    assert len(_FakeOrchestrator.calls) == 1

    # A replayed id still needs a valid signature.
    assert _post(client, BODY, signature=_sign(BODY, "x"), delivery="dup-1").status_code == 401


def test_failed_delivery_is_not_remembered(client, monkeypatch):
    class _Boom(_FakeOrchestrator):
        async def process(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(webhook_module, "WebhookOrchestrator", _Boom)
    assert _post(client, BODY, delivery="retry-1").status_code == 500

    monkeypatch.setattr(webhook_module, "WebhookOrchestrator", _FakeOrchestrator)
    resp = _post(client, BODY, delivery="retry-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"


def test_seen_deliveries_are_bounded(client, monkeypatch):
    monkeypatch.setattr(webhook_module, "MAX_SEEN_DELIVERIES", 3)
    for i in range(5):
        assert _post(client, BODY, delivery=f"b-{i}").status_code == 200
    assert list(webhook_module._seen_deliveries) == ["b-2", "b-3", "b-4"]
    # The evicted oldest id is processed again.
    assert _post(client, BODY, delivery="b-0").json()["status"] == "processing"
