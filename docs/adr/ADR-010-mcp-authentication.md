# ADR-010: MCP Authentication — Static Tokens and a Single-Owner OAuth Server

## Status

Accepted (2026-10-28). Amends ADR-008 ("authentication is a hosted-edition feature") for the
single-owner case.

## Context

ADR-008 made MCP reachable from other machines but kept it unauthenticated, so the remote tier
could only be used on a private network. Cloud clients that cannot join that network
cannot use it at all. The main example is claude.ai custom connectors, and with them Claude Duet,
which runs on Anthropic's cloud and uses the connectors on the user's claude.ai account:

- A claude.ai custom connector must be a **public HTTPS URL**. Anthropic's private-network
  option (MCP tunnels) is Enterprise-only.
- A claude.ai connector authenticates with **OAuth 2.1** (dynamic client registration or
  Claude's client metadata document, PKCE). Static request headers are a limited beta, and
  "no sign-in" on a public URL would hand every tool, including graph mutations, to anyone
  who finds it.
- Native clients (Claude Code, the API's MCP connector, agent SDKs) can send a fixed
  `Authorization: Bearer` header.

The community edition has no user accounts (`AUTH_MODE=none|demo`), so OAuth sign-in cannot be
delegated to an identity provider.

## Decision

Add `MCP_AUTH` with three modes. The default, `none`, is unchanged.

| Mode | MCP requests need | Extra surface |
|---|---|---|
| `none` | nothing (ADR-008) | — |
| `token` | a static bearer token listed in `MCP_STATIC_TOKENS` (`name:sha256`) | — |
| `oauth` | a static token **or** an OAuth access token issued by imi | authorization server under `/oauth/*` + `/.well-known/*` |

The OAuth server is built from the MCP SDK's `mcp.server.auth` (metadata, authorize, token,
register and revoke handlers, PKCE and redirect-URI checks, bearer middleware that binds each
MCP session to its token's principal). imi supplies the storage and the sign-in step
(`app/services/mcp_oauth.py`):

- **One owner, one passphrase.** `/oauth/authorize` parks the request and shows imi's sign-in
  page. The owner approves by typing the passphrase whose scrypt hash is
  `MCP_OAUTH_PASSPHRASE_HASH`. Five wrong passphrases in 15 minutes lock sign-in for 15
  minutes.
- **Codes only go to Claude or to localhost.** Dynamic registration accepts only redirect URIs
  in `MCP_OAUTH_REDIRECT_URIS` (claude.ai's callback by default) or loopback `http` URIs.
  Someone who registers a client still cannot receive a code without the passphrase, and
  cannot send one to a host they control.
- **Tokens are bound to `MCP_PUBLIC_URL`** (RFC 8707). The bearer middleware refuses tokens
  issued for any other resource.
- **Stored hashed.** Codes, access tokens (1 h) and refresh tokens (30 d, rotated on every use)
  are stored as SHA-256 in `mcp_oauth_tokens`. Revoking any token revokes its grant.
  `scripts/mcp_auth.py` lists clients and revokes grants.
- **Fail closed.** An enabled mode that is misconfigured (no tokens, no passphrase hash, no
  `https` public URL) stops the app at startup instead of serving MCP with less protection.
- **Verified audit actor.** When a token authenticates the request, the ADR-009 actor is
  `mcp:<token name or OAuth client name>` instead of the client's self-reported
  `clientInfo.name`.

## Consequences

- imi can be a claude.ai custom connector (and so be used from Claude Duet) behind a public
  tunnel such as Tailscale Funnel. Only the MCP path, `/.well-known/` and `/oauth/` need to be
  exposed; the UI and REST API stay private.
- Turning auth on applies to every MCP client, local ones included, because tunnel traffic
  arrives from loopback too. Existing clients need a static token
  (`scripts/mcp_auth.py new-token`).
- The registered OAuth client name is self-chosen by the client. It labels the audit actor
  but is not proof of identity: the passphrase approval is.
- The global sign-in lockout lets someone who can reach the page delay the owner's sign-in
  by up to 15 minutes. Tokens that were already issued keep working during a lockout.
- Authentication says who may call. ADR-002 still decides what a caller may do, and
  `MCP_REVIEW_ACTIONS=false` is still recommended for any tier reachable by others.
- Per-user identity, scopes per tool and SSO remain hosted-edition scope.
