---
name: imi-onboarding
description: >-
  Install, configure, and verify an imi instance end to end — Docker setup, .env, domain
  and lanes, inbound sources, skills, first ingest, MCP connection — optionally from a
  use-case pack ("set up imi as a freelance consultant", "install the
  freelance-implementation pack"). Use when the user wants to set up imi, says "get this
  running", names a use case or pack, reports a fresh-install failure, or wants an existing
  instance health-checked.
---

# imi Onboarding

Drive the setup in `docs/getting-started/onboarding.md`, verifying every step with commands
rather than assuming success. Full config reference: `docs/getting-started/configuration.md`.

The spine is always the same five decisions — **Install → Ontology → Lanes → Inbound → Use**
(ADR-005). A **use-case pack** (`use-cases/<id>/`) answers all five for one kind of work;
without a pack, ask the user each question yourself.

## 0. Pack or no pack

- The user named a pack id, or a use case that matches one: list `use-cases/*/pack.yaml`,
  read the matching pack's `README.md` and `pack.yaml`, and confirm the choice in one line.
  Planned packs listed in `use-cases/README.md` but without a directory are not installable
  yet — say so and continue without a pack.
- Make a small venv for the pack scripts (they need no backend dependencies):
  `python3 -m venv .venv-packs && .venv-packs/bin/pip install -q pydantic pyyaml httpx mcp`.
  Below, `python3 scripts/...` means `.venv-packs/bin/python scripts/...`.
- Validate before touching anything: `python3 scripts/validate_packs.py <pack>`. Stop on errors.
- Note the pack's `optional` features and tell the user, briefly, which ones the guide uses
  and what changes without them. Every pack works with all of them off; acquiring any is the
  user's call — do not push.

## 1. Install

1. **Preflight.** `docker --version` (24+), `docker compose version` (v2). Check ports —
   `for p in 8080 7474 7687; do lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 && echo "$p busy" || echo "$p free"; done`
   (`lsof` is portable across macOS/Linux; `ss` is Linux-only) — all must be free, unless
   this is a re-run against the user's running imi. Confirm the user has an Anthropic API
   key; never echo it back or commit it.
2. **Configure.** `cp .env.example .env` (skip if `.env` exists — re-run); set
   `ANTHROPIC_API_KEY` and `NEO4J_PASSWORD`.

## 2. Ontology (domain)

**With a pack** whose `pack.yaml` has `domain.file`:

```bash
python3 scripts/install_pack.py status <pack>      # per target: absent | current | upgradable | edited | foreign | unstamped
```

- `absent` / `upgradable` → `python3 scripts/install_pack.py install <pack> --only domain`.
  The copy lands in `config/domains/<domain id>.yaml` with a first line
  `# pack: <id>@<version> sha256=…`.
- `current` → nothing to do.
- `edited` (the user changed their copy), `foreign` (another pack's file) or `unstamped`
  (a hand-made file at that path) → run `python3 scripts/install_pack.py diff <pack> --only domain`,
  show the diff, and **ask**: keep theirs, take the pack's (`install --force --only domain`),
  or merge by hand. Never overwrite without a yes.

If `pack.yaml` names a shipped domain (`domain.shipped`) there is nothing to copy.

**Without a pack:** ask which shipped domain fits (consulting_firm / b2b_saas / agency /
solo_consulting / member_network / personal_crm). If none fit, hand off to the
`domain-config-advisor` skill.

Either way set `ACTIVE_DOMAIN=<domain id>` in `.env` **explicitly** — unset falls back to
the first file alphabetically (`agency`), which surprises people.

## 3. Lanes

Lanes decide what is **record** (we were party to it) versus **library** (third-party
content we watch) — ADR-003. Skipping this step is how newsletters end up as "decisions".

**With a pack** that has `lanes.yaml`: same flow as the domain, with `--only lanes`; the copy
lands in `config/lanes.yaml`. Same rule for `edited` / `foreign` / `unstamped`: show the
diff, ask.

**Without a pack:** `cp config/lanes.yaml.example config/lanes.yaml` and walk the user
through it: which sources they will feed (recorders, email, Drive, RSS…), and for each
whether it is record, library or `per_item`. Set `owner:` to whose knowledge base this is.

Explain the one trade-off plainly: `per_item` sources are judged by a decision model only
when `config/inference.yaml` routes `lane_admission` to one. Without it (the default),
`per_item` falls back to `record`. That is fine; it just means narrower intake queries matter
more.

## 4. Build, start, and apply config

Domain switching is **restart-only**: `ACTIVE_DOMAIN` and `config/lanes.yaml` are read once at
startup (`POST /api/domain/switch` is a no-op).

- Fresh install: `docker compose up -d --build`. First build takes 5–12 minutes — do not
  diagnose "failures" before it finishes.
- Re-run with changed domain, lanes or `.env`: `docker compose up -d` (recreates the app
  container with the new environment; `docker compose restart` would keep the old env).

Then poll:

```bash
docker compose ps                                    # both -> healthy (~1–2 min post-build)
curl -fsS http://localhost:8080/health && echo OK
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/mcp/sse   # 200
curl -s http://localhost:8080/api/domain/config | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'   # == ACTIVE_DOMAIN
```

A boot crash naming a config field means a malformed domain YAML (fail-fast by design) —
`python3 scripts/validate_packs.py` names the field too.

## 5. Connect MCP

`cp .mcp.json.example .mcp.json` for Claude Code in this checkout. For other clients and for
cloud sessions, the access tiers — **Local**, **Relayed**, **Remote** — and what each needs
are in [`docs/mcp_access_tiers.md`](../../../docs/mcp_access_tiers.md). Packs document Local
and Relayed; Remote is optional. Warn: the community edition has no MCP authentication —
keep ports loopback-bound (the shipped compose file does) unless the user deliberately runs
the Remote tier on a private network.

## 6. Inbound

**With a pack:** walk each recipe in `use-cases/<pack>/inbound/*.yaml`, one at a time:

1. Say what it reads, on what schedule, and through which tool it writes (`source`,
   `source_id` shape, where event time comes from — the ADR-007 contract).
2. Check the prerequisites in its `needs:` — the Claude connector (Gmail, Calendar, Drive)
   enabled in the user's Claude client, and a client that runs scheduled tasks. If either is
   missing, say what the recipe would add, offer to skip it, and move on.
3. Fill the placeholders in its `prompt` (`CLIENT_DOMAINS`, `CLIENT_FOLDERS`) with the user,
   then give them the finished prompt and schedule to paste into their scheduler. Do not
   invent the schedule mechanism; ask which client they use.
4. Offer to run the prompt once now, by hand, to prove the path end to end — only if the
   connector and imi MCP are both live in this session.

**Without a pack** (or for inputs the pack has no recipe for): recorder → Zapier →
`/api/ingest/zapier`; git corpus via `GIT_REPO_URL` + webhook; Grain via
`python -m app.connectors`.

## 7. Use — install the skills

The consumption skills ship as a generated plugin; the repo is the only source of truth:

```bash
python3 scripts/build_plugin.py [--pack <pack>]       # core skills + the pack's skills
claude plugin marketplace add ./build/plugins/imi-marketplace
claude plugin install imi@imi-local
```

Add `--mcp-url http://localhost:8080/api/mcp/sse` if the user wants the plugin to declare the
imi MCP server itself (skip it if `.mcp.json` / their client already has one). On a re-run,
rebuild and `claude plugin marketplace update imi-local`. The skills appear as `imi:brief`,
`imi:what-changed`, `imi:constitution-review`, `imi:memory-wrap`, plus the pack's own.

## 8. Prove it

**With a pack** — ingest its synthetic `sample/` and run its proof:

```bash
python3 scripts/check_pack_proof.py <pack> --url http://localhost:8080 --report build/pack-proof/report.json
```

Before running, tell the user the sample is fictional and **will land in their knowledge
base**; on an instance they already use, ask first. Re-runs are free: ingestion is
idempotent by `source_id` and the runner keeps a ledger. Report each proof question as
**pass/fail** with the facts it matched, and the overall result against `min_pass_rate`. A
failure is information, not a reason to edit the proof: show the answer that missed.

**Without a pack** — the smoke ingest: POST a small doc to `/api/ingest` with a `source_id`
like `onboarding-smoke-1`; poll `/api/ingest/{job_id}/status` to `completed`; show the user
the delta report (`/api/ingest/{job_id}/delta`) and where to look in the UI (`/explorer`).

## 9. Report

Summarize: what's running, the pack and version (or chosen domain), domain and lanes state
(installed / kept user edits), which inbound recipes are live vs skipped and why, skills
installed, proof result (n/m questions, pass/fail), and next-step pointers (domain tuning →
`docs/customization/domain-schemas.md`; governance/review flow →
`docs/architecture/signals-and-governance.md`; the pack's README "Use" section).

## Failure triage

| Symptom | Fix |
|---|---|
| neo4j container unhealthy | `NEO4J_PASSWORD` mismatch with an existing volume → `docker compose down -v` (destroys data — confirm with user) or restore the original password |
| App boot crash naming a config field | malformed domain YAML — fail-fast by design; fix the named field (`scripts/validate_packs.py` helps) |
| `/api/domain/config` shows the old domain | the container kept its old env — `docker compose up -d`, not `restart` |
| Health OK but ingest fails at CLASSIFY | invalid `ANTHROPIC_API_KEY` — `docker compose logs app \| grep -i anthropic` |
| Proof ingest "dropped" | lane admission dropped a sample doc — check `drop_senders` in `config/lanes.yaml` |
| Proof questions fail, ingest fine | semantic index empty after a restart → `curl -X POST localhost:8080/api/admin/backfill-memory-index`, then re-run with `--skip-ingest` |
| Semantic search empty | `curl -X POST localhost:8080/api/admin/backfill-memory-index` |
| Port already bound | change `PORT` in `.env` or free the port; recheck all three |
