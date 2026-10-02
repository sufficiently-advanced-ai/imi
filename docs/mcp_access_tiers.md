# MCP Access Tiers — Local, Relayed, Remote

> **Audience:** operators connecting MCP clients to an imi instance ·
> **Decision record:** [ADR-008](adr/ADR-008-remote-mcp-access.md) ·
> **Source of truth:** `app/routes/mcp_server.py` (`build_mcp_app`, `mount_mcp`,
> `_build_allowed_hosts`, `log_mcp_access_tier`), `app/config.py` (`MCP_ALLOWED_HOSTS`,
> `MCP_PUBLIC_URL`) · **See also:** [MCP & API](architecture/mcp-and-api.md)

> **Warning: imi's MCP server has no authentication.** Anyone who can open a connection to
> the endpoint can call every tool, including the `graph_*` mutations, `delete_signal`, and
> `update_signal` review actions. The community edition is meant for one machine or a
> private network (a home server, a VPN or tailnet). If you expose it beyond that, the access
> control is yours: a tunnel or reverse proxy that authenticates. Authentication (tokens,
> OAuth, SSO, per-user audit) is a hosted-edition feature.

## Endpoints

imi serves the same tool set over two MCP transports, behind the same DNS-rebinding
protection and Host-header allowlist:

| Transport | URL (default port) | Use it for |
|---|---|---|
| **Streamable HTTP** (current MCP standard) | `http://localhost:8080/api/mcp/http` | new clients; required for the remote tier |
| **HTTP+SSE** (legacy) | `http://localhost:8080/api/mcp/sse` | existing configs; unchanged |

Both run in every tier. Local clients can use either one.

## The three tiers

| Tier | Who | Transport | Auth | Status |
|---|---|---|---|---|
| **Local** | one user, same machine | SSE or Streamable HTTP | none; loopback only | default |
| **Relayed** | one user, cloud sessions via their desktop app | as Local | none (loopback) | works with the defaults |
| **Remote** | scheduled tasks and teammates on a private network | Streamable HTTP | none in imi; the operator's network | opt-in, off by default |

### Defaults are closed

| Setting | Default | Remote tier |
|---|---|---|
| compose port binding (`BIND_ADDRESS`) | `127.0.0.1` | the private-network interface (or keep loopback and use `tailscale serve`, below) |
| `MCP_ALLOWED_HOSTS` | localhost only | the private hostname(s) clients use |
| `MCP_PUBLIC_URL` | unset, so the remote tier is off | the URL clients use. `http://` is accepted (a tailnet already encrypts traffic) and logs a startup warning |

With the defaults, only loopback can connect. Requests with any other `Host` header get
`421 Invalid Host header`.

---

## Local tier

Nothing to configure. Connect a client on the same machine.

**Claude Code**:

```bash
# Streamable HTTP (recommended for new setups)
claude mcp add --transport http imi http://localhost:8080/api/mcp/http
# or the legacy SSE transport
claude mcp add --transport sse imi http://localhost:8080/api/mcp/sse
```

You can also use a project `.mcp.json`. `.mcp.json.example` ships the SSE form, and the
Streamable HTTP form is:

```json
{ "mcpServers": { "imi": { "type": "http", "url": "http://localhost:8080/api/mcp/http" } } }
```

**Claude Desktop**: its `claude_desktop_config.json` launches local (stdio) servers. To reach
imi's HTTP endpoint from there, use a stdio-to-HTTP bridge such as `mcp-remote`:

```json
{
  "mcpServers": {
    "imi": { "command": "npx", "args": ["mcp-remote", "http://localhost:8080/api/mcp/http"] }
  }
}
```

**Smoke test** (needs both `Accept` types; expect `200` and an `mcp-session-id` header):

```bash
curl -si http://localhost:8080/api/mcp/http \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  | head -20
```

## Relayed tier

A Claude session that runs in the cloud can use the MCP servers that your **desktop app** has
configured. The desktop app relays the calls to imi over loopback, so imi's configuration is
the same as the Local tier. Set imi up in the desktop app as described above.

Limits of the relayed tier:

- The machine must be awake and online, with the desktop app open. Scheduled tasks that run
  while it's asleep fail without any error you'll see.
