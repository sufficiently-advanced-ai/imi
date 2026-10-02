# Use-case packs

> **Audience:** anyone setting imi up for a specific kind of work, and anyone writing a pack ·
> **Decision record:** [ADR-005](../docs/adr/ADR-005-use-case-packs.md)

A use-case pack is a directory of configuration — no code — that sets imi up for one kind of
work: the domain schema, the lanes, how content arrives, the skills that turn the graph into
work, and a small synthetic corpus with questions it must answer. Every deployment makes the
same five decisions (**Install → Ontology → Lanes → Inbound → Use**); a pack writes down one
operator's answers so the next person starts from a working setup instead of tribal
knowledge.

## Packs

| Pack | For | Status |
|---|---|---|
| [`freelance-implementation`](freelance-implementation/) | a solo consultant doing technical implementations on SOW-bound engagements | stable |
| `climate-advisory` | a climate advisory firm tracking how positions shift across hundreds of sources | **planned** — blocked on [ADR-006](../docs/adr/ADR-006-library-primary-deployments.md) (library-primary lane policy) |

## Using a pack

Ask Claude Code, in an imi checkout:

> Set up imi with the freelance-implementation pack.

The [`imi-onboarding`](../.claude/skills/imi-onboarding/SKILL.md) skill installs imi, copies
the pack's `domain.yaml` and `lanes.yaml` into `config/` with a version stamp, sets
`ACTIVE_DOMAIN` and restarts, walks you through the inbound recipes, installs the skills as a
plugin, ingests the sample corpus and runs the proof. Or follow the pack's README by hand.

Every pack works on a stock community install with every optional feature off. Optional
infrastructure — a decision-model endpoint for lane and entity judgment, the Remote MCP tier,
authentication in front of imi — is named in each guide where it applies, with what changes
without it. Getting it is your call.

## Layout

```
use-cases/<id>/
  pack.yaml          # manifest (below)
  README.md          # the guide: ## 1. Install, ## 2. Ontology, ## 3. Lanes, ## 4. Inbound, ## 5. Use
  domain.yaml        # copied to config/domains/<domain id>.yaml (or the manifest names a shipped domain)
  lanes.yaml         # copied to config/lanes.yaml
  inbound/*.yaml     # one recipe per input (ADR-007 intake contract)
  skills/<name>/     # pack-specific skills, SKILL.md each — only when genuinely use-case specific
  sample/            # synthetic corpus + manifest.yaml — never real client data
  proof.yaml         # questions the sample must answer, with expected facts
```

Skills shared by every pack — `brief`, `constitution-review`, `what-changed`, `memory-wrap` —
live once in [`skills/core/`](../skills/core/). A pack adds skills only for work that is
specific to it.

## Pack manifest

`pack.yaml`:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | kebab-case; equals the directory name |
| `name` | yes | human name of the use case |
| `version` | yes | semver; stamped into copied files, bump on any change to `domain.yaml` / `lanes.yaml` |
| `status` | yes | `draft` or `stable` |
| `description`, `audience` | `audience` yes | who this is for, in a sentence or two |
| `domain` | yes | `{file: domain.yaml, id: <domain id>}` or `{shipped: <config/domains stem>}` |
| `lanes` | no | path to `lanes.yaml` |
| `inbound` | no | list of recipe files |
| `skills` | no | list of pack skill directories |
| `sample` | yes | path to `sample/manifest.yaml` |
| `proof` | yes | path to `proof.yaml` |
| `requires` | no | stock features only: `rest_ingest`, `mcp_local` |
| `optional` | no | `[{id, used_for, without}]` — `id` ∈ `decision_model`, `claude_connectors`, `scheduled_tasks`, `mcp_relayed`, `mcp_remote`, `git_corpus`; `without` says what changes when it is off |

### `domain.yaml`

A full domain schema, validated against the authoritative model
`app/model_schemas/domain_config.py` (see [`config/domains/DOMAIN_SCHEMA.md`](../config/domains/DOMAIN_SCHEMA.md)).
Its `id` must equal `domain.id` in the manifest, because it is copied to
`config/domains/<id>.yaml` and selected with `ACTIVE_DOMAIN=<id>`. Prefer deriving from a
shipped domain; never invent entity types the schema cannot hold.

### `lanes.yaml`

