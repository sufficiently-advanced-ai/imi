# Git corpus: backing the knowledge base with a GitHub repo

> **Audience:** operators connecting a git remote to an instance ·
> **Source of truth:** `app/git_ops.py` (clone/push/pull), `app/config.py` (settings),
> `app/services/orchestrators/webhook_orchestrator.py` (inbound webhook) ·
> **See also:** [Configuration](configuration.md) for the full settings tables

Everything imi knows lives as markdown+JSON files in `./repo` — files are the source of
truth, and the Neo4j graph is rebuilt from them at boot. By default that directory is a
**local-only** git repo: fine for trying things out, but the only copy of your knowledge base
is on one disk. Setting `GIT_REPO_URL` upgrades it to **remote mode**: every write imi makes
is committed and pushed to your GitHub repo, giving you offsite backup, history, and a
human-editable corpus for free.

## The two modes

| | Local-only (default) | Remote (`GIT_REPO_URL` set) |
|---|---|---|
| `./repo` initialized by | `git init`, contents **preserved** across restarts | fresh `git clone` — **local contents wiped at every startup** |
| imi's writes | committed locally, never pushed | committed and pushed to `origin <GIT_BRANCH>` |
| Backup story | you back up `./repo` yourself | every change is on GitHub moments after it happens |
| Humans editing the KB | edit files in `./repo` directly | edit on GitHub / any clone; webhook re-ingests (optional, below) |

> **The one rule to internalize: in remote mode, the remote is authoritative.**
> `initialize()` deletes `./repo` and re-clones on every app start
> (`app/git_ops.py:608-666`, called from `app/main.py:297`) — and again on every webhook
> delivery. Anything that exists only in the local directory is gone. If you have been
> running local-only and want to switch, **push your existing corpus first** (migration
> steps below).

## Step 1 — Create the repo

Create a GitHub repo for the knowledge base (github.com → New repository, or
`gh repo create your-org/your-kb --private`).

- **Private**, unless you genuinely want your extracted meetings and decisions public.
- **It must have at least one commit on your corpus branch.** The clone runs with
  `--branch <GIT_BRANCH>` (`app/git_ops.py:662`), which fails against an empty repo — tick
  *"Add a README"* when creating it, or push any initial commit to `main`.
- One repo per instance. Two instances pushing to the same branch works (rejected pushes
  recover with pull-rebase-retry, never force — `app/git_ops.py:483-533`), but you rarely
  want two knowledge bases interleaved.

## Step 2 — Create the token

imi needs to clone and push, so the token needs read **and write** on the repo's contents:

- **Fine-grained PAT** (github.com → Settings → Developer settings → Fine-grained tokens):
  scope it to the one KB repo, permission **Contents: Read and write**. Preferred — least
  privilege.
- **Classic PAT**: the `repo` scope. Works, but grants access to every repo the account can
  see.

How it's used: the token is injected into the clone/push URL as
`https://<token>@github.com/...` (`app/git_ops.py:636-641`). Two consequences:

- Only `https://github.com/...` URLs get automatic auth. For another host (GitLab, Gitea, a
  private server), embed credentials in `GIT_REPO_URL` yourself
  (`https://user:token@host/org/repo.git`) — or use a public/unauthenticated remote.
- Set it as `GITHUB_ACCESS_TOKEN` in `.env` — that's the name the shipped compose files
  forward. The code canonically reads `GITHUB_TOKEN`; the alias is bridged at startup and
  `GITHUB_TOKEN` wins if both are set (`app/config.py:125-136`).

## Step 3 — Configure and restart

**If you have an existing local corpus, migrate it first** — this is the step that protects
you from the wipe-and-reclone:

```bash
cd repo
git remote add origin https://github.com/your-org/your-kb.git
git push -u origin main        # your GIT_BRANCH; authenticate as needed
cd ..
```

Then in `.env`:

```bash
GIT_REPO_URL=https://github.com/your-org/your-kb.git
GITHUB_ACCESS_TOKEN=github_pat_...
GIT_BRANCH=main                          # default
GIT_USER_NAME="imi Bot"                  # commit identity — defaults shown
GIT_USER_EMAIL=imi-bot@example.com       # (app/git_ops.py:681-696)
```

Restart: `docker compose up -d --force-recreate app`.

**Verify:**

