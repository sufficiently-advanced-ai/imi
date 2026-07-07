"""Phase 4.2 tests — Postgres + RLS relational backend.

These exercise the backend's contract and policy DDL without a live Postgres
(engine creation is lazy; ``asyncpg`` is only needed when a session is opened).
The single-tenant SQLite default remains the path the rest of the suite uses.
"""

import pytest

from app.core.tenancy.backends.postgres import (
    TENANT_SCOPED_TABLES,
    PostgresRelationalBackend,
)


def test_rejects_non_postgres_url():
    with pytest.raises(ValueError):
        PostgresRelationalBackend("sqlite+aiosqlite:///./x.db")


def test_rejects_sync_postgres_driver():
    # Plain postgresql:// is a sync driver; create_async_engine needs an async
    # one, so it must be rejected up front (not fail later at connect).
    with pytest.raises(ValueError):
        PostgresRelationalBackend("postgresql://u:p@h/db")


def test_accepts_asyncpg_url_without_connecting():
    # Construction must not open a connection (no asyncpg needed yet).
    backend = PostgresRelationalBackend("postgresql+asyncpg://u:p@h/db")
    assert backend._engine is None


def test_session_factory_is_per_tenant_maker_and_memoized():
    backend = PostgresRelationalBackend("postgresql+asyncpg://u:p@h/db")
    maker_a = backend.session_factory_for("acme")
    # Callable (yields sessions) and memoized per tenant; distinct per tenant.
    assert callable(maker_a)
    assert backend.session_factory_for("acme") is maker_a
    assert backend.session_factory_for("other") is not maker_a


def test_rls_policy_sql_enforces_tenant_isolation():
    stmts = PostgresRelationalBackend.rls_policy_sql("users")
    joined = "\n".join(stmts)
    assert "ENABLE ROW LEVEL SECURITY" in joined
    # FORCE so the table owner is subject to RLS too (defense in depth).
    assert "FORCE ROW LEVEL SECURITY" in joined
    assert "CREATE POLICY users_tenant_isolation ON users" in joined
    # Both USING (reads) and WITH CHECK (writes) keyed off the GUC.
    assert joined.count("current_setting('app.tenant_id', true)") == 2
    assert "USING" in joined and "WITH CHECK" in joined


def test_policy_is_idempotent():
    stmts = PostgresRelationalBackend.rls_policy_sql("user_sessions")
    assert any(s.startswith("DROP POLICY IF EXISTS") for s in stmts)


def test_tenant_scoped_tables_match_models():
    # The backend's table list must match the models carrying TenantScopedMixin.
    from app.user_models.db_models import (
        User,
        UserSession,
    )

    model_tables = {
        m.__tablename__
        for m in (User, UserSession)
    }
    assert set(TENANT_SCOPED_TABLES) == model_tables


def test_models_have_tenant_id_column_defaulting_to_default():
    from app.user_models.db_models import User

    col = User.__table__.c.tenant_id
    assert col is not None
    assert not col.nullable
    # server_default backfills existing rows / single-tenant deployments.
    assert col.server_default is not None
