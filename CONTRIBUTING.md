# Contributing to imi

Issues and PRs are welcome. This page covers the mechanics; for orientation in the codebase,
start with the [system overview](docs/architecture/overview.md) and the
[customization map](docs/customization/README.md) — most changes turn out to be config or
prompt edits, not code.

## Dev environment

```bash
./dev-hot.sh
```

This starts a hot-reload environment: edits to `app/` (FastAPI backend) and `imi-frontend/`
(Next.js) reload live, with a dev Neo4j alongside. Docker is the only host dependency.

## Tests and checks

```bash
docker exec imi-dev pytest tests/ -q      # backend tests (use the dev env's Neo4j)
docker exec imi-dev ruff check app/       # lint
cd imi-frontend && npx jest               # frontend tests
scripts/smoke_test.sh --quick             # import sweep — no API key needed
scripts/smoke_test.sh                     # full end-to-end (build, boot, real ingest)
```

**If you change extraction behavior** — anything in `app/prompts/*.xml` or
`app/services/entity_resolver.py` — run the extraction-quality evals:

```bash
scripts/check_evals.sh
```

For pipeline or schema changes, a real ingest beats unit tests: `POST /api/ingest` a
representative document, then check `/api/ingest/{job_id}/delta` and `/api/signals/feed`.

## Ground rules

- **Don't weaken the authority gate.** `provenance_status`, `review_status`, and
  `can_use_as_*` must never be accepted from client input — see
  [ADR-002](docs/adr/ADR-002-evidence-instruction-authority-gate.md).
- **Files are the source of truth.** Graph writes go through the write-through layer; never
  write Neo4j directly and skip the file corpus.
- **Domain schemas are the type system.** Entity types come from `config/domains/*.yaml` —
  don't hardcode them.
- Prefer editing prompts (`app/prompts/*.xml`) over adding extraction code.
- When a doc and the code disagree, the code wins — and a PR fixing the doc is welcome.

The full working agreement — invariants, known traps, conventions — is in
[`CLAUDE.md`](CLAUDE.md); it's written for coding agents but applies equally to humans.

## Commit style

Conventional-commit-ish: `feat(memory): ...`, `fix(judge): ...`, `docs: ...`.

## Design decisions

Significant design decisions are recorded in [`docs/adr/`](docs/adr/) (template included).
If your change reverses or bends one, say so in the PR and propose an ADR update.
