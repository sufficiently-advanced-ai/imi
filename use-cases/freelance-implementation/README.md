# imi for a freelance implementation consultant

> **Pack:** `freelance-implementation` · **Version:** see [`pack.yaml`](pack.yaml) ·
> **Works on:** a stock community install, every optional feature off

This is how one solo consultant runs imi for technical implementation work — data
migrations, integrations, small data platforms — on fixed-fee, SOW-bound engagements with a
handful of clients at a time. It is a description of a working setup, not a prescription:
take the parts that fit. Where a step uses optional infrastructure, the guide says what it
adds and what changes without it; whether to set any of it up is your call.

What you get: walk into every client call knowing what was promised, what changed since the
last one, and what is out of scope — without re-reading your inbox.

The fast path is one sentence to Claude Code in an imi checkout:

> Set up imi with the freelance-implementation pack.

That runs the `imi-onboarding` skill, which does every step below and finishes by ingesting
this pack's sample corpus and reporting each proof question as pass/fail. The rest of this
page explains what it did and why.

## 1. Install

Follow [Onboarding](../../docs/getting-started/onboarding.md) (or let the `imi-onboarding`
skill drive it): Docker, `.env` with `ANTHROPIC_API_KEY` and `NEO4J_PASSWORD`,
`docker compose up -d --build`, health checks. Nothing here is pack-specific except the
domain choice in step 2.

**MCP access.** The consumption skills and the inbound recipes talk to imi over MCP. I run
the **Local** tier (Claude Code and Claude Desktop on the same machine as imi) and the
**Relayed** tier (cloud Claude sessions reaching that local imi through the desktop app). The
tiers, what each needs and their trade-offs are in
[MCP access tiers](../../docs/mcp_access_tiers.md). The **Remote** tier (ADR-008) lets
scheduled cloud tasks run while your machine is off; it is optional and this pack never
requires it.

## 2. Ontology

The pack ships its own domain, [`domain.yaml`](domain.yaml) (`freelance_implementation`),
derived from the shipped `solo_consulting` schema:

| Type | What it holds |
|---|---|
| `client` | the organizations that hire you, and prospects you are scoping |
| `engagement` | one bounded piece of paid work — usually one SOW or proposal |
| `stakeholder` | people on the client side: sponsor, approver, technical lead, finance |
| `system` | the platforms the work touches: the legacy system you migrate off, the warehouse, the BI tool |
| `consultant` | you, and any subcontractor you bring in |

`system` is the addition that matters for implementation work: "what did we decide about the
warehouse" and "which engagements touch the order system" become graph questions. The
intelligence patterns watch for scope creep, client-side dependencies that block you,
timeline changes and proposals you owe.

Onboarding copies it to `config/domains/freelance_implementation.yaml` with a
`# pack: freelance-implementation@<version>` stamp, sets
`ACTIVE_DOMAIN=freelance_implementation`, and restarts the backend — domain switching is
restart-only. Edit the copy freely; a later onboarding run notices the edit, shows you the
diff against the new pack version and asks before replacing it
(`python scripts/install_pack.py status freelance-implementation`).

To adapt it further, the `domain-config-advisor` skill reads your own description or sample
documents and proposes changes.

## 3. Lanes

[`lanes.yaml`](lanes.yaml) decides which sources are **record** (you were party to it) and
which are **library** (third-party content you watch) — see ADR-003. Onboarding copies it to
`config/lanes.yaml` with the same stamp.

| Source | Lane | Why |
|---|---|---|
| `gcal`, `gdrive`, `document` | record | your calendar and your client folders |
| `gmail`, `email`, `mail` | per_item | client threads and vendor newsletters share an inbox |
| `web`, `rss` | library | articles and feeds you follow |

**The decision model is optional.** `per_item` means "judge each item" — but only when
`config/inference.yaml` routes lane admission to a decision-model endpoint. I run one; you
do not need to. Without it, `per_item` falls back to `record`, the pre-lanes behaviour: a
newsletter that slips past the Gmail query becomes memory rather than background. The Gmail
recipe's query is scoped to client domains for exactly this reason. The proof runs with no
decision model.