Same format as [`config/lanes.yaml.example`](../config/lanes.yaml.example). Known top-level
keys: `owner`, `sources`, `drop_senders`, plus `mcp_trusted_sources` (ADR-007) and
`library` / `recall` (ADR-006). Lane values: `record`, `library`, `per_item`.

### Inbound recipes

One YAML file per input — a Claude task that reads a connector on a schedule and writes to
imi over MCP:

```yaml
id: gmail
connector: gmail                  # gmail | gdrive | gcal | slack | web | rss | manual
title: Client email from Gmail
needs: [claude_connectors, scheduled_tasks]
schedule: "0 7-19/2 * * 1-5"      # cron, the operator's local time
tiers: [local, relayed]           # ADR-008; the Remote tier is linked, never required
window: "newer_than:2d"           # overlaps prior runs on purpose
query: ...
writes:
  - tool: capture_thought         # or add_call_transcript
    source: gmail                 # MUST equal connector
    source_id: "gmail:{message_id}"   # MUST be <connector>:<native id>
    event_time:
      field: source_date          # start_time for add_call_transcript
      from: "the Date header — never the time this task ran"
prompt: |
  The full task prompt, pasted into the scheduler.
```

The contract (ADR-007 §4): `source` is the connector; `source_id` is
`<connector>:<native id>` (message id; file id + revision; channel + ts); event time comes
from the content — sent date, publication date, meeting start — never fetch time (ADR-004);
windows may overlap, because idempotency makes re-runs free. The prompt must state all of it,
since the agent running it sees only the prompt and the tool schemas.

### `sample/` and `proof.yaml`

`sample/manifest.yaml` lists each document with the `POST /api/ingest` fields: `file`,
`title`, `source` (a REST `ContentSource`: `email`, `document`, `local_recording`, …),
`source_id` (`sample:<pack id>:<doc>`), `timestamp` (event time, timezone-aware, and its date
must appear in the document) and `participants` for transcripts. Every person and
organization is fictional; use `.example` domains.

`proof.yaml`:

```yaml
min_pass_rate: 0.75              # share of questions that must pass
questions:
  - id: sow-fixed-fee
    ask: "What is the total fixed fee in the statement of work?"
    facts:
      - any: ["48,000", "48000", "48k"]   # one fact; any alternative matches
    min_facts: 1                          # default: all facts
```

Facts match case- and punctuation-insensitively on whole words. Thresholds, not exact
strings: a proof ingest makes many non-deterministic model calls.

## Tooling

| Script | Does | Needs |
|---|---|---|
| `scripts/validate_packs.py [pack]` | validates manifests, domain against the Pydantic model, lanes keys, skill frontmatter and tool names, inbound contract, sample and proof | pydantic, pyyaml |
| `scripts/install_pack.py status\|diff\|install <pack>` | copy-and-stamp `domain.yaml` / `lanes.yaml` into `config/`; detects untouched vs edited copies | pyyaml |
| `scripts/build_plugin.py --pack <pack>` | builds the Claude Code plugin (core + pack skills) into `build/plugins/` | — |
| `scripts/check_pack_proof.py <pack> --url ...` | ingests `sample/` idempotently and scores `proof.yaml` against a running instance | httpx, pyyaml, mcp |

CI ([`.github/workflows/pack-proof.yml`](../.github/workflows/pack-proof.yml)) validates on
every change to packs or skills and nightly; it runs each pack's proof against a fresh stack
when an `ANTHROPIC_API_KEY` secret is configured. It is not a required check.

## Writing a pack

1. Copy `freelance-implementation/` and rename it; set `id`, `name`, `version: 0.1.0`.
2. Start the domain from the closest shipped schema (the `domain-config-advisor` skill
   helps). Keep it to 3–6 entity types.
3. Write `lanes.yaml` for the sources this use case actually has.
4. One inbound recipe per input the use case relies on.
5. A synthetic sample of 6–10 documents that exercises the domain, and 5–8 proof questions
   only that sample can answer.
6. The README, in the five sections, written as "how one operator does it" — name optional
   infrastructure where it applies and what changes without it.
7. `python scripts/validate_packs.py <id>`, then the proof against a disposable instance.

If a use case cannot be expressed as a pack, that is a platform gap — it gets an ADR, not
Python in the pack.
