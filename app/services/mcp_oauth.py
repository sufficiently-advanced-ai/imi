"""MCP authentication: static bearer tokens and an OAuth 2.1 server (ADR-010).

``MCP_AUTH`` selects the mode:

- ``none`` (default): no MCP authentication; access control is the network's
  job (ADR-008).
- ``token``: every request to ``/api/mcp/*`` needs ``Authorization: Bearer``
  with a token listed (as a SHA-256 hash) in ``MCP_STATIC_TOKENS``.
- ``oauth``: static tokens still work, and imi also runs a single-owner OAuth
  2.1 authorization server so clients that can only do OAuth — claude.ai
  custom connectors, and so Claude Duet — can connect over the internet.

The OAuth endpoints come from the MCP SDK (``mcp.server.auth``: metadata,
authorize, token, register, revoke, PKCE and redirect-URI checks). This module
supplies what the SDK leaves to the server:

- :class:`OAuthStore` — clients and SHA-256-hashed codes/tokens in the app
  SQLite database (``mcp_oauth_clients`` / ``mcp_oauth_tokens``).
- :class:`ImiOAuthProvider` — the SDK provider protocol. ``authorize`` parks the
  request and sends the browser to imi's own sign-in page, where the owner
  approves it by typing the passphrase whose scrypt hash is
  ``MCP_OAUTH_PASSPHRASE_HASH``. There are no user accounts.
- Registration accepts only redirect URIs from ``MCP_OAUTH_REDIRECT_URIS``
  (claude.ai's callback by default) or loopback ``http`` URIs, so a code can
  only ever be delivered to Claude or to the signing-in user's own machine.
- Tokens are bound to the resource ``MCP_PUBLIC_URL`` (RFC 8707); the bearer
  middleware refuses tokens issued for anything else.

Layout on the host app (issuer = origin of ``MCP_PUBLIC_URL``)::

    /.well-known/oauth-authorization-server         RFC 8414 metadata
    /.well-known/oauth-protected-resource[/<path>]  RFC 9728 metadata
    /oauth/authorize /oauth/token /oauth/register /oauth/revoke
    /oauth/login                                    passphrase sign-in page

Authentication says who may call; ADR-002 still decides what any caller may do.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import logging
import secrets
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import (
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route, request_response

logger = logging.getLogger(__name__)

MODE_NONE = "none"
MODE_TOKEN = "token"
MODE_OAUTH = "oauth"

OAUTH_PREFIX = "/oauth"
LOGIN_PATH = f"{OAUTH_PREFIX}/login"
DEFAULT_SCOPE = "mcp"
OWNER_SUBJECT = "owner"

AUTH_CODE_TTL = 300
PENDING_TTL = 600
# Pending sign-ins: capped per source IP (oldest evicted) and overall (new
# ones refused), so nobody can push the owner's pending sign-in out.
MAX_PENDING = 500
MAX_PENDING_PER_IP = 10
# Dynamic registration is open to anyone. Clients the owner never approved are
# pruned after an hour, and beyond the cap the oldest unapproved one is
# evicted, so registrations can't fill the table. Approved clients are kept.
MAX_UNAPPROVED_CLIENTS = 200
UNAPPROVED_CLIENT_AGE = 3600
# A grant ends this long after sign-in however often it is refreshed.
MAX_GRANT_AGE = 90 * 86400
# Owner sign-in: this many wrong passphrases from one source IP within the
# window lock that IP out for the window; a pending sign-in is dropped after
# MAX_FAILURES_PER_REQUEST wrong passphrases. Checks run one at a time.
MAX_LOGIN_FAILURES = 5
MAX_FAILURES_PER_REQUEST = 3
LOGIN_WINDOW = 900
MAX_PASSPHRASE_LEN = 1024
LOGIN_BODY_LIMIT = 16 * 1024

_LOOPBACK_REDIRECT_HOSTS = ("localhost", "127.0.0.1", "::1")
_LOOPBACK_PEERS = ("127.0.0.1", "::1", "localhost")

# scrypt runs here, not on the default thread pool the rest of the app shares.
_SCRYPT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-oauth-scrypt")


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_passphrase(passphrase: str) -> str:
    """``scrypt:n:r:p:salt:hash`` (base64) for ``MCP_OAUTH_PASSPHRASE_HASH``.

    Colon-separated, not ``$``: docker compose interpolates ``$`` in ``.env``.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=64 * 1024 * 1024, dklen=32,
    )
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{b64(salt)}:{b64(digest)}"