- Only that one user can reach the instance.

Use the remote tier if either limit is a problem.

## Remote tier

Use this tier for clients on **your private network** that are not on the imi machine, such
as a teammate's laptop on the same tailnet, an always-on machine that runs scheduled agents,
or a cloud environment that you've joined to the tailnet. A client that can't reach your
private network can't reach imi in this tier. That's the point of the tier, because imi
has no authentication of its own.

The examples use a tailnet hostname placeholder, `imi.example.ts.net`.

### 1. Make imi reachable on the private network

Pick one of these options.

- **Option A: keep the loopback binding and let the VPN proxy it.** With Tailscale, for
  example, `tailscale serve --bg 8080` serves `https://imi.example.ts.net` to tailnet members
  only and forwards it to `127.0.0.1:8080`. The compose binding stays `127.0.0.1`.
- **Option B: bind the private interface.** Set `BIND_ADDRESS` in `.env` to the host's
  VPN/tailnet IP (for example `100.64.0.10`). Never use `0.0.0.0` or a public IP.

### 2. Tell imi which name clients use

In `.env`:

```bash
# Option A (tailscale serve, HTTPS on 443):
MCP_ALLOWED_HOSTS=imi.example.ts.net
MCP_PUBLIC_URL=https://imi.example.ts.net/api/mcp/http

# Option B (direct bind, plain HTTP inside the tailnet):
BIND_ADDRESS=100.64.0.10
MCP_ALLOWED_HOSTS=imi.example.ts.net:8080
MCP_PUBLIC_URL=http://imi.example.ts.net:8080/api/mcp/http
```

imi adds the `host[:port]` of `MCP_PUBLIC_URL` to the Host allowlist itself.
`MCP_ALLOWED_HOSTS` is still the place to list any *other* names clients use, such as a
short MagicDNS name or the raw IP. Restart the app afterwards. Domain and settings changes
take effect only on restart.

### 3. Check the startup log

With `MCP_PUBLIC_URL` set, imi logs once at startup that the remote tier is **enabled and
unauthenticated**. If the URL is `http://`, it logs a second warning. An `MCP_PUBLIC_URL`
that can't be parsed (it must be `http(s)://host[:port][/path]`) is logged as an error and
ignored, and the remote tier stays off.

### 4. Connect clients

```bash
claude mcp add --transport http imi https://imi.example.ts.net/api/mcp/http
```

Run the curl smoke test above against the public URL from another machine on the network.

### Upgrading an existing private-network deployment

If you already reach imi over a VPN or tailnet with `MCP_ALLOWED_HOSTS` set to a non-local
name and no `MCP_PUBLIC_URL`, that setup **keeps working** unchanged on both transports.
At startup, imi logs a warning that recommends setting `MCP_PUBLIC_URL` so that the remote
tier is declared explicitly.

---

## What network access does not change

Network access decides *who can reach the server*.
[ADR-002](adr/ADR-002-evidence-instruction-authority-gate.md) decides *what any caller may
do*. No tool accepts `provenance_status`, `review_status` or `can_use_as_*` as input.
`capture_thought` and `memory_writeback` always write evidence-grade, pending-review memory.
Only a review action can promote a record to instruction-grade.
`tests/test_mcp_surface_authority.py` covers these rules.

One of those review actions, `update_signal` with `review_action`, is itself an MCP tool. imi
has no way to tell who is calling, so a review over MCP is only as trustworthy as the set of
people and agents who can reach the endpoint. On the remote tier, that set is everyone on
your private network.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `421 Invalid Host header` | the `Host` the client sends isn't allowlisted | add it to `MCP_ALLOWED_HOSTS` (include `:port` if the URL has one) or set `MCP_PUBLIC_URL`; restart |
| `403 Invalid Origin header` | a browser-style client sent an `Origin` header | browser clients aren't supported; use a native MCP client |
| `406 Not Acceptable` on `/api/mcp/http` | the client didn't send `Accept: application/json, text/event-stream` | use an MCP client, or add the header to curl |
| `404` on `/api/mcp/http` | imi is older than ADR-008 | upgrade, or use `/api/mcp/sse` |
| Connection refused from another machine | the port is bound to loopback | step 1 of the remote tier |
