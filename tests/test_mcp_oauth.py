"""ADR-010 — MCP authentication: static bearer tokens and the OAuth 2.1 server.

Covers:
  - Config validation: an enabled MCP_AUTH that is misconfigured refuses to mount.
  - MCP_AUTH=token: MCP requests need a listed static token.
  - MCP_AUTH=oauth: discovery metadata, dynamic client registration limited to
    allowed redirect URIs, the passphrase sign-in page (approve, deny, wrong
    passphrase, lockout), PKCE code exchange, single-use codes, resource
    binding, refresh-token rotation, revocation, and static tokens alongside.
  - The audit principal comes from the token, not the client's self-report.
  - scripts/mcp_auth.py mints hashes the app accepts.

None of this needs Neo4j: initialize only touches the static server info.
"""

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.routes import mcp_server
from app.services import mcp_oauth

ORIGIN = "https://imi.example.test"
PUBLIC_URL = f"{ORIGIN}/api/mcp/http"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSPHRASE = "correct horse battery staple"
STATIC_TOKEN = "imi_st_test-static-token"

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


@pytest.fixture(scope="module")
def passphrase_hash() -> str:
    return mcp_oauth.hash_passphrase(PASSPHRASE)


@pytest.fixture
def auth_settings(monkeypatch, tmp_path, passphrase_hash):
    def _set(mode: str, **overrides):
        values = {
            "MCP_AUTH": mode,
            "MCP_PUBLIC_URL": PUBLIC_URL,
            "MCP_ALLOWED_HOSTS": "",
            "MCP_REVIEW_ACTIONS": False,
            "MCP_STATIC_TOKENS": f"ci:{mcp_oauth.hash_token(STATIC_TOKEN)}",
            "MCP_OAUTH_PASSPHRASE_HASH": passphrase_hash,
            "MCP_OAUTH_REDIRECT_URIS": CALLBACK,
            "MCP_OAUTH_ACCESS_TOKEN_TTL": 3600,
            "MCP_OAUTH_REFRESH_TOKEN_TTL": 86400,
            "DATABASE_PATH": str(tmp_path / "imi.db"),
        }
        values.update(overrides)
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value, raising=False)
        monkeypatch.setattr(
            mcp_server,
            "_security",
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=mcp_server._build_allowed_hosts(),
            ),
        )

    return _set


def _client() -> TestClient:
    app = FastAPI()
    mcp_server.mount_mcp(app, "/api/mcp")
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _init(client: TestClient, token: str | None = None):
    headers = dict(MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/mcp/http", json=INIT, headers=headers)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _register(client: TestClient, redirect_uri: str = CALLBACK) -> dict:
    return client.post(
        "/oauth/register",
        json={
            "redirect_uris": [redirect_uri],
            "client_name": "Claude <b>",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


def _start_authorize(client: TestClient, client_id: str, challenge: str, **extra) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "resource": PUBLIC_URL,
        **extra,
    }
    resp = client.get("/oauth/authorize", params=params)
    assert resp.status_code == 302, resp.text
    return resp.headers["location"]


def _request_id(login_url: str) -> str:
    parts = urlsplit(login_url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ORIGIN}/oauth/login"
    return parse_qs(parts.query)["request"][0]


def _sign_in(client: TestClient) -> tuple[str, dict]:
    """Register, authorize, approve, exchange: returns (client_id, token response)."""
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    request_id = _request_id(_start_authorize(client, client_id, challenge))
    resp = client.post("/oauth/login", data={"request": request_id, "passphrase": PASSPHRASE, "action": "approve"})
    assert resp.status_code == 302, resp.text
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    tokens = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": CALLBACK,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": PUBLIC_URL,
        },
    )
    assert tokens.status_code == 200, tokens.text
    return client_id, tokens.json()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, overrides, message",
    [
        ("oauth", {"MCP_PUBLIC_URL": ""}, "MCP_PUBLIC_URL"),
        ("oauth", {"MCP_OAUTH_PASSPHRASE_HASH": ""}, "MCP_OAUTH_PASSPHRASE_HASH"),
        ("oauth", {"MCP_PUBLIC_URL": "http://imi.example.test/api/mcp/http"}, "https"),
        ("token", {"MCP_STATIC_TOKENS": ""}, "MCP_STATIC_TOKENS"),
        ("token", {"MCP_STATIC_TOKENS": "ci:not-a-hash"}, "sha256"),
        ("bogus", {}, "MCP_AUTH"),
    ],
)
def test_misconfigured_auth_refuses_to_mount(auth_settings, mode, overrides, message):
    auth_settings(mode, **overrides)
    with pytest.raises(ValueError, match=message):
        mcp_server.mount_mcp(FastAPI(), "/api/mcp")