`mcp_trusted_sources: [manual, gcal]` says that on this single-user instance, notes I
capture by hand and my own calendar are first-party even when they arrive over MCP
(ADR-007). Servers without ADR-007 ignore the key.

## 4. Inbound

Content arrives three ways, in the order I would set them up:

1. **By hand** — drop a transcript or SOW into the web UI, or `POST /api/ingest`. Always
   available.
2. **Scheduled Claude tasks** reading Gmail, Calendar and Drive through Claude's connectors
   and writing to imi over MCP — the recipes below. This replaces Zapier for most of my
   intake.
3. **A recorder** (Grain, Fathom, Fireflies, …) through the shipped Zapier adapter or
   connector — see [Onboarding](../../docs/getting-started/onboarding.md).

| Recipe | Reads | Writes through | `source` / `source_id` | Event time from |
|---|---|---|---|---|
| [`gmail`](inbound/gmail.yaml) | client email, every 2 h on weekdays | `capture_thought` | `gmail` / `gmail:<message id>` | the Date header |
| [`gcal`](inbound/gcal.yaml) | meetings with external attendees, each morning | `capture_thought` | `gcal` / `gcal:<event id>:<start>` | the event start |
| [`gdrive`](inbound/gdrive.yaml) | SOWs, proposals, transcripts in client folders, each morning | `capture_thought`, `add_call_transcript` | `gdrive` / `gdrive:<file id>:<revision>` | the date the document states; the meeting start |

Every recipe follows the ADR-007 intake contract: `source` is the connector, `source_id` is
`<connector>:<native id>`, event time comes from the content (never the time the task ran),
and query windows overlap the previous run on purpose — imi deduplicates, so re-runs are
free. Each file holds the schedule and the full task prompt; paste the prompt into your
client's scheduled-task feature and fill in the `CLIENT_DOMAINS` / `CLIENT_FOLDERS` line.

**Where the task runs:**

- **Local tier** — Claude Desktop or Claude Code on the machine running imi, with the
  Google connectors enabled. Runs while that machine is awake.
- **Relayed tier** — a scheduled task in a cloud Claude session; it reaches imi through your
  desktop app, so the desktop app must be open and the machine awake. Missed runs are caught
  up by the overlapping window on the next run.
- **Remote tier** — scheduled cloud tasks that run with your machine off. Needs ADR-008's
  Remote tier; see [MCP access tiers](../../docs/mcp_access_tiers.md). Optional.

Without connectors or scheduled tasks, run a recipe's prompt by hand when you want imi
caught up, or ingest by hand.

## 5. Use

Install the skills as one plugin, generated from this repo:

```bash
python scripts/build_plugin.py --pack freelance-implementation
claude plugin marketplace add ./build/plugins/imi-marketplace
claude plugin install imi@imi-local
```

| Skill | Ask Claude | What it does |
|---|---|---|
| `imi:brief` | "Brief me before my 2pm with Brightwater." | open items, current decisions, who is in the room, unresolved threads |
| `imi:what-changed` | "What changed on the migration since last Friday?" | dated changes, replaced decisions, material recorded late |
| `imi:constitution-review` | "What's in force on the Brightwater engagement?" | standing rules, scope boundaries, commitments — confirmed vs unreviewed |
| `imi:memory-wrap` | "Wrap up — anything worth saving?" | proposes decisions/facts/lessons, saves the approved ones as evidence pending review |
| `imi:scope-check` (this pack) | "Priya wants returns in the dashboard — is that in scope?" | checks the SOW and later decisions; drafts a change request if needed |

My week, roughly: a brief before every client call; `scope-check` the moment a client asks
for something new; `what-changed` on Monday mornings across all engagements;
`constitution-review` before a renewal or when a new subcontractor joins; `memory-wrap` at
the end of any session where something was decided.

**Try it on the sample.** [`sample/`](sample/) is a fictional practice — an order-data
migration for "Brightwater Outfitters" and a discovery call with "Kestrel Dental Group",
eight documents. [`proof.yaml`](proof.yaml) holds the questions it must answer. To run the
proof yourself against a disposable instance:

```bash
python scripts/check_pack_proof.py freelance-implementation --url http://localhost:8080
```

Everything in `sample/` is synthetic. Do not ingest it into an instance you care about
unless you are happy to delete it afterwards.
