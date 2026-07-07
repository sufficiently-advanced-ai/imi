"""Hosted Postgres + Row-Level-Security RelationalBackend (Phase 4.2).

Implements the ``RelationalBackend`` interface from ``base.py`` against a single
shared Postgres database, isolating tenants with Row-Level Security:

  * Every tenant-scoped table carries a ``tenant_id`` column (see the Alembic
    migration ``003_add_tenant_id_and_rls``).
  * An RLS policy on each table restricts visible rows to
    ``tenant_id = current_setting('app.tenant_id')``.
  * This backend sets that GUC per checked-out connection
    (``SET app.tenant_id = '<id>'``), so RLS is enforced **even if an
    application query forgets its WHERE clause** — defense in depth beneath the
    app-level filter.

This is the *hosted* implementation. Core continues to ship the SQLite
``DefaultRelationalBackend``; nothing here runs in single-tenant mode unless a
deployment installs it via ``configure_tenancy(backends=...)``.

NOTE: requires ``asyncpg`` (not in the base requirements yet) and a running
Postgres. Wiring it into a live deployment — adding the driver, pointing
``DATABASE_URL`` at Postgres, running the migration — is a deployment step; this
module is the code + policy that step turns on.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

# asyncio-compatible Postgres drivers create_async_engine can use.
_ASYNC_PG_DRIVERS = {"asyncpg", "psycopg"}


class _TenantScopedSessionMaker:
    """Callable that yields sessions which set this tenant's RLS GUC.

    Returned by ``session_factory_for`` in place of a bare ``async_sessionmaker``.
    Each ``async with maker() as session`` opens a real ``AsyncSession`` and
    issues ``set_config('app.tenant_id', <id>, true)`` (transaction-local) before
    yielding, so RLS confines the session to this tenant's rows. Crucially this
    is **per session** — it does not register cumulative class-level event
    listeners (which would stack across tenants and break isolation).
    """

    def __init__(self, base_factory_provider, tenant_id: str) -> None:
        # provider() -> async_sessionmaker; called lazily so requesting a maker
        # never forces engine/driver creation (the driver loads on first use).
        self._provider = base_factory_provider
        self._tenant_id = tenant_id

    def __call__(self) -> _TenantSession:
        return _TenantSession(self._provider(), self._tenant_id)


class _TenantSession:
    """Async context manager wrapping an AsyncSession + per-session GUC set."""

    def __init__(self, base_factory: async_sessionmaker[AsyncSession], tenant_id: str) -> None:
        self._base_factory = base_factory
        self._tenant_id = tenant_id
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self._session = self._base_factory()
        await self._session.__aenter__()
        # Transaction-local GUC: scoped to this session's transaction, so it
        # cannot bleed to another tenant on a pooled connection.
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self._tenant_id}
        )
        return self._session

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._session.__aexit__(*exc)


class PostgresRelationalBackend:
    """Shared-Postgres + RLS relational backend.

    A single engine is shared across tenants; isolation is by the
    ``app.tenant_id`` GUC set per session, which the RLS policies key off.
    """

    def __init__(self, database_url: str, *, echo: bool = False, **engine_kwargs: Any) -> None:
        # Must be an asyncio-compatible driver — create_async_engine needs one,
        # and a sync driver scheme (e.g. plain "postgresql://") would only fail
        # later at connect time.
        driver = make_url(database_url).drivername  # e.g. "postgresql+asyncpg"
        backend_name, _, driver_name = driver.partition("+")
        if backend_name != "postgresql" or driver_name not in _ASYNC_PG_DRIVERS:
            raise ValueError(
                "PostgresRelationalBackend requires an async Postgres driver "
                f"(postgresql+asyncpg / postgresql+psycopg); got {driver!r}"
            )
        self._database_url = database_url
        self._engine_kwargs = {"echo": echo, "pool_pre_ping": True, **engine_kwargs}
        self._engine: AsyncEngine | None = None
        self._base_factory: async_sessionmaker[AsyncSession] | None = None
        self._tenant_makers: dict[str, _TenantScopedSessionMaker] = {}

    def _ensure_base_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._base_factory is None:
            self._engine = create_async_engine(self._database_url, **self._engine_kwargs)
            self._base_factory = async_sessionmaker(
                self._engine, class_=AsyncSession, expire_on_commit=False
            )
            logger.info("PostgresRelationalBackend engine + session factory created")
        return self._base_factory

    def session_factory_for(self, tenant_id: str) -> _TenantScopedSessionMaker:
        """Return a per-tenant session maker that stamps the RLS GUC per session."""
        maker = self._tenant_makers.get(tenant_id)
        if maker is None:
            # Pass the lazy provider (bound method), not its result, so making a
            # maker never triggers engine/driver creation.
            maker = _TenantScopedSessionMaker(self._ensure_base_factory, tenant_id)
            self._tenant_makers[tenant_id] = maker
        return maker

    @staticmethod
    def rls_policy_sql(table: str) -> list[str]:
        """Return the idempotent DDL to enable tenant RLS on ``table``.

        Used by the Alembic migration; exposed here so the policy text has a
        single source of truth and can be unit-asserted without a live database.
        """
        policy = f"{table}_tenant_isolation"
        return [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS {policy} ON {table};",
            (
                f"CREATE POLICY {policy} ON {table} "
                "USING (tenant_id = current_setting('app.tenant_id', true)) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id', true));"
            ),
        ]


# Tables that carry a tenant_id and get an RLS policy (Phase 4.2 scope).
TENANT_SCOPED_TABLES = (
    "users",
    "user_sessions",
)
