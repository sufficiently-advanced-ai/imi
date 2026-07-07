"""Judge decision events table (OB1 absorption Phase 4)

Compact judgment telemetry: what action was proposed, what the judge decided
(allow/block/revise/escalate — ADR-001 gate vocabulary), which memories were
used, and which were written back. Raw tool arguments are never stored
(arguments_digest only, per the OB1 write-back rules).

Named ``judge_decision_events`` to avoid any collision with the DecisionRecord
domain concept — this is runtime telemetry, not knowledge.

Revision ID: 006
Revises: 005
Create Date: 2026-07-03 22:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)

    if not inspector.has_table("judge_decision_events"):
        op.create_table(
            "judge_decision_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.String(length=255),
                nullable=False,
                server_default="default",
            ),
            sa.Column("action_id", sa.String(length=64), nullable=False),
            # No unique: dedup rides on (tenant_id, action_id) — a global
            # unique key would collide across tenants.
            sa.Column(
                "idempotency_key", sa.String(length=128), nullable=True, index=True
            ),
            sa.Column("risk_class", sa.String(length=32), nullable=False),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("reasoning_summary", sa.Text(), nullable=False),
            sa.Column(
                "confidence",
                sa.String(length=8),
                nullable=False,
                server_default="medium",
            ),
            sa.Column("judge", sa.JSON(), nullable=False),
            sa.Column("checks", sa.JSON(), nullable=False),
            sa.Column("memory_used", sa.JSON(), nullable=False),
            sa.Column("memory_written", sa.JSON(), nullable=False),
            sa.Column("arguments_digest", sa.String(length=64), nullable=True),
            sa.Column("expected_consequence", sa.Text(), nullable=True),
            sa.Column("rollback", sa.JSON(), nullable=True),
            sa.Column("recall_request_id", sa.String(length=64), nullable=True),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("runtime_name", sa.String(length=255), nullable=True),
            sa.Column("task_id", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("tenant_id", "action_id"),
        )
        op.create_index(
            "ix_judge_decision_events_task_id", "judge_decision_events", ["task_id"]
        )
        op.create_index(
            "ix_judge_decision_events_tenant_created",
            "judge_decision_events",
            ["tenant_id", "created_at"],
        )

    if is_postgres:
        from app.core.tenancy.backends.postgres import PostgresRelationalBackend

        for stmt in PostgresRelationalBackend.rls_policy_sql("judge_decision_events"):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_table("judge_decision_events")
