"""SQLAlchemy models for MCP OAuth (ADR-010): registered clients and grants.

``mcp_oauth_clients`` holds what dynamic client registration (RFC 7591)
returned to each client, as JSON. ``mcp_oauth_tokens`` holds authorization
codes, access tokens and refresh tokens, keyed by the SHA-256 of the token so
the raw value is never stored; rows issued from one sign-in share a
``grant_id`` so revoking any token revokes the whole grant.

Written through :class:`app.services.mcp_oauth.OAuthStore`. Operational state
only, like ``ingest_jobs``: losing it signs every connector out.
"""

from sqlalchemy import JSON, Column, DateTime, Float, String, Text

from app.database import Base


class McpOAuthClient(Base):
    __tablename__ = "mcp_oauth_clients"

    client_id = Column(String(64), primary_key=True)
    client_name = Column(String(256), nullable=True)
    # OAuthClientInformationFull as JSON (includes the client secret, which the
    # SDK's client authenticator compares against at /token).
    client_info = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    # Set when the owner first approves a sign-in for this client. Clients
    # never approved are pruned (registration is open to anyone).
    approved_at = Column(DateTime(timezone=True), nullable=True)


class McpOAuthToken(Base):
    __tablename__ = "mcp_oauth_tokens"

    token_hash = Column(String(64), primary_key=True)
    # code | access | refresh, or code_used / refresh_used: a spent single-use
    # token kept until its expiry so a replay is detected and revokes the grant.
    kind = Column(String(16), nullable=False)
    client_id = Column(String(64), nullable=False, index=True)
    grant_id = Column(String(64), nullable=False, index=True)
    client_name = Column(String(256), nullable=True)
    scopes = Column(JSON, nullable=False, default=list)
    resource = Column(Text, nullable=True)
    # Codes: PKCE challenge and redirect URI binding. Refresh tokens: grant start.
    payload = Column(JSON, nullable=True)
    expires_at = Column(Float, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
