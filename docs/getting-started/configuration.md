# Configuration Reference

> **Audience:** operators and agents configuring an instance ·
> **Source of truth:** `app/config.py` (class `Settings`), `.env.example`,
> `config/*.example.*` ·
> **See also:** [Domain Schemas](../customization/domain-schemas.md) for the domain YAML itself

## The four config systems (they do not merge)

imi has four independent configuration surfaces. Knowing which one owns a knob saves a lot of
searching:

| System | File(s) | Owns | Loaded |
|---|---|---|---|
| **App settings** | env vars / `.env` / `config/app_config.json` | credentials, backends, models, auth, telemetry | at startup, `app/config.py` |
| **Domain schema** | `config/domains/<id>.yaml`, selected by `ACTIVE_DOMAIN` | entity types, relationships, extraction steering, UI labels | at import — **restart to switch** |
| **Inference routing** | `config/inference.yaml` (optional) | which LLM endpoint serves which operation | at startup; absent = all-Anthropic |
| **Workflows** | `config/workflows/<id>.yaml` (optional) | per-meeting-type processors + agent config | on demand; absent = built-in default |

### App settings precedence

From `settings_customise_sources` (`app/config.py:11-20`):

```text
init kwargs  >  OS environment  >  config/app_config.json  >  .env file  >  defaults
```

Notes:
- `app_config.json` is read from the **container path** `/app/config/app_config.json` only,
  and only feeds Claude + GitHub keys (nested `{claude:{...}, github:{...}}` is flattened).
  Example: `config/app_config.example.json`. Most deployments just use `.env`.
- `ENV_FILE=.env.test` switches which dotenv file is read (used by tests).
- Legacy aliases are bridged: `CLAUDE_MODEL→CLAUDE_SONNET_MODEL`,
  `CLAUDE_DEFAULT_MODEL→CLAUDE_HAIKU_MODEL`, `GITHUB_ACCESS_TOKEN→GITHUB_TOKEN`.

## Settings table

Operator-facing settings (full set: `app/config.py:77-269`). **Bold** = required.

### Core

| Setting | Default | Effect |
|---|---|---|
| **`ANTHROPIC_API_KEY`** | — | Claude auth; nothing extracts without it |
| **`NEO4J_PASSWORD`** | `dev-password-2024` | must match the Neo4j container's auth |
| `NEO4J_URI` / `NEO4J_USERNAME` | `bolt://neo4j:7687` / `neo4j` | compose defaults are correct |
| `ACTIVE_DOMAIN` | falls back to first YAML alphabetically | selects `config/domains/<id>.yaml`; **set it explicitly** |
| `AUTH_MODE` | `none` | `none` (open, demo user) or `demo` (cookie login). Hosted SSO layers on this seam |
| `PORT` | `8080` | host port for the app container |

### Models & inference

| Setting | Default | Effect |
|---|---|---|
| `CLAUDE_SONNET_MODEL` | `claude-sonnet-4-5-20250929` | full-power tier: chat, synthesis |
| `CLAUDE_HAIKU_MODEL` | `claude-haiku-4-5-20251001` | lightweight tier: all extraction ops |
| `CLAUDE_AGENT_MODEL` | haiku default | Claude Agent SDK / chat agent |
| `ANTHROPIC_BASE_URL` | — | point at a gateway/proxy |

Per-operation routing (send `signal_extraction` to a local vLLM, keep chat on Anthropic, …)
lives in `config/inference.yaml` — copy `config/inference.yaml.example`. Resolution order:
`operations[op]` > `aliases[model]` > `default` > implicit Anthropic
(`app/services/inference/registry.py:130`). Two fail-closed rules: non-Anthropic endpoints
reject tool-using calls, and a malformed file or dangling endpoint reference raises at
startup.

### Storage & memory

