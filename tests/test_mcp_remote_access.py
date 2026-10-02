"""ADR-008 — remote MCP access: transports, Host allowlist, startup warnings.

Covers:
  - Streamable HTTP is mounted at /api/mcp/http alongside SSE, completes an
    MCP initialize + tools/list handshake, and its session manager is wired
    into the host app's lifespan (startup handlers still run).
  - Both transports enforce the same DNS-rebinding / Host allowlist.
  - MCP_PUBLIC_URL contributes its host[:port] to the allowlist; invalid
    values are ignored.
  - Backward compatibility: a non-loopback MCP_ALLOWED_HOSTS entry without
    MCP_PUBLIC_URL keeps working (and logs a warning recommending it).
  - Startup logging: remote tier enabled + unauthenticated; http:// warning.

None of this needs Neo4j: tools/list only reads the static tool list.
"""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.routes import mcp_server

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def mcp_settings(monkeypatch):
    """Set MCP_ALLOWED_HOSTS / MCP_PUBLIC_URL for one test."""

    def _set(allowed_hosts: str = "", public_url: str = ""):
        monkeypatch.setattr(settings, "MCP_ALLOWED_HOSTS", allowed_hosts, raising=False)
        monkeypatch.setattr(settings, "MCP_PUBLIC_URL", public_url, raising=False)

    _set()
    return _set


def _rebuild_security(monkeypatch):
    """Recompute the transport security settings from current settings.

    ``_security`` is computed at import; build_mcp_app() reads it at call time,
    so patching it exercises the real allowlist on the Streamable HTTP route.
    """
    monkeypatch.setattr(
        mcp_server,
        "_security",
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=mcp_server._build_allowed_hosts(),
        ),
    )


def _app(startup_calls: list | None = None) -> FastAPI:
    app = FastAPI()
    if startup_calls is not None:
        app.add_event_handler("startup", lambda: startup_calls.append("startup"))
    mcp_server.mount_mcp(app, "/api/mcp")
    return app


def _sse_json(resp) -> dict:
    """Extract the JSON-RPC message from a Streamable HTTP response."""
    if resp.headers["content-type"].startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no data line in SSE response: {resp.text!r}")


# ---------------------------------------------------------------------------
# Streamable HTTP transport
# ---------------------------------------------------------------------------