```bash
docker compose logs app | grep -i '"operation": "initialize"'   # action: "clone", status: "completed"
```

Then ingest something (any document via `POST /api/ingest`) and confirm a commit from your
`GIT_USER_NAME` appears on GitHub within a minute. Automated commits carry the
`BOT_COMMIT_PREFIX` (default `[bot]`) in the message.

## Optional — Step 4: webhook for inbound edits

Pushing is one-way: imi → GitHub. If humans (or other tooling) also edit the KB repo
directly and you want imi to re-ingest those changes, add a webhook. Skip this if imi is the
only writer.

**imi-side.** The handler needs a token and the repo name. The token side is already covered:
the handler reads the canonical `GITHUB_TOKEN`, which the alias bridge fills from the
`GITHUB_ACCESS_TOKEN` you set in Step 2 (`app/config.py:125-136`). What stock
`docker-compose.yml` does *not* forward is `REPO_NAME` (`owner/repo`, used by the GitHub API
client — see `docker-compose.yml:55-59` for what is forwarded). Add it via a local
`docker-compose.override.yml` — auto-merged and already gitignored for exactly this purpose
(`.gitignore:129-130`):

```yaml
services:
  app:
    environment:
      - REPO_NAME=${REPO_NAME:-}
```

Then set `REPO_NAME=your-org/your-kb` in `.env` and recreate the container.

**GitHub-side.** Repo → Settings → Webhooks → Add webhook:

- Payload URL: `https://<your-host>/api/webhook/github`
- Content type: `application/json`
- Events: *Just the push event* — anything else is rejected with a 400
  (`app/github_client.py:258-270`)

**What a delivery does** (`webhook_orchestrator.py:169-224`): re-initializes and pulls the
repo, diffs the pushed commit range, filters to `.md`/`.markdown` files, and re-ingests those
in the background. Commits from `*[bot]` users or messages starting with
`BOT_COMMIT_PREFIX` are skipped (`webhook_orchestrator.py:235-281`) — that's the loop
protection that stops imi re-ingesting its own pushes; don't strip the prefix from automated
commits.

> **Security reality check:** the community webhook path validates the event type but does
> **not** verify GitHub's HMAC signature — `WEBHOOK_SECRET` is accepted in config and unused
> here, as is the `allowed_branches` setting. Anyone who can reach the endpoint can trigger
> a pull-and-ingest. Keep the instance loopback/VPN-only (the shipped compose binds
> `127.0.0.1`), or front it with a proxy that enforces auth before exposing the webhook. If
> you can't do either, don't add the webhook — poll with a cron that calls
> `POST /api/repository/reinitialize` instead.

## Operational notes

- **Backups become trivial** — the corpus is on GitHub, and the graph is rebuilt from it at
  boot (`NEO4J_REBUILD_ON_STARTUP=true`, the default). What's *not* in the corpus: the
  SQLite app DB and vector index under `./data` (re-derivable; re-run
  `POST /api/admin/backfill-memory-index` after a restore) and Neo4j's own volume
  (re-derivable).
- **Changing `GIT_REPO_URL` later** repoints the instance wholesale at the next restart —
  same wipe-and-reclone rules. `POST /api/repository/reinitialize` does it without a
  restart.
- **Conflict behavior**: pulls auto-stash local changes (`app/git_ops.py:256-320`); rejected
  pushes recover with pull-rebase-retry and never force-push, so concurrent writers can't
  destroy each other's work.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Boot fails, logs show clone error | bad token, wrong URL, or the branch doesn't exist — remember an empty repo has no `main` to `--branch` onto |
| Clone works but pushes fail with 403 | token lacks **write** (fine-grained: Contents read-only; or SSO not authorized for an org repo) |
| Webhook returns 500 `GITHUB_TOKEN not configured` | no token reached the app — set `GITHUB_ACCESS_TOKEN` in `.env` (Step 2) and recreate the container |
| Webhook returns 400 | non-push event — set the webhook to push events only |
| Webhook delivers but nothing re-ingests | commit was skipped as a bot commit, or touched no `.md`/`.markdown` files — check `docker compose logs app` for `skipping_bot_commit` |
| Local corpus vanished after enabling remote mode | the wipe-and-reclone above — restore from any backup/clone of the old `./repo` and push it to the remote |
