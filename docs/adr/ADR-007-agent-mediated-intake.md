# ADR-007: Agent-Mediated Intake — Channels, Not Lanes

## Status

Proposed (2026-10-01). Tightens ADR-003 §1–2 for content arriving through MCP.

## Context

The fastest-growing intake path is not a webhook. It is Claude, with the user's connectors
(Gmail, Drive, Calendar, Slack), reading a source on a schedule and writing to imi through
`capture_thought` or `add_call_transcript`. For most use cases (ADR-005) this replaces Zapier.

Two properties of the current MCP surface make that path unsafe for ADR-003:

1. **`capture_thought.source` defaults to `"manual"`**, and `manual` is a record-default source
   (`lane_admission.SOURCE_DEFAULTS`). Record-default sources skip the decision model and are
   never dropped. An agent that forwards a newsletter from Gmail without setting `source` files
   it as a first-person record with full extraction — reproducing the pollution ADR-003
   measured.
2. **`source` is free text chosen by the client, and it deterministically picks the lane** for
   any configured source. ADR-003 §1 says the lane is never client-settable; in effect it is,
   one level removed.

`add_call_transcript.source` is an enum of recorders, all record — reasonable for transcripts,
but it gives an agent ingesting a calendar-attached doc nowhere honest to say where it came
from.

There is also no convention for idempotency or event time on this path. ADR-004 requires
`occurred_at` from the content, and invariant 3 requires stable `source_id`s; agents currently
improvise both.

## Decision

### 1. Agents declare a channel; the server decides the lane

MCP intake tools take `source` as the **connector the content came from** — `gmail`, `gdrive`,
`gcal`, `slack`, `web`, `rss`, or `manual` — and the server stamps `channel: mcp` on every
record it admits through MCP.

New `SOURCE_DEFAULTS` entries: `gmail` and `gdrive` → `per_item`; `gcal` → `record` (`slack`
is already `record`). `gmail` joins the existing `mail` / `email` keys rather than replacing
them, so existing corpora and Zapier intake keep their source names. As today, operators
override in `config/lanes.yaml`.

`channel` is stamped by the transport — the MCP handler sets it — never read from arguments
or a request body. In particular it is not the existing `actor` field: MCP already passes
`actor="mcp"` (`chat_tools.capture_thought`), but REST captures take `actor` from the client
(`routes/captures.py`), so `actor` cannot carry trust.

### 2. Record-default sources are not trusted on the MCP channel by default

On `channel: mcp`, a record-default source is admitted as `per_item` (judged by the decision
model) unless it appears in `lanes.yaml`:

```yaml
mcp_trusted_sources: [manual]   # e.g. a single-user instance where "remember this" is always first-party
```

A first-party note still lands in `record` — the model files it as memory at
`RECORD_MIN_PROBABILITY`. What changes is that an agent can no longer *assert* record.

### 3. The default `source` for `capture_thought` becomes absent, not `manual`

An omitted source is treated as unknown → `per_item`. Agents that know the channel say so;
agents that don't get judged. The `"manual"` default currently lives in three places, and all
three change together: the tool schema (`mcp_tool_definitions.py`, `capture_thought.source`),
the MCP handler (`mcp_server.py`, `args.get("source", "manual")`) and the delegate signature
(`chat_tools.capture_thought`).

### 4. Intake contract for connector recipes

Every inbound recipe in a pack (ADR-005) must specify:

| Field | Rule |
|---|---|
| `source` | the connector name (§1) |
| `source_id` | `<connector>:<native id>` — Gmail message id, Drive file id + revision, Slack channel + ts |
| `source_date` / `start_time` | taken from the content (sent date, publication date, meeting start) — never fetch time (ADR-004) |
| dedup scope | the recipe's query window may overlap prior runs; idempotency makes re-runs free |

The same rules go in the MCP tool descriptions for `capture_thought` and
`add_call_transcript`, so an agent without a recipe still sees them.

### 5. Scope: MCP only

REST intake (`/api/ingest`, `/api/captures`, the Zapier adapter) also takes `source` from the
caller. Those paths are loopback-bound by default and driven by the operator's own
automation, not by a general-purpose agent, so they are out of scope here. If a REST path is ever opened to agents, it gets a channel the same way.
### 6. The judge is optional; its absence is documented, not hidden

Lane judgment runs only when `config/inference.yaml` routes `lane_admission` to a decision
endpoint (e.g. Jev). Without one, `per_item` falls back to `record` — the pre-lanes behaviour
(`lane_admission._fallback_lane`). So §2 and §3 take effect only on instances with a judge;
without it, MCP intake behaves as it does today. That is intended: the system works without
the judge, and getting an endpoint is the reader's choice. Pack guides state the trade-off in
the Lanes step.

## Alternatives considered

- **Let agents pass `lane` explicitly.** Violates ADR-003 §1 outright.
- **Keep `manual` default, document "always set source".** Correct behaviour would depend on
  every prompt author remembering; the failure is silent.

## Consequences

- Agent intake can't contaminate the record lane by omission.
- One extra model call per MCP capture that isn't explicitly trusted — negligible next to
  extraction.
- Single-user operators who want hand captures to skip judgment add one line to `lanes.yaml`.
- Existing scheduled tasks and skills that rely on the `manual` default need a review; their
  captures will start being judged, which is the point.
