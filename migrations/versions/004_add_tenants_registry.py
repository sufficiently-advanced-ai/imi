"""Add tenants registry table (Phase 4.4)

The control-plane table holding per-tenant corpus/domain/graph config. Here ``tenant_id`` is the table's
**primary key / identity** (one row per tenant) — not a tenant-scoping column
like on the data tables — so this table is not subject to RLS (the control plane
reads across all tenants).

Revision ID: 004
Revises: 003
Create Date: 2026-05-31 22:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(length=255), primary_key=True),
        sa.Column("git_repo_url", sa.String(length=1024), nullable=True),
        sa.Column("git_token_encrypted", sa.String(length=2048), nullable=True),
        sa.Column("domain_config", sa.String(length=255), nullable=True),
        sa.Column("graph_scope", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="provisioning"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tenants")
