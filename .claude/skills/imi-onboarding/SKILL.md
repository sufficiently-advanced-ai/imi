---
name: imi-onboarding
description: >-
  Install, configure, and verify an imi instance end to end — Docker setup, .env, domain
  selection, first ingest, MCP connection. Use when the user wants to set up imi, says
  "get this running", reports a fresh-install failure, or wants an existing instance
  health-checked.
---

# imi Onboarding

Drive the setup in `docs/getting-started/onboarding.md`, verifying every step with commands
rather than assuming success. Full config reference: `docs/getting-started/configuration.md`.

## Procedure

1. **Preflight.** `docker --version` (24+), `docker compose version` (v2). Check ports —
   `for p in 8080 7474 7687; do lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 && echo "$p busy" || echo "$p free"; done`
   (`lsof` is portable across macOS/Linux; `ss` is Linux-only) — all must be free. Confirm the
   user has an Anthropic API key; never echo it back or commit it.

2. **Configure.** `cp .env.example .env`; set `ANTHROPIC_API_KEY` and `NEO4J_PASSWORD`.
   Ask which shipped domain fits (consulting_firm / b2b_saas / agency / solo_consulting /
   member_network / personal_crm) and set `ACTIVE_DOMAIN` **explicitly** — unset falls back
   to the first file alphabetically (`agency`), which surprises people. If none fit, hand off
   to the `domain-config-advisor` skill.

3. **Build & start.** `docker compose up -d --build`. First build takes 5–12 minutes — do not
   diagnose "failures" before it finishes. Then poll:
   ```bash
   docker compose ps                                    # both -> healthy (~1–2 min post-build)
   curl -fsS http://localhost:8080/health && echo OK
   curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/mcp/sse   # 200
   ```

4. **Smoke ingest.** POST a small doc to `/api/ingest` with a `source_id` like
   `onboarding-smoke-1`; poll `/api/ingest/{job_id}/status` to `completed`; show the user the
   delta report (`/api/ingest/{job_id}/delta`) and where to look in the UI (`/explorer`). This proves
   the API key, pipeline, graph, and signal store in one shot.

5. **Connect MCP** if the user works with MCP clients: `cp .mcp.json.example .mcp.json`.
   Warn: no auth on the MCP endpoint in community mode — keep ports loopback-bound (the
   shipped compose file already does this).

6. **Wire real inputs** per the user's stack: recorder→Zapier→`/api/ingest/zapier`;
   git corpus via `GIT_REPO_URL` + webhook; Grain via `python -m app.connectors`.

7. **Report.** Summarize: what's running, chosen domain, verified checks, and the next-step
   pointers (domain tuning → `docs/customization/domain-schemas.md`; governance/review flow →
   `docs/architecture/signals-and-governance.md`).

## Failure triage

| Symptom | Fix |
|---|---|
| neo4j container unhealthy | `NEO4J_PASSWORD` mismatch with an existing volume → `docker compose down -v` (destroys data — confirm with user) or restore the original password |
| App boot crash naming a config field | malformed domain YAML — fail-fast by design; fix the named field |
| Health OK but ingest fails at CLASSIFY | invalid `ANTHROPIC_API_KEY` — `docker compose logs app \| grep -i anthropic` |
| Semantic search empty | `curl -X POST localhost:8080/api/admin/backfill-memory-index` |
| Port already bound | change `PORT` in `.env` or free the port; recheck all three |
