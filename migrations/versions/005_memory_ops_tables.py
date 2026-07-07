"""Memory operations telemetry tables (OB1 absorption Phase 3)

Creates the SQL side of the hybrid storage split: recall traces, per-item
ranking/usage snapshots, and the append-only memory event log. Knowledge
records stay in the git corpus; these tables hold the high-churn operational
data that would bloat the repo (plan storage decision).

``record_id`` columns carry NO foreign key — governed records live in the
corpus, not SQL, and traces must survive record deletion.

RLS on Postgres mirrors migration 003 (tenant GUC policies on the
tenant-scoped tables); SQLite skips RLS unchanged.

Revision ID: 005
Revises: 004
Create Date: 2026-07-03 21:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = (
    "memory_recall_traces",
    "memory_recall_items",
    "memory_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)

    if not inspector.has_table("memory_recall_traces"):
        op.create_table(
            "memory_recall_traces",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column(
                "tenant_id",
                sa.String(length=255),
                nullable=False,
                server_default="default",
            ),
            sa.Column("workspace_id", sa.String(length=255), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column(
                "surface",
                sa.String(length=32),
                nullable=False,
                server_default="agent_recall",
            ),
            sa.Column("runtime_name", sa.String(length=255), nullable=True),
            sa.Column("runtime_version", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=255), nullable=True),
            sa.Column("flow_id", sa.String(length=255), nullable=True),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column(
                "authority",
                sa.String(length=16),
                nullable=False,
                server_default="evidence",
            ),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("request_scope", sa.JSON(), nullable=False),
            sa.Column("response_policy", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_memory_recall_traces_tenant_created",
            "memory_recall_traces",
            ["tenant_id", "created_at"],
        )
        op.create_index(
            "ix_memory_recall_traces_task_id", "memory_recall_traces", ["task_id"]
        )

    if not inspector.has_table("memory_recall_items"):
        op.create_table(
            "memory_recall_items",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.String(length=255),
                nullable=False,
                server_default="default",
            ),
            sa.Column(
                "trace_id",
                sa.String(length=36),
                sa.ForeignKey("memory_recall_traces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("record_id", sa.String(length=64), nullable=False),
            sa.Column("record_kind", sa.String(length=32), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("similarity", sa.Float(), nullable=True),
            sa.Column("ranking_score", sa.Float(), nullable=True),
            sa.Column(
                "returned", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column("used", sa.Boolean(), nullable=True),
            sa.Column("used_as", sa.String(length=16), nullable=True),
            sa.Column("ignored_reason", sa.Text(), nullable=True),
            sa.Column("use_policy_snapshot", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("trace_id", "record_id"),
        )
        op.create_index(
            "ix_memory_recall_items_trace_id", "memory_recall_items", ["trace_id"]
        )

    if not inspector.has_table("memory_events"):
        op.create_table(
            "memory_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.String(length=255),
                nullable=False,
                server_default="default",
            ),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("record_id", sa.String(length=64), nullable=True),
            sa.Column("record_kind", sa.String(length=32), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column(
                "actor_kind",
                sa.String(length=16),
                nullable=False,
                server_default="system",
            ),
            sa.Column("actor_label", sa.String(length=255), nullable=True),
            sa.Column("runtime_name", sa.String(length=255), nullable=True),
            sa.Column("task_id", sa.String(length=255), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_memory_events_tenant_created",
            "memory_events",
            ["tenant_id", "created_at"],
        )
        op.create_index(
            "ix_memory_events_event_type", "memory_events", ["event_type"]
        )

    if is_postgres:
        from app.core.tenancy.backends.postgres import PostgresRelationalBackend

        for table in TENANT_SCOPED_TABLES:
            for stmt in PostgresRelationalBackend.rls_policy_sql(table):
                op.execute(stmt)


def downgrade() -> None:
    op.drop_table("memory_recall_items")
    op.drop_table("memory_events")
    op.drop_table("memory_recall_traces")