def _parse_passphrase_hash(encoded: str) -> tuple[int, int, int, bytes, bytes]:
    scheme, n, r, p, salt, digest = encoded.strip().split(":")
    if scheme != "scrypt":
        raise ValueError(f"unsupported passphrase hash scheme {scheme!r}")
    return int(n), int(r), int(p), base64.b64decode(salt), base64.b64decode(digest)


def verify_passphrase(passphrase: str, encoded: str) -> bool:
    try:
        n, r, p, salt, expected = _parse_passphrase_hash(encoded)
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=n, r=r, p=p,
        maxmem=64 * 1024 * 1024, dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


def parse_static_tokens(raw: str) -> dict[str, str]:
    """``name:sha256hex,...`` → {name: hash}. Raises ValueError on a bad entry."""
    tokens: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, digest = entry.partition(":")
        name, digest = name.strip(), digest.strip().lower()
        if not sep or not name or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(
                f"MCP_STATIC_TOKENS entry for {name or '?'!r} is not name:<64 hex chars sha256>"
            )
        tokens[name] = digest
    return tokens


def _normalize_resource(url: str | None) -> str | None:
    try:
        return str(AnyHttpUrl(url or "")).removesuffix("/")
    except ValidationError:
        return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpAuthConfig:
    mode: str = MODE_NONE
    resource_url: str | None = None  # MCP_PUBLIC_URL, the RFC 8707 resource
    issuer_url: str | None = None  # its origin
    static_tokens: dict[str, str] = field(default_factory=dict)
    passphrase_hash: str = ""
    redirect_uris: frozenset[str] = frozenset()
    access_ttl: int = 3600
    refresh_ttl: int = 30 * 86400

    @property
    def enabled(self) -> bool:
        return self.mode != MODE_NONE

    @property
    def resource_metadata_url(self) -> str | None:
        if self.mode != MODE_OAUTH or not self.resource_url:
            return None
        return str(build_resource_metadata_url(AnyHttpUrl(self.resource_url)))


def load_auth_config(settings: Any = None) -> McpAuthConfig:
    """Read and validate the MCP auth settings. Raises ValueError when an
    enabled mode is misconfigured: imi refuses to start rather than serve MCP
    with less protection than the operator asked for."""
    if settings is None:
        from app.config import settings
    mode = (getattr(settings, "MCP_AUTH", MODE_NONE) or MODE_NONE).strip().lower()
    if mode == MODE_NONE:
        return McpAuthConfig()
    if mode not in (MODE_TOKEN, MODE_OAUTH):
        raise ValueError(f"MCP_AUTH must be none, token or oauth, not {mode!r}")

    static_tokens = parse_static_tokens(_secret(settings, "MCP_STATIC_TOKENS"))
    raw_url = (getattr(settings, "MCP_PUBLIC_URL", "") or "").strip()
    resource_url = issuer_url = None
    if raw_url:
        parts = urlsplit(raw_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"MCP_PUBLIC_URL {raw_url!r} is not an http(s) URL")
        resource_url = _normalize_resource(raw_url)
        issuer_url = f"{parts.scheme}://{parts.netloc}"

    if mode == MODE_TOKEN:
        if not static_tokens:
            raise ValueError("MCP_AUTH=token needs at least one MCP_STATIC_TOKENS entry")
        return McpAuthConfig(mode=mode, resource_url=resource_url, static_tokens=static_tokens)

    if not resource_url:
        raise ValueError("MCP_AUTH=oauth needs MCP_PUBLIC_URL (the URL connectors use)")
    host = urlsplit(issuer_url).hostname or ""
    if issuer_url.startswith("http://") and host not in ("localhost", "127.0.0.1"):
        raise ValueError("MCP_AUTH=oauth needs an https:// MCP_PUBLIC_URL")
    passphrase_hash = _secret(settings, "MCP_OAUTH_PASSPHRASE_HASH").strip()
    try:
        _parse_passphrase_hash(passphrase_hash)
    except (ValueError, TypeError):
        raise ValueError(
            "MCP_AUTH=oauth needs MCP_OAUTH_PASSPHRASE_HASH "
            "(python scripts/mcp_auth.py hash-passphrase)"
        ) from None
    redirect_uris = frozenset(
        u.strip()
        for u in (getattr(settings, "MCP_OAUTH_REDIRECT_URIS", "") or "").split(",")
        if u.strip()
    )
    return McpAuthConfig(
        mode=mode,
        resource_url=resource_url,
        issuer_url=issuer_url,
        static_tokens=static_tokens,
        passphrase_hash=passphrase_hash,
        redirect_uris=redirect_uris,
        access_ttl=int(getattr(settings, "MCP_OAUTH_ACCESS_TOKEN_TTL", 3600)),
        refresh_ttl=int(getattr(settings, "MCP_OAUTH_REFRESH_TOKEN_TTL", 30 * 86400)),
    )


