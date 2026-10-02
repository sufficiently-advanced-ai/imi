# ADR-008: Remote MCP Access — Reaching imi From Cloud Clients

## Status

Proposed (2026-10-01).

## Context

imi's MCP server is an SSE transport mounted at `/api/mcp/sse` (`mcp_server.py:79`), bound to
loopback by the shipped compose file, with DNS-rebinding protection and a host allowlist
(`MCP_ALLOWED_HOSTS`) — and no authentication. `AUTH_MODE` governs the web UI; it does not
cover MCP.

That is right for a local client (Claude Code, Claude Desktop on the same machine). It is the
wrong shape for where the use cases live (ADR-005): Claude sessions that run in the cloud, on a
schedule, or for a team.

Today the only way a cloud Claude session reaches imi is indirect: the user's desktop app
relays its locally configured MCP servers to the session. It works, but:

- the machine must be awake, online, with the desktop app open — scheduled tasks fail
  silently overnight;
- only that one user can reach the instance — "team context" is impossible;
- the setup step is the hardest one in any guide to write and the most likely to lose a reader.

Separately, the MCP specification has moved from HTTP+SSE to Streamable HTTP as its standard
remote transport; new clients increasingly expect the latter.

## Decision

Three supported access tiers, documented as such in every pack guide:

| Tier | Who | Transport | Auth | Status |
|---|---|---|---|---|
| **Local** | one user, same machine | SSE (today) + Streamable HTTP | none; loopback only | supported today |
| **Relayed** | one user, cloud sessions via their desktop app | as Local | none (loopback) | supported today; document it |
| **Remote** | scheduled cloud tasks, teams on a private network | Streamable HTTP | none in imi; operator's network | **new** (opt-in, off by default) |

### Remote tier

- Add a Streamable HTTP endpoint alongside SSE; keep SSE for existing clients. Both are
  served in every tier; Local clients can use either.
- **Community edition ships no MCP authentication.** It targets local and private-network
  deployments (a home server, a VPN or tailnet). Exposing imi beyond that is the operator's
  decision and the operator's access control (a tunnel or reverse proxy that authenticates).
- **Defaults are closed:**

  | Setting | Default | Remote tier |
  |---|---|---|
  | compose port binding | `127.0.0.1` | operator binds the private-network interface |
  | `MCP_ALLOWED_HOSTS` | localhost only | the private hostname(s) clients use |
  | `MCP_PUBLIC_URL` | unset → remote tier off | the URL clients use; `http://` is accepted (a tailnet is already encrypted) with a startup warning that imi has no auth |

  Nothing beyond loopback answers until the operator sets all three.
- Pack guides document this as "how I run it", not a hardened recipe: the reader owns the
  exposure.

### Hosted edition

Authentication — bearer tokens with scopes (`read` / `write` / `curate`), OAuth, SSO,
per-user identity and per-actor audit — is a hosted/commercial feature, layered through the
existing `create_app(extra_routers=...)` and tenancy seams. Not built in community.

### What does not change

Network access answers *who can reach the server*. ADR-002 answers *what anything reaching it
may do*. No caller — local, relayed or remote — can mint instruction-grade memory; only a human
review action can. MCP captures enter as `provenance_status="imported"`, which is in ADR-002's
instruction-eligible set, so a test pins that nothing on the MCP surface sets
`can_use_as_instruction` without a review action.

## Resolved questions

- **Auth in community (2026-10-02):** none. Community is a self-hosted reference
  implementation; operators who need authentication get it from their network layer or from
  the hosted edition.
- **Library visibility by scope:** moot without scopes in community.

## Consequences

- Scheduled tasks and team use become possible without the user's laptop in the loop.
- Exposing imi beyond loopback becomes a documented configuration instead of an undocumented
  hack — but an unauthenticated one. Anyone who can reach the endpoint can call every tool,
  including `graph_*` mutations and `delete_signal`. The guide must say so plainly.
- ADR-002 still holds for every caller: reaching the server never confers instruction
  authority; only a human review action does.
