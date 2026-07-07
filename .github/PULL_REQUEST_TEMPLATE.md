## What & why

<!-- One or two sentences: what changes, and what problem it solves. -->

## How it was verified

<!-- Check what applies and paste the relevant one-line results. -->

- [ ] `docker exec imi-dev pytest tests/ -q` (or CI) — backend tests
- [ ] `cd imi-frontend && npx jest` — frontend tests
- [ ] `docker exec imi-dev ruff check app/` — lint
- [ ] `scripts/check_evals.sh` — **required** if `app/prompts/*.xml` or
      `app/services/entity_resolver.py` changed
- [ ] Real ingest exercised (`POST /api/ingest` → `/delta`) — for pipeline/schema changes

## Notes for reviewers

<!-- Anything non-obvious: invariants touched (see CLAUDE.md), migration notes, follow-ups. -->