def test_streamable_http_initialize_and_list_tools(mcp_settings):
    startup_calls: list = []
    app = _app(startup_calls)
    with TestClient(app, base_url="http://localhost") as client:
        # The wrapped lifespan still runs the app's own startup handlers.
        assert startup_calls == ["startup"]

        resp = client.post("/api/mcp/http", json=INIT, headers=MCP_HEADERS)
        assert resp.status_code == 200, resp.text
        init = _sse_json(resp)
        assert init["result"]["serverInfo"]["name"] == "kb-graph"
        session_id = resp.headers["mcp-session-id"]

        headers = {**MCP_HEADERS, "mcp-session-id": session_id}
        client.post(
            "/api/mcp/http",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        resp = client.post(
            "/api/mcp/http",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        names = {t["name"] for t in _sse_json(resp)["result"]["tools"]}

    # Same tool set as the SSE transport (one Server instance serves both).
    assert names == {t.name for t in mcp_server.TOOLS}


def test_streamable_http_trailing_slash_does_not_redirect(mcp_settings):
    with TestClient(_app(), base_url="http://localhost") as client:
        resp = client.post(
            "/api/mcp/http/", json=INIT, headers=MCP_HEADERS, follow_redirects=False
        )
    assert resp.status_code == 200


def test_session_manager_runs_once_per_app(mcp_settings):
    """create_app() may be called more than once (tests, downstream factories)."""
    for _ in range(2):
        with TestClient(_app(), base_url="http://localhost") as client:
            resp = client.post("/api/mcp/http", json=INIT, headers=MCP_HEADERS)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Same Host allowlist on both transports
# ---------------------------------------------------------------------------


def test_unknown_host_rejected_on_both_transports(mcp_settings):
    # The SDK's connect_sse sends the 421 and then raises; don't re-raise it here.
    with TestClient(
        _app(), base_url="http://evil.example.com", raise_server_exceptions=False
    ) as client:
        http = client.post("/api/mcp/http", json=INIT, headers=MCP_HEADERS)
        sse_get = client.get("/api/mcp/sse")
        sse_post = client.post("/api/mcp/messages/?session_id=00", json=INIT)
    assert http.status_code == 421
    assert sse_get.status_code == 421
    assert sse_post.status_code == 421


def test_sse_transport_still_mounted(mcp_settings):
    # Allowed host + unknown session → the SSE transport's own 4xx, not 404/421.
    with TestClient(_app(), base_url="http://localhost") as client:
        resp = client.post(
            "/api/mcp/messages/?session_id=0123456789abcdef0123456789abcdef", json=INIT
        )
    assert resp.status_code in (400, 404)
    assert resp.status_code != 421
    assert "session" in resp.text.lower()


def test_public_url_host_accepted_by_transport(mcp_settings, monkeypatch):
    mcp_settings(public_url="http://imi.example.ts.net:8080/api/mcp/http")
    _rebuild_security(monkeypatch)
    with TestClient(_app(), base_url="http://imi.example.ts.net:8080") as client:
        resp = client.post("/api/mcp/http", json=INIT, headers=MCP_HEADERS)
    assert resp.status_code == 200


def test_legacy_allowed_hosts_without_public_url_still_accepted(mcp_settings, monkeypatch):
    """Backward compatibility: pre-ADR-008 private-network deployments."""
    mcp_settings(allowed_hosts="imi.example.ts.net")
    _rebuild_security(monkeypatch)
    with TestClient(_app(), base_url="http://imi.example.ts.net") as client:
        resp = client.post("/api/mcp/http", json=INIT, headers=MCP_HEADERS)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Allowlist derivation
# ---------------------------------------------------------------------------

LOCALHOST_ONLY = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]


def test_default_allowlist_is_localhost_only(mcp_settings):
    assert mcp_server._build_allowed_hosts() == LOCALHOST_ONLY


@pytest.mark.parametrize(
    "public_url, expected",
    [
        ("https://imi.example.ts.net/api/mcp/http", "imi.example.ts.net"),
        ("http://imi.example.ts.net:8080/api/mcp/http", "imi.example.ts.net:8080"),
        ("HTTP://IMI.Example.ts.net:8080", "imi.example.ts.net:8080"),
        ("https://imi.example.ts.net:443/api/mcp/http", "imi.example.ts.net"),
        ("http://100.64.0.10:8080/api/mcp/http", "100.64.0.10:8080"),
        ("http://[fd7a:115c::1]:8080/api/mcp/http", "[fd7a:115c::1]:8080"),
    ],
)
def test_public_url_host_added_to_allowlist(mcp_settings, public_url, expected):
    mcp_settings(public_url=public_url)
    hosts = mcp_server._build_allowed_hosts()
    assert hosts[: len(LOCALHOST_ONLY)] == LOCALHOST_ONLY
    assert expected in hosts


@pytest.mark.parametrize(
    "public_url",
    ["imi.example.ts.net", "ftp://imi.example.ts.net", "http://", "http://host:notaport/"],
)
def test_invalid_public_url_is_ignored(mcp_settings, public_url):
    mcp_settings(public_url=public_url)
    assert mcp_server._build_allowed_hosts() == LOCALHOST_ONLY


def test_allowed_hosts_and_public_url_combine_without_duplicates(mcp_settings):
    mcp_settings(
        allowed_hosts="imi.example.ts.net, other.example.ts.net",
        public_url="https://imi.example.ts.net/api/mcp/http",
    )
    hosts = mcp_server._build_allowed_hosts()
    assert hosts == LOCALHOST_ONLY + ["imi.example.ts.net", "other.example.ts.net"]


# ---------------------------------------------------------------------------
# Startup logging
# ---------------------------------------------------------------------------


def _messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_local_tier_logs_no_warning(mcp_settings, caplog):
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    assert _messages(caplog, logging.WARNING) == []
    assert any("local tier" in m for m in _messages(caplog, logging.INFO))


def test_loopback_allowed_hosts_count_as_local(mcp_settings, caplog):
    mcp_settings(allowed_hosts="localhost:8080,127.0.0.2,[::1]:8080")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    assert _messages(caplog, logging.WARNING) == []


def test_https_public_url_logs_remote_tier_unauthenticated_once(mcp_settings, caplog):
    mcp_settings(public_url="https://imi.example.ts.net/api/mcp/http")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "remote tier enabled" in warnings[0]
    assert "NO MCP authentication" in warnings[0]


def test_http_public_url_adds_plain_http_warning(mcp_settings, caplog):
    mcp_settings(public_url="http://imi.example.ts.net:8080/api/mcp/http")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 2
    assert any("remote tier enabled" in w for w in warnings)
    assert any("plain http://" in w for w in warnings)


def test_invalid_public_url_logs_error(mcp_settings, caplog):
    mcp_settings(public_url="imi.example.ts.net")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    assert any("invalid MCP_PUBLIC_URL" in m for m in _messages(caplog, logging.ERROR))


def test_legacy_remote_allowed_hosts_logs_recommendation(mcp_settings, caplog):
    mcp_settings(allowed_hosts="imi.example.ts.net")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    mcp_server.log_mcp_access_tier()
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "imi.example.ts.net" in warnings[0]
    assert "MCP_PUBLIC_URL" in warnings[0]


def test_lifespan_logs_tier_at_startup(mcp_settings, caplog):
    mcp_settings(public_url="https://imi.example.ts.net/api/mcp/http")
    caplog.set_level(logging.INFO, logger=mcp_server.logger.name)
    with TestClient(_app(), base_url="http://localhost"):
        pass
    assert any("remote tier enabled" in m for m in _messages(caplog, logging.WARNING))
