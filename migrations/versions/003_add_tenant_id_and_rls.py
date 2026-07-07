"""Add tenant_id columns and Row-Level-Security policies (Phase 4.2)

Adds a ``tenant_id`` column to every tenant-scoped table and, on PostgreSQL,
enables RLS policies confining each row to its tenant (keyed off the
``app.tenant_id`` GUC set by ``PostgresRelationalBackend``).

Behavior preservation: existing rows are backfilled with ``'default'`` (the
single-tenant id), so a SQLite single-tenant deployment is unaffected. RLS DDL
is **Postgres-only** — SQLite has no row-level security and those statements are
skipped, so the community/SQLite path keeps working unchanged.

Revision ID: 003
Revises: 002
Create Date: 2026-05-31 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '001'
branch_labels = None
depends_on = None

# Single source of truth lives in the backend module; imported lazily in the
# functions so Alembic can load this script without the app package on older
# tooling paths.
TENANT_SCOPED_TABLES = (
    "users",
    "user_sessions",
)
DEFAULT_TENANT_ID = "default"


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)

    # Lineage-tolerant guards: legacy SQLite has the tables without tenant_id;
    # a create_all()'d schema already has the columns. Skip what is absent /
    # already present instead of failing.
    present_tables = [t for t in TENANT_SCOPED_TABLES if inspector.has_table(t)]
    skipped = sorted(set(TENANT_SCOPED_TABLES) - set(present_tables))
    if skipped:
        print(f"[003] tables not present, skipped (created later by the app): {skipped}")

    for table in present_tables:
        if any(c["name"] == "tenant_id" for c in inspector.get_columns(table)):
            continue
        # Add as nullable + server_default so existing rows backfill to
        # 'default', then tighten to NOT NULL.
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                sa.String(length=255),
                nullable=True,
                server_default=DEFAULT_TENANT_ID,
                index=True,
            ),
        )
        op.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL").bindparams(
                tid=DEFAULT_TENANT_ID
            )
        )
        # SQLite's limited ALTER cannot easily set NOT NULL post-hoc; enforce it
        # only where supported (Postgres). The server_default keeps SQLite rows
        # populated regardless.
        if is_postgres:
            op.alter_column(table, "tenant_id", nullable=False)

    if is_postgres:
        from app.core.tenancy.backends.postgres import PostgresRelationalBackend

        for table in present_tables:
            for stmt in PostgresRelationalBackend.rls_policy_sql(table):
                op.execute(stmt)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        for table in TENANT_SCOPED_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    for table in TENANT_SCOPED_TABLES:
        op.drop_column(table, "tenant_id")
