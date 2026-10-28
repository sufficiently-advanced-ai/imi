"""MCP OAuth clients and tokens (ADR-010)

Registered OAuth clients and hashed authorization codes / access / refresh
tokens for the MCP authorization server (``MCP_AUTH=oauth``).

The app also creates these tables at startup (``Base.metadata.create_all`` in
``app/database.py`` and ``OAuthStore.open``), so an existing deployment needs
no manual step; this revision keeps alembic-managed databases in step.

Revision ID: 008
Revises: 007
Create Date: 2026-10-28 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("mcp_oauth_clients"):
        op.create_table(
            "mcp_oauth_clients",
            sa.Column("client_id", sa.String(length=64), primary_key=True),
            sa.Column("client_name", sa.String(length=256), nullable=True),
            sa.Column("client_info", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_mcp_oauth_clients_created_at", "mcp_oauth_clients", ["created_at"]
        )

    if not inspector.has_table("mcp_oauth_tokens"):
        op.create_table(
            "mcp_oauth_tokens",
            sa.Column("token_hash", sa.String(length=64), primary_key=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("client_id", sa.String(length=64), nullable=False),
            sa.Column("grant_id", sa.String(length=64), nullable=False),
            sa.Column("client_name", sa.String(length=256), nullable=True),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("resource", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_mcp_oauth_tokens_client_id", "mcp_oauth_tokens", ["client_id"])
        op.create_index("ix_mcp_oauth_tokens_grant_id", "mcp_oauth_tokens", ["grant_id"])
        op.create_index("ix_mcp_oauth_tokens_expires_at", "mcp_oauth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_oauth_clients")