def _secret(settings: Any, name: str) -> str:
    value = getattr(settings, name, "") or ""
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    return str(value)


def public_paths(config: McpAuthConfig) -> tuple[str, ...]:
    """Paths the session-auth middleware must let through: they carry their
    own authentication (bearer tokens, the OAuth flow)."""
    if not config.enabled:
        return ()
    paths = ["/api/mcp"]
    if config.mode == MODE_OAUTH:
        paths += [
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-protected-resource",
            OAUTH_PREFIX,
        ]
    return tuple(paths)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class OAuthStore:
    """Clients and hashed tokens in SQLite, on one worker thread (ordered,
    off the event loop), like :mod:`app.services.ingest_job_store`."""

    def __init__(self) -> None:
        self._engine = None
        self._executor: ThreadPoolExecutor | None = None

    @property
    def is_open(self) -> bool:
        return self._engine is not None

    async def open(self, db_path: str) -> None:
        if self._engine is not None:
            return
        import os

        from sqlalchemy import create_engine

        from app.user_models.mcp_oauth_models import McpOAuthClient, McpOAuthToken

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        McpOAuthClient.__table__.create(engine, checkfirst=True)
        McpOAuthToken.__table__.create(engine, checkfirst=True)
        self._engine = engine
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-oauth")

    async def close(self) -> None:
        executor, engine = self._executor, self._engine
        self._executor = self._engine = None
        if executor is not None:
            executor.shutdown(wait=True)
        if engine is not None:
            engine.dispose()

    async def _run(self, fn, *args):
        if self._executor is None:
            raise RuntimeError("MCP OAuth store is not open")
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _session(self):
        from sqlalchemy.orm import Session

        return Session(self._engine)

    # -- clients -----------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def _get():
            from app.user_models.mcp_oauth_models import McpOAuthClient

            with self._session() as s:
                row = s.get(McpOAuthClient, client_id)
                return row.client_info if row else None

        raw = await self._run(_get)
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def add_client(self, info: OAuthClientInformationFull) -> None:
        """Store a registration, pruning clients the owner never approved."""

        def _add() -> None:
            from sqlalchemy import delete, select

            from app.user_models.mcp_oauth_models import McpOAuthClient

            with self._session() as s:
                unapproved = McpOAuthClient.approved_at.is_(None)
                cutoff = datetime.fromtimestamp(time.time() - UNAPPROVED_CLIENT_AGE, UTC)
                s.execute(delete(McpOAuthClient).where(unapproved, McpOAuthClient.created_at < cutoff))
                surplus = s.scalars(
                    select(McpOAuthClient.client_id)
                    .where(unapproved)
                    .order_by(McpOAuthClient.created_at.desc())
                    .offset(MAX_UNAPPROVED_CLIENTS - 1)
                ).all()
                if surplus:
                    s.execute(delete(McpOAuthClient).where(McpOAuthClient.client_id.in_(surplus)))
                s.add(
                    McpOAuthClient(
                        client_id=info.client_id,
                        client_name=(info.client_name or "")[:256] or None,
                        client_info=info.model_dump_json(),
                        created_at=datetime.now(UTC),
                    )
                )
                s.commit()

        await self._run(_add)

    async def mark_client_approved(self, client_id: str) -> None:
        def _mark():
            from app.user_models.mcp_oauth_models import McpOAuthClient

            with self._session() as s:
                row = s.get(McpOAuthClient, client_id)
                if row is not None and row.approved_at is None:
                    row.approved_at = datetime.now(UTC)
                    s.commit()

        await self._run(_mark)

    async def list_clients(self) -> list[dict[str, Any]]:
        def _list():
            from sqlalchemy import func, select

            from app.user_models.mcp_oauth_models import McpOAuthClient, McpOAuthToken

            with self._session() as s:
                grants = dict(
                    s.execute(
                        select(McpOAuthToken.client_id, func.count(func.distinct(McpOAuthToken.grant_id)))
                        .where(McpOAuthToken.expires_at > time.time())
                        .group_by(McpOAuthToken.client_id)
                    ).all()
                )
                return [
                    {
                        "client_id": c.client_id,
                        "client_name": c.client_name,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                        "approved_at": c.approved_at.isoformat() if c.approved_at else None,
                        "live_grants": grants.get(c.client_id, 0),
                    }
                    for c in s.query(McpOAuthClient).order_by(McpOAuthClient.created_at)
                ]

        return await self._run(_list)

    async def revoke_client(self, client_id: str | None = None) -> int:
        """Delete every token of one client (or of all clients); returns rows removed."""

        def _revoke() -> int:
            from sqlalchemy import delete

            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                stmt = delete(McpOAuthToken)
                if client_id is not None:
                    stmt = stmt.where(McpOAuthToken.client_id == client_id)
                count = s.execute(stmt).rowcount
                s.commit()
                return count

        return await self._run(_revoke)

    # -- tokens ------------------------------------------------------------

    async def add_tokens(self, rows: list[dict[str, Any]]) -> None:
        def _add():
            from sqlalchemy import delete

            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                s.execute(delete(McpOAuthToken).where(McpOAuthToken.expires_at < time.time()))
                now = datetime.now(UTC)
                for row in rows:
                    s.add(McpOAuthToken(created_at=now, **row))
                s.commit()

        await self._run(_add)

    async def get_token(self, token: str, kind: str) -> dict[str, Any] | None:
        def _get():
            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                row = s.get(McpOAuthToken, hash_token(token))
                if row is None or row.kind != kind or row.expires_at < time.time():
                    return None
                return _row_dict(row)

        return await self._run(_get)

    async def find_token(self, token: str) -> dict[str, Any] | None:
        """Any row for *token*, whatever its kind or expiry."""

        def _find():
            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                row = s.get(McpOAuthToken, hash_token(token))
                return _row_dict(row) if row is not None else None

        return await self._run(_find)

    async def use_token(self, token: str, kind: str) -> dict[str, Any] | None:
        """Spend a single-use token (code, refresh): its row becomes a
        ``<kind>_used`` marker until it would have expired, so a second use is
        detected (and revokes the grant) instead of looking like a bad token.
        None when absent, already used or expired."""

        def _use():
            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                row = s.get(McpOAuthToken, hash_token(token))
                if row is None or row.kind != kind or row.expires_at < time.time():
                    return None
                data = _row_dict(row)
                row.kind = f"{kind}_used"
                s.commit()
                return data

        return await self._run(_use)

    async def revoke_grant(self, grant_id: str, kinds: tuple[str, ...] | None = None) -> None:
        def _revoke():
            from sqlalchemy import delete

            from app.user_models.mcp_oauth_models import McpOAuthToken

            with self._session() as s:
                stmt = delete(McpOAuthToken).where(McpOAuthToken.grant_id == grant_id)
                if kinds:
                    stmt = stmt.where(McpOAuthToken.kind.in_(kinds))
                s.execute(stmt)
                s.commit()

        await self._run(_revoke)