def test_default_mode_is_open(auth_settings):
    auth_settings("none")
    assert mcp_oauth.load_auth_config() == mcp_oauth.McpAuthConfig()
    with _client() as client:
        assert _init(client).status_code == 200
        assert client.get("/.well-known/oauth-authorization-server").status_code == 404


def test_public_paths_follow_mode():
    assert mcp_oauth.public_paths(mcp_oauth.McpAuthConfig()) == ()
    assert mcp_oauth.public_paths(mcp_oauth.McpAuthConfig(mode="token")) == ("/api/mcp",)
    assert "/oauth" in mcp_oauth.public_paths(mcp_oauth.McpAuthConfig(mode="oauth"))


# ---------------------------------------------------------------------------
# Static tokens
# ---------------------------------------------------------------------------


def test_token_mode_requires_a_listed_token(auth_settings):
    auth_settings("token")
    with _client() as client:
        resp = _init(client)
        assert resp.status_code == 401
        assert "resource_metadata" not in resp.headers["www-authenticate"]
        assert _init(client, "imi_st_wrong").status_code == 401
        assert _init(client, STATIC_TOKEN).status_code == 200
        # SSE transport is behind the same check.
        assert client.get("/api/mcp/sse").status_code == 401
        # No OAuth server in token mode.
        assert client.get("/.well-known/oauth-authorization-server").status_code == 404