| Setting | Default | Effect |
|---|---|---|
| `VECTOR_BACKEND` | `sqlite` | `sqlite` (persistent sidecar `vectors.db`) · `pgvector` (needs `DATABASE_URL`) · `faiss` (legacy, in-memory, avoid). After switching: `POST /api/admin/backfill-memory-index` |
| `DATABASE_PATH` | `/app/data/imi.db` | SQLite app DB |
| `DATABASE_URL` | — | SQLAlchemy URL, required for pgvector |
| `NEO4J_REBUILD_ON_STARTUP` | `true` | wipe + rebuild the graph from the file corpus at boot |

### Git corpus & webhooks

Setup walkthrough — repo creation, token scopes, webhook wiring, migration warnings:
[Git corpus](git-corpus.md).

| Setting | Default | Effect |
|---|---|---|
| `GIT_REPO_URL` / `GIT_BRANCH` | — / `main` | external git repo as the knowledge corpus; omit for local-only. **Remote is authoritative: when set, `./repo` is wiped and re-cloned at every startup** (`app/git_ops.py:608`) |
| `GITHUB_ACCESS_TOKEN` (alias of `GITHUB_TOKEN`; canonical wins) | — | corpus clone/push auth; auto-injected only into `https://github.com/...` URLs (`app/git_ops.py:636`) |
| `GIT_USER_NAME` / `GIT_USER_EMAIL` | `imi Bot` / `imi-bot@example.com` | commit identity on corpus writes |
| `REPO_NAME` | — | `owner/repo` for the webhook path's GitHub API client; **not forwarded by stock compose** — add via an override file |
| `WEBHOOK_SECRET` | — | reserved — accepted in config but **not verified** by the community `/api/webhook/github` path; don't expose the endpoint publicly |
| `allowed_branches` | `["main"]` | defined but currently unused |
| `BOT_COMMIT_PREFIX` | `[bot]` | prefix on automated commits; the webhook skips commits carrying it (loop protection) |

### Observability & misc

| Setting | Default | Effect |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector (4318; gRPC 4317 unsupported) |
| `OTEL_ENABLED` | `true` | master telemetry switch |
| `CLIENT_NAME` | `unknown` | label on LLM/document/entity metrics |
| `MCP_ALLOWED_HOSTS` | — | extra Host-header allowlist entries for the MCP SSE endpoint |
| `DEMO_MODE` | `false` | demo-data routes |
| `ENCRYPTION_KEY` | — | AES key for sensitive data |

### Frontend (`imi-frontend`, Next.js)

| Var | Default | Effect |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `/api` | backend base URL (only change when splitting front/back) |
| `NEXT_PUBLIC_AUTH_MODE` | `none` | **must mirror** backend `AUTH_MODE` |
| `NEXT_PUBLIC_BASE_PATH` | `''` | sub-path mounting behind a proxy |

All domain-specific UI labels and navigation come from the backend at runtime
(`GET /api/domain/config`, the schema's `ui:` block) — no frontend rebuild per domain.

## Domain selection semantics (worth being precise about)

- The active domain loads **once, at process import**
  (`app/core/domain_config/active_domain.py`). A malformed schema fails startup immediately —
  that's a feature.
- If `ACTIVE_DOMAIN` is unset, the loader falls back to the **first YAML alphabetically** in
  `config/domains/` — which is `agency.yaml`, probably not what you want. Set it explicitly.
- Switching domains is **env change + restart**. The `POST /api/domain/switch` endpoint and
  `DomainConfigService.set_active_domain()` are intentional no-ops in this edition.
- One process = one domain = one tenant. Multi-tenancy is a code seam
  (`app/core/tenancy/`), not a config option.

## Workflows config (optional)

`config/workflows/<workflow_id>.yaml` customizes processing per meeting type: which signal
processors run (`decision_detector`, `action_item_detector`, `key_point_extractor` by
default), per-processor `confidence_threshold` and `system_prompt_path`, and an `agent:`
block (model, prompt, skills). Missing file = built-in default, not an error. Models:
`app/models/workflow.py`; loader: `app/services/workflow_loader.py`.