def _row_dict(row) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "client_id": row.client_id,
        "grant_id": row.grant_id,
        "client_name": row.client_name,
        "scopes": list(row.scopes or []),
        "resource": row.resource,
        "payload": row.payload,
        "expires_at": row.expires_at,
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


_request_ip: ContextVar[str] = ContextVar("mcp_oauth_request_ip", default="unknown")


def client_ip(scope: dict) -> str:
    """Source address of a request. Behind a local tunnel or proxy (peer on
    loopback, as with Tailscale Funnel) it is the last ``X-Forwarded-For``
    entry: the one the nearest proxy added, which the caller can't forge."""
    peer = (scope.get("client") or ("unknown", 0))[0]
    if peer in _LOOPBACK_PEERS or peer.startswith("127."):
        for key, value in scope.get("headers") or []:
            if key == b"x-forwarded-for":
                last = value.decode("latin-1").split(",")[-1].strip()
                if last:
                    return last
    return peer


class _WithRequestIP:
    """ASGI wrapper exposing :func:`client_ip` to the provider (the SDK's
    authorize handler doesn't pass the request through)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = _request_ip.set(client_ip(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _request_ip.reset(token)


@dataclass
class _Pending:
    client_id: str
    client_name: str | None
    params: AuthorizationParams
    expires_at: float
    source_ip: str = "unknown"
    failures: int = 0


class LoginGuard:
    """Per-source-IP lockout after repeated wrong passphrases.

    ``lock`` serialises passphrase checks so concurrent guesses can't all pass
    the lockout check before any failure is recorded."""

    def __init__(self, max_failures: int = MAX_LOGIN_FAILURES, window: int = LOGIN_WINDOW):
        self.max_failures = max_failures
        self.window = window
        self.lock = asyncio.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    def locked(self, ip: str) -> bool:
        return time.time() < self._locked_until.get(ip, 0.0)

    def fail(self, ip: str) -> None:
        now = time.time()
        if len(self._failures) > 1000:
            self._prune(now)
        failures = self._failures.setdefault(ip, deque())
        failures.append(now)
        while failures and failures[0] < now - self.window:
            failures.popleft()
        if len(failures) >= self.max_failures:
            self._locked_until[ip] = now + self.window
            failures.clear()
            logger.warning("MCP OAuth sign-in locked for %s for %ss after repeated wrong passphrases", ip, self.window)

    def succeed(self, ip: str) -> None:
        self._failures.pop(ip, None)

    def _prune(self, now: float) -> None:
        for ip in [ip for ip, f in self._failures.items() if not f or f[-1] < now - self.window]:
            del self._failures[ip]
        for ip in [ip for ip, until in self._locked_until.items() if until < now]:
            del self._locked_until[ip]


class ImiOAuthProvider:
    """``OAuthAuthorizationServerProvider`` for one owner, backed by OAuthStore."""

    def __init__(self, config: McpAuthConfig, store: OAuthStore | None = None):
        self.config = config
        self.store = store or OAuthStore()
        self.guard = LoginGuard()
        self._pending: OrderedDict[str, _Pending] = OrderedDict()

    # -- registration ------------------------------------------------------

    def redirect_uri_allowed(self, uri: str) -> bool:
        if uri in self.config.redirect_uris:
            return True
        parts = urlsplit(uri)
        return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_REDIRECT_HOSTS

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self.store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not self.redirect_uri_allowed(str(uri)):
                logger.warning("MCP OAuth: refused registration with redirect URI %s", uri)
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"redirect URI {uri} is not allowed by this server (MCP_OAUTH_REDIRECT_URIS)",
                )
        await self.store.add_client(client_info)
        logger.info(
            "MCP OAuth: registered client %s (%s)", client_info.client_id, client_info.client_name
        )

    # -- authorization -----------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource is not None and _normalize_resource(params.resource) != self.config.resource_url:
            raise AuthorizeError("invalid_request", f"unknown resource {params.resource}")
        now = time.time()
        ip = _request_ip.get()
        for key in [k for k, p in self._pending.items() if p.expires_at < now]:
            del self._pending[key]
        same_ip = [k for k, p in self._pending.items() if p.source_ip == ip]
        for key in same_ip[: max(0, len(same_ip) - MAX_PENDING_PER_IP + 1)]:
            del self._pending[key]
        if len(self._pending) >= MAX_PENDING:
            logger.warning("MCP OAuth: pending sign-in limit reached; refusing authorize from %s", ip)
            raise AuthorizeError("temporarily_unavailable", "too many sign-ins in progress; try again shortly")
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = _Pending(
            client_id=client.client_id,
            client_name=client.client_name,
            params=params,
            expires_at=now + PENDING_TTL,
            source_ip=ip,
        )
        return f"{self.config.issuer_url}{LOGIN_PATH}?request={request_id}"

    def pending(self, request_id: str) -> _Pending | None:
        pending = self._pending.get(request_id or "")
        if pending is None or pending.expires_at < time.time():
            return None
        return pending

    def drop(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    async def approve(self, request_id: str) -> str | None:
        """Issue a code for a pending request; returns the client redirect URL,
        or None when the request is gone (double submit, approve racing deny)."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return None
        code = secrets.token_urlsafe(32)
        p = pending.params
        await self.store.add_tokens(
            [
                {
                    "token_hash": hash_token(code),
                    "kind": "code",
                    "client_id": pending.client_id,
                    "grant_id": uuid.uuid4().hex,
                    "client_name": pending.client_name,
                    "scopes": p.scopes or [DEFAULT_SCOPE],
                    "resource": self.config.resource_url,
                    "payload": {
                        "code_challenge": p.code_challenge,
                        "redirect_uri": str(p.redirect_uri),
                        "redirect_uri_provided_explicitly": p.redirect_uri_provided_explicitly,
                    },
                    "expires_at": time.time() + AUTH_CODE_TTL,
                }
            ]
        )
        await self.store.mark_client_approved(pending.client_id)
        logger.info("MCP OAuth: owner approved client %s (%s)", pending.client_id, pending.client_name)
        return construct_redirect_uri(str(p.redirect_uri), code=code, state=p.state)

    def deny(self, request_id: str) -> str | None:
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return None
        p = pending.params
        return construct_redirect_uri(
            str(p.redirect_uri), error="access_denied", error_description="The owner denied access", state=p.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await self._find_unreplayed(authorization_code, "code")
        if row is None or row["client_id"] != client.client_id:
            return None
        payload = row["payload"] or {}
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=payload["code_challenge"],
            redirect_uri=payload["redirect_uri"],
            redirect_uri_provided_explicitly=payload["redirect_uri_provided_explicitly"],
            resource=row["resource"],
            subject=OWNER_SUBJECT,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        row = await self.store.use_token(authorization_code.code, "code")
        if row is None:
            raise TokenError("invalid_grant", "authorization code already used or expired")
        return await self._issue(row["client_id"], row["client_name"], row["grant_id"], row["scopes"], time.time())

    async def _find_unreplayed(self, token: str, kind: str) -> dict[str, Any] | None:
        """The live *kind* row for *token*. A spent one means the token was
        replayed, so someone else may hold the grant: revoke all of it
        (OAuth 2.1 refresh-token reuse detection; RFC 6749 §4.1.2 for codes)."""
        row = await self.store.find_token(token)
        if row is None:
            return None
        if row["kind"] == f"{kind}_used":
            logger.warning(
                "MCP OAuth: %s reused for client %s; revoking its grant", kind, row["client_id"]
            )
            await self.store.revoke_grant(row["grant_id"])
            return None
        if row["kind"] != kind or row["expires_at"] < time.time():
            return None
        return row

    # -- tokens ------------------------------------------------------------

    async def _issue(
        self, client_id: str, client_name: str | None, grant_id: str, scopes: list[str], grant_started: float
    ) -> OAuthToken:
        access, refresh = f"imi_at_{secrets.token_urlsafe(32)}", f"imi_rt_{secrets.token_urlsafe(32)}"
        now = time.time()
        refresh_expires = min(now + self.config.refresh_ttl, grant_started + MAX_GRANT_AGE)
        common = {
            "client_id": client_id,
            "grant_id": grant_id,
            "client_name": client_name,
            "scopes": scopes,
            "resource": self.config.resource_url,
        }
        await self.store.add_tokens(
            [
                {"token_hash": hash_token(access), "kind": "access", "expires_at": now + self.config.access_ttl, **common},
                {
                    "token_hash": hash_token(refresh),
                    "kind": "refresh",
                    "expires_at": refresh_expires,
                    "payload": {"grant_started": grant_started},
                    **common,
                },
            ]
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self.config.access_ttl,
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = await self._find_unreplayed(refresh_token, "refresh")
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"]),
            resource=row["resource"],
            subject=OWNER_SUBJECT,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        row = await self.store.use_token(refresh_token.token, "refresh")
        if row is None:
            raise TokenError("invalid_grant", "refresh token already used or expired")
        # Rotation: the grant's outstanding access tokens go with the old refresh token.
        await self.store.revoke_grant(row["grant_id"], kinds=("access",))
        grant_started = (row["payload"] or {}).get("grant_started", time.time())
        return await self._issue(
            row["client_id"], row["client_name"], row["grant_id"], scopes or row["scopes"], grant_started
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        name = self.static_token_name(token)
        if name is not None:
            return static_access_token(token, name, self.config)
        row = await self.store.get_token(token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"]),
            resource=row["resource"],
            subject=OWNER_SUBJECT,
            claims={"client_name": row["client_name"] or row["client_id"]},
        )

    def static_token_name(self, token: str) -> str | None:
        return match_static_token(token, self.config)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if token.client_id.startswith("static:"):
            return  # static tokens are revoked by removing them from MCP_STATIC_TOKENS
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        row = await self.store.get_token(token.token, kind)
        if row is not None:
            await self.store.revoke_grant(row["grant_id"])
            logger.info("MCP OAuth: revoked grant for client %s", row["client_id"])


def match_static_token(token: str, config: McpAuthConfig) -> str | None:
    digest = hash_token(token)
    found = None
    for name, expected in config.static_tokens.items():
        if hmac.compare_digest(digest, expected):
            found = name
    return found


def static_access_token(token: str, name: str, config: McpAuthConfig) -> AccessToken:
    return AccessToken(
        token=token,
        client_id=f"static:{name}",
        scopes=[DEFAULT_SCOPE],
        resource=config.resource_url,
        subject=name,
        claims={"client_name": f"token-{name}"},
    )


class StaticTokenVerifier:
    """TokenVerifier for ``MCP_AUTH=token``."""

    def __init__(self, config: McpAuthConfig):
        self.config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        name = match_static_token(token, self.config)
        return static_access_token(token, name, self.config) if name is not None else None


class ProviderVerifier:
    """TokenVerifier for ``MCP_AUTH=oauth`` (static tokens + issued tokens)."""

    def __init__(self, provider: ImiOAuthProvider):
        self.provider = provider

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.provider.load_access_token(token)


def principal_name(scope_user: Any) -> str | None:
    """Verified caller label for the audit actor (ADR-009), if authenticated."""
    if isinstance(scope_user, AuthenticatedUser):
        claims = scope_user.access_token.claims or {}
        return claims.get("client_name") or scope_user.access_token.client_id
    return None


# ---------------------------------------------------------------------------
# Routes (host app)
# ---------------------------------------------------------------------------


def build_auth_routes(provider: ImiOAuthProvider) -> list[Route]:
    """Authorization-server, protected-resource and sign-in routes."""
    config = provider.config
    issuer = AnyHttpUrl(config.issuer_url)
    registration = ClientRegistrationOptions(enabled=True, default_scopes=[DEFAULT_SCOPE])
    revocation = RevocationOptions(enabled=True)

    sdk_routes = create_auth_routes(
        provider,
        issuer_url=issuer,
        client_registration_options=registration,
        revocation_options=revocation,
    )
    metadata = build_metadata(issuer, None, registration, revocation)
    base = config.issuer_url.rstrip("/")
    metadata.authorization_endpoint = AnyHttpUrl(f"{base}{OAUTH_PREFIX}/authorize")
    metadata.token_endpoint = AnyHttpUrl(f"{base}{OAUTH_PREFIX}/token")
    metadata.registration_endpoint = AnyHttpUrl(f"{base}{OAUTH_PREFIX}/register")
    metadata.revocation_endpoint = AnyHttpUrl(f"{base}{OAUTH_PREFIX}/revoke")
    metadata.token_endpoint_auth_methods_supported = ["client_secret_post", "client_secret_basic", "none"]
    metadata.revocation_endpoint_auth_methods_supported = ["client_secret_post", "client_secret_basic", "none"]

    routes: list[Route] = [
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
    ]
    # The SDK mounts its endpoints at the root; serve them under /oauth so the
    # whole flow is three path prefixes on a tunnel (see docs/mcp_access_tiers.md).
    for route in sdk_routes:
        if route.path.startswith("/.well-known/"):
            continue
        endpoint = _WithRequestIP(route.endpoint) if route.path == "/authorize" else route.endpoint
        routes.append(Route(f"{OAUTH_PREFIX}{route.path}", endpoint=endpoint, methods=route.methods))

    prm = create_protected_resource_routes(
        resource_url=AnyHttpUrl(config.resource_url),
        authorization_servers=[issuer],
        scopes_supported=[DEFAULT_SCOPE],
        resource_name="imi",
    )
    routes += prm
    if prm and prm[0].path != "/.well-known/oauth-protected-resource":
        routes.append(
            Route("/.well-known/oauth-protected-resource", endpoint=prm[0].endpoint, methods=prm[0].methods)
        )

    login = _LoginPage(provider)
    routes.append(Route(LOGIN_PATH, endpoint=login.get, methods=["GET"]))
    routes.append(
        Route(
            LOGIN_PATH,
            endpoint=_WithRequestIP(RequestBodyLimitMiddleware(request_response(login.post), LOGIN_BODY_LIMIT)),
            methods=["POST"],
        )
    )
    return routes


_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http://localhost:* http://127.0.0.1:* http://[::1]:*; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


class _LoginPage:
    def __init__(self, provider: ImiOAuthProvider):
        self.provider = provider

    async def get(self, request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        pending = self.provider.pending(request_id)
        if pending is None:
            return _expired()
        return _page("Connect to imi", _form(request_id, pending))

    async def post(self, request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        if form.get("action") == "deny":
            target = self.provider.deny(request_id)
            if target is None:
                return _expired()
            return RedirectResponse(target, status_code=302, headers=_PAGE_HEADERS)
        ip = _request_ip.get()
        guard = self.provider.guard
        passphrase = str(form.get("passphrase", ""))[:MAX_PASSPHRASE_LEN]
        async with guard.lock:
            pending = self.provider.pending(request_id)
            if pending is None:
                return _expired()
            if guard.locked(ip):
                return _page("Connect to imi", _form(request_id, pending, "Too many wrong passphrases. Try again in 15 minutes."), 429)
            ok = await asyncio.get_running_loop().run_in_executor(
                _SCRYPT_EXECUTOR, verify_passphrase, passphrase, self.provider.config.passphrase_hash
            )
            if not ok:
                guard.fail(ip)
                pending.failures += 1
                logger.warning("MCP OAuth: wrong passphrase from %s for client %s", ip, pending.client_id)
                if pending.failures >= MAX_FAILURES_PER_REQUEST:
                    self.provider.drop(request_id)
                    return _page(
                        "Sign-in cancelled",
                        "<p>Too many wrong passphrases for this sign-in. Start the connection again from your client.</p>",
                        401,
                    )
                return _page("Connect to imi", _form(request_id, pending, "Wrong passphrase."), 401)
            guard.succeed(ip)
            target = await self.provider.approve(request_id)
        if target is None:
            return _expired()
        return RedirectResponse(target, status_code=302, headers=_PAGE_HEADERS)


def _expired() -> HTMLResponse:
    return _page(
        "Sign-in expired",
        "<p>This sign-in link has expired. Start the connection again from your client.</p>",
        400,
    )


def _form(request_id: str, pending: _Pending, error: str | None = None) -> str:
    target = urlsplit(str(pending.params.redirect_uri))
    esc = html.escape
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    return f"""
<p>A client calling itself <strong>{esc(pending.client_name or "unnamed client")}</strong>
wants to use this imi instance's MCP tools. They can read and change the knowledge graph.</p>
<p>After approval you return to <strong>{esc(target.scheme)}://{esc(target.netloc)}</strong>.
Approve only if <em>you</em> just started connecting from there yourself. Never approve a
sign-in link someone sent you: whoever started this sign-in gets the access.</p>
{error_html}
<form method="post" action="{LOGIN_PATH}">
  <input type="hidden" name="request" value="{esc(request_id)}">
  <label>Owner passphrase <input type="password" name="passphrase" autocomplete="current-password" autofocus></label>
  <div class="actions">
    <button type="submit" name="action" value="approve">Approve</button>
    <button type="submit" name="action" value="deny" class="secondary">Deny</button>
  </div>
</form>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 30rem; margin: 4rem auto; padding: 0 1rem; color: #1a1a1a; }}
label {{ display: block; margin: 1rem 0; }}
input[type=password] {{ display: block; width: 100%; padding: .5rem; margin-top: .25rem; font-size: 1rem; box-sizing: border-box; }}
.actions {{ display: flex; gap: .5rem; }}
button {{ padding: .5rem 1rem; font-size: 1rem; cursor: pointer; }}
.secondary {{ background: none; }}
.error {{ color: #b00020; }}
</style></head>
<body><h1>{html.escape(title)}</h1>{body}</body></html>"""
    return HTMLResponse(doc, status_code=status, headers=_PAGE_HEADERS)


__all__ = [
    "ImiOAuthProvider",
    "McpAuthConfig",
    "OAuthStore",
    "ProviderVerifier",
    "StaticTokenVerifier",
    "build_auth_routes",
    "hash_passphrase",
    "hash_token",
    "load_auth_config",
    "parse_static_tokens",
    "principal_name",
    "public_paths",
    "verify_passphrase",
]