def test_static_token_parsing():
    digest = "a" * 64
    assert mcp_oauth.parse_static_tokens(f" one:{digest} , two:{digest.upper()},") == {
        "one": digest,
        "two": digest,
    }
    with pytest.raises(ValueError):
        mcp_oauth.parse_static_tokens("nocolon")


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def test_unauthenticated_request_points_at_resource_metadata(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        resp = _init(client)
        assert resp.status_code == 401
        assert (
            f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/api/mcp/http"'
            in resp.headers["www-authenticate"]
        )

        prm = client.get("/.well-known/oauth-protected-resource/api/mcp/http").json()
        assert prm["resource"] == PUBLIC_URL
        assert prm["authorization_servers"] == [f"{ORIGIN}/"]
        assert client.get("/.well-known/oauth-protected-resource").json() == prm

        meta = client.get("/.well-known/oauth-authorization-server").json()
        assert meta["authorization_endpoint"] == f"{ORIGIN}/oauth/authorize"
        assert meta["token_endpoint"] == f"{ORIGIN}/oauth/token"
        assert meta["registration_endpoint"] == f"{ORIGIN}/oauth/register"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "none" in meta["token_endpoint_auth_methods_supported"]


def test_registration_only_accepts_allowed_redirects(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        resp = _register(client, "https://evil.example/callback")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_redirect_uri"
        assert _register(client, "http://localhost:33418/callback").status_code == 201
        assert _register(client, "http://127.0.0.1/callback").status_code == 201
        assert _register(client, "https://localhost/callback").status_code == 400
        assert _register(client).status_code == 201


def test_full_flow_refresh_and_revoke(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id, tokens = _sign_in(client)
        assert tokens["token_type"].lower() == "bearer"
        access, refresh = tokens["access_token"], tokens["refresh_token"]
        assert _init(client, access).status_code == 200

        # Rotation: new pair works, the old pair is dead.
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
        )
        assert resp.status_code == 200, resp.text
        rotated = resp.json()
        assert _init(client, access).status_code == 401
        assert _init(client, rotated["access_token"]).status_code == 200

        # Revoking the access token revokes the grant, refresh token included.
        # The SDK's revocation form requires client_secret, empty for public clients.
        resp = client.post(
            "/oauth/revoke",
            data={"token": rotated["access_token"], "client_id": client_id, "client_secret": ""},
        )
        assert resp.status_code == 200
        assert _init(client, rotated["access_token"]).status_code == 401
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": rotated["refresh_token"], "client_id": client_id},
        )
        assert resp.status_code == 400


def test_refresh_token_replay_revokes_the_grant(auth_settings):
    """OAuth 2.1 reuse detection: a spent refresh token coming back means two
    parties hold the grant, so all of it is revoked."""
    auth_settings("oauth")
    with _client() as client:
        client_id, tokens = _sign_in(client)
        refresh = {"grant_type": "refresh_token", "client_id": client_id}
        # The "attacker" rotates first...
        rotated = client.post("/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]}).json()
        assert _init(client, rotated["access_token"]).status_code == 200
        # ...then the old token is replayed: refused, and the new chain dies too.
        replay = client.post("/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]})
        assert replay.status_code == 400
        assert _init(client, rotated["access_token"]).status_code == 401
        again = client.post("/oauth/token", data={**refresh, "refresh_token": rotated["refresh_token"]})
        assert again.status_code == 400


def test_grant_has_an_absolute_lifetime(auth_settings, monkeypatch):
    auth_settings("oauth")
    monkeypatch.setattr(mcp_oauth, "MAX_GRANT_AGE", 0)
    with _client() as client:
        client_id, tokens = _sign_in(client)
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert resp.status_code == 400


def test_code_is_single_use_and_pkce_bound(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        verifier, challenge = _pkce()
        request_id = _request_id(_start_authorize(client, client_id, challenge))
        resp = client.post("/oauth/login", data={"request": request_id, "passphrase": PASSPHRASE, "action": "approve"})
        location = resp.headers["location"]
        assert location.startswith(CALLBACK)
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["xyz"]
        form = {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": CALLBACK,
            "client_id": client_id,
        }
        bad = client.post("/oauth/token", data={**form, "code_verifier": "wrong" * 10})
        assert bad.status_code == 400
        ok = client.post("/oauth/token", data={**form, "code_verifier": verifier})
        assert ok.status_code == 200, ok.text
        assert _init(client, ok.json()["access_token"]).status_code == 200
        again = client.post("/oauth/token", data={**form, "code_verifier": verifier})
        assert again.status_code == 400
        assert again.json()["error"] == "invalid_grant"
        # RFC 6749 §4.1.2: tokens issued from a replayed code are revoked.
        assert _init(client, ok.json()["access_token"]).status_code == 401


def test_authorize_rejects_other_resources(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        _, challenge = _pkce()
        location = _start_authorize(client, client_id, challenge, resource="https://other.example/mcp")
        assert location.startswith(CALLBACK)
        assert parse_qs(urlsplit(location).query)["error"] == ["invalid_request"]


def test_sign_in_page_escapes_and_denies(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        _, challenge = _pkce()
        request_id = _request_id(_start_authorize(client, client_id, challenge))
        page = client.get("/oauth/login", params={"request": request_id})
        assert page.status_code == 200
        assert "Claude &lt;b&gt;" in page.text
        assert "https://claude.ai" in page.text
        assert page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

        resp = client.post("/oauth/login", data={"request": request_id, "action": "deny"})
        assert resp.status_code == 302
        assert parse_qs(urlsplit(resp.headers["location"]).query)["error"] == ["access_denied"]
        # The request is consumed.
        assert client.get("/oauth/login", params={"request": request_id}).status_code == 400
        assert client.get("/oauth/login", params={"request": "nope"}).status_code == 400


def _new_request(client: TestClient, client_id: str) -> str:
    _, challenge = _pkce()
    return _request_id(_start_authorize(client, client_id, challenge))


def _login(client: TestClient, request_id: str, passphrase: str):
    return client.post("/oauth/login", data={"request": request_id, "passphrase": passphrase, "action": "approve"})


def test_wrong_passphrases_cancel_the_sign_in(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        request_id = _new_request(client, client_id)
        for _ in range(mcp_oauth.MAX_FAILURES_PER_REQUEST - 1):
            resp = _login(client, request_id, "guess")
            assert resp.status_code == 401
            assert "Wrong passphrase" in resp.text
        resp = _login(client, request_id, "guess")
        assert resp.status_code == 401
        assert "Sign-in cancelled" in resp.text
        assert _login(client, request_id, PASSPHRASE).status_code == 400


def test_lockout_is_per_source_ip(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        for _ in range(mcp_oauth.MAX_LOGIN_FAILURES):
            assert _login(client, _new_request(client, client_id), "guess").status_code == 401
        # This source is locked out, even with the right passphrase...
        assert _login(client, _new_request(client, client_id), PASSPHRASE).status_code == 429
    guard = mcp_oauth.LoginGuard()
    for _ in range(mcp_oauth.MAX_LOGIN_FAILURES):
        guard.fail("203.0.113.9")
    # ...but other sources are not.
    assert guard.locked("203.0.113.9")
    assert not guard.locked("198.51.100.7")


def test_concurrent_guesses_cannot_outrun_the_lockout(auth_settings):
    """Checks are serialised: parallel wrong passphrases get at most
    MAX_LOGIN_FAILURES real checks before the source is locked out."""
    from concurrent.futures import ThreadPoolExecutor

    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        requests = [_new_request(client, client_id) for _ in range(mcp_oauth.MAX_PENDING_PER_IP)]
        attempts = [r for r in requests for _ in range(2)]
        with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
            codes = list(pool.map(lambda r: _login(client, r, "guess").status_code, attempts))
        assert codes.count(401) == mcp_oauth.MAX_LOGIN_FAILURES
        assert codes.count(429) == len(attempts) - mcp_oauth.MAX_LOGIN_FAILURES


def test_client_ip_skips_proxy_hops_but_not_forged_entries():
    def scope(peer, xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return {"client": (peer, 1234), "headers": headers}

    assert mcp_oauth.client_ip(scope("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    assert mcp_oauth.client_ip(scope("127.0.0.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    assert mcp_oauth.client_ip(scope("127.0.0.1")) == "127.0.0.1"
    # Funnel -> container nginx: nginx appends the Docker gateway after the caller.
    assert mcp_oauth.client_ip(scope("127.0.0.1", "6.6.6.6, 198.51.100.7, 172.20.0.1")) == "198.51.100.7"
    # Tailnet callers are callers, not proxy hops.
    assert mcp_oauth.client_ip(scope("127.0.0.1", "100.101.102.103, 172.20.0.1")) == "100.101.102.103"
    # Only proxy hops: fall back to the outermost one.
    assert mcp_oauth.client_ip(scope("127.0.0.1", "172.20.0.1")) == "172.20.0.1"


def test_pending_sign_ins_are_capped_per_ip_and_overall(auth_settings, monkeypatch):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        first = _new_request(client, client_id)
        for _ in range(mcp_oauth.MAX_PENDING_PER_IP):
            _new_request(client, client_id)
        # One source can only evict its own oldest pending sign-in.
        assert client.get("/oauth/login", params={"request": first}).status_code == 400
        # Per-IP eviction runs first and leaves MAX_PENDING_PER_IP - 1 pending.
        monkeypatch.setattr(mcp_oauth, "MAX_PENDING", mcp_oauth.MAX_PENDING_PER_IP - 1)
        _, challenge = _pkce()
        location = _start_authorize(client, client_id, challenge)
        assert parse_qs(urlsplit(location).query)["error"] == ["temporarily_unavailable"]


def test_unapproved_registrations_are_evicted_not_refused(auth_settings, monkeypatch):
    auth_settings("oauth")
    monkeypatch.setattr(mcp_oauth, "MAX_UNAPPROVED_CLIENTS", 3)
    with _client() as client:
        approved_id, _ = _sign_in(client)
        ids = [_register(client).json()["client_id"] for _ in range(5)]
        assert client.get("/.well-known/oauth-authorization-server").status_code == 200
        _, challenge = _pkce()
        params = {"response_type": "code", "redirect_uri": CALLBACK, "code_challenge": challenge}
        # Oldest unapproved registrations are gone; the newest and the approved one remain.
        assert client.get("/oauth/authorize", params={**params, "client_id": ids[0]}).status_code == 400
        assert client.get("/oauth/authorize", params={**params, "client_id": ids[-1]}).status_code == 302
        assert client.get("/oauth/authorize", params={**params, "client_id": approved_id}).status_code == 302


def test_double_submit_is_not_a_server_error(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        client_id = _register(client).json()["client_id"]
        request_id = _new_request(client, client_id)
        assert _login(client, request_id, PASSPHRASE).status_code == 302
        assert _login(client, request_id, PASSPHRASE).status_code == 400
        assert client.post("/oauth/login", data={"request": request_id, "action": "deny"}).status_code == 400


def test_login_body_is_size_limited(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        resp = client.post("/oauth/login", data={"request": "x", "passphrase": "a" * (mcp_oauth.LOGIN_BODY_LIMIT + 1)})
        assert resp.status_code == 413


def test_static_token_still_works_in_oauth_mode(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        assert _init(client, STATIC_TOKEN).status_code == 200
        assert _init(client, "imi_at_forged").status_code == 401


def test_tokens_survive_a_restart(auth_settings):
    auth_settings("oauth")
    with _client() as client:
        _, tokens = _sign_in(client)
    with _client() as client:
        assert _init(client, tokens["access_token"]).status_code == 200


def test_session_auth_lets_mcp_auth_paths_through(auth_settings, monkeypatch):
    """AUTH_MODE=demo guards the UI with a cookie; MCP and OAuth paths carry
    their own authentication and must not be blocked by it."""
    from app.middleware import auth as session_middleware
    from app.services import auth as auth_service

    auth_settings("oauth")
    monkeypatch.setattr(settings, "AUTH_MODE", "demo", raising=False)
    monkeypatch.setattr(auth_service, "_auth_service", None)
    session_middleware._mcp_auth_paths.cache_clear()
    try:
        app = FastAPI()
        app.add_middleware(session_middleware.AuthenticationMiddleware)
        app.add_api_route("/api/private", lambda: {"ok": True})
        mcp_server.mount_mcp(app, "/api/mcp")
        with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
            assert client.get("/api/private").status_code == 401
            assert client.get("/.well-known/oauth-authorization-server").status_code == 200
            assert client.get("/oauth/login", params={"request": "x"}).status_code == 400
            resp = _init(client)
            assert resp.status_code == 401
            assert "resource_metadata" in resp.headers["www-authenticate"]
            assert _init(client, STATIC_TOKEN).status_code == 200
    finally:
        session_middleware._mcp_auth_paths.cache_clear()
        auth_service._auth_service = None


# ---------------------------------------------------------------------------
# Audit principal (ADR-009)
# ---------------------------------------------------------------------------


def test_principal_comes_from_the_token():
    oauth_user = AuthenticatedUser(
        AccessToken(token="t", client_id="abc", scopes=[], claims={"client_name": "Claude"})
    )
    assert mcp_oauth.principal_name(oauth_user) == "Claude"
    config = mcp_oauth.McpAuthConfig(mode="token")
    static_user = AuthenticatedUser(mcp_oauth.static_access_token("t", "laptop", config))
    assert mcp_oauth.principal_name(static_user) == "token-laptop"
    assert mcp_oauth.principal_name(None) is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_mints_values_the_app_accepts(capsys, monkeypatch, tmp_path):
    import io

    from scripts import mcp_auth

    assert mcp_auth.main(["new-token", "laptop"]) == 0
    out = capsys.readouterr().out
    token = re.search(r"(imi_st_\S+)", out).group(1)
    entry = re.search(r"(laptop:[0-9a-f]{64})", out).group(1)
    assert mcp_oauth.parse_static_tokens(entry) == {"laptop": mcp_oauth.hash_token(token)}

    monkeypatch.setattr("sys.stdin", io.StringIO(PASSPHRASE + "\n"))
    assert mcp_auth.main(["hash-passphrase", "--stdin"]) == 0
    encoded = capsys.readouterr().out.strip().split("=", 1)[1]
    assert mcp_oauth.verify_passphrase(PASSPHRASE, encoded)
    assert not mcp_oauth.verify_passphrase("nope", encoded)

    assert mcp_auth.main(["revoke", "--all", "--db", str(tmp_path / "x.db")]) == 0
    assert mcp_auth.main(["list-clients", "--db", str(tmp_path / "x.db")]) == 0
