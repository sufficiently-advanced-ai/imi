"""Durable ingestion jobs table

Replaces the process-local ingest job dict: job status, phase progress and
outcomes, result, and the stored request (for retry and restart recovery).
Dedup by ``source_id`` / content hash reads the indexed columns.

The app also creates this table at startup (``Base.metadata.create_all`` in
``app/database.py`` and ``JobStore.open``), so an existing deployment needs no
manual step; this revision keeps alembic-managed databases in step.

Revision ID: 007
Revises: 006
Create Date: 2026-10-23 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("ingest_jobs"):
        op.create_table(
            "ingest_jobs",
            sa.Column("job_id", sa.String(length=64), primary_key=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("content_type", sa.String(length=64), nullable=True),
            sa.Column("source_id", sa.String(length=512), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("channel", sa.String(length=64), nullable=True),
            sa.Column("request_payload", sa.Text(), nullable=True),
            sa.Column("phases_completed", sa.JSON(), nullable=False),
            sa.Column("phase_outcomes", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "interrupted", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_ingest_jobs_status", "ingest_jobs", ["status"])
        op.create_index("ix_ingest_jobs_source_id", "ingest_jobs", ["source_id"])
        op.create_index("ix_ingest_jobs_content_hash", "ingest_jobs", ["content_hash"])
        op.create_index("ix_ingest_jobs_created_at", "ingest_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_table("ingest_jobs")
