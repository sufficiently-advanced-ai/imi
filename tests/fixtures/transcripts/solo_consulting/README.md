# Solo Consulting Sample Transcripts

Sample client-call transcripts for the **solo_consulting** ("practice brain") tenant, shaped as Zapier adapter payloads (`POST /api/ingest/zapier`).

## Why these exist
The repo's default `repo/` content is aligned to the org-centric domains (accounts/projects/people) and does **not** apply to a solopreneur practice graph. These fixtures provide practice-aligned data: three fictional clients (Acme Corp, Globex, Initech), each with multiple calls carrying commitments (with due dates), decisions, and insights. Globex/Initech calls sit in mid-May 2026; **Acme Corp spans mid-April → late-May 2026** so the signal feed can surface change over time.

## Acme temporal arc (mid-April → late-May)
Acme is the hero client. Its calls deliberately encode *comparable* statements across time so the existing tooling (`compare_statements`, `detect_weak_signals`, `build_timeline`) surfaces evolution, not just isolated facts:

| Change pattern | How it shows up across Acme calls |
|---|---|
| Commitment slippage | Supplier survey target slips May 18 → "early June" (04-28 → 05-25) |
| Stance shift | CFO Victor Lin moves blocker → skeptic → conditional supporter (04-16 → 05-25 → 05-27) |
| Scope creep | Scope 3 expands from "Category 1 only" to "Categories 1, 4, and 6" (04-16 → 05-25) |
| Going quiet | Plant manager Ken Mueller attends early, then stops responding (04-28 → 05-25 → 05-27) |
| Decision reversal | Emission factors: GHG Protocol → custom floated → reverted to GHG Protocol (04-28 → 05-25 → 05-27) |
| Sentiment decline | Board-deadline pressure escalates call over call toward the June 6 board date |

Acme stakeholders are named with the full names used in `practice_kb/stakeholders/` so extracted signals link to the profiled influence-map nodes rather than bare stubs.

## Seeding a clean practice graph
1. Provision/point an instance at the practice domain: `ACTIVE_DOMAIN=solo_consulting`.
2. For a clean dev graph, set `NEO4J_REBUILD_ON_STARTUP=true` and start from an empty `repo/` — entities (clients, engagements, stakeholders) are created purely by extraction during ingestion, not pre-seeded as markdown.
3. Ingest the samples:
   ```bash
   python scripts/seed_practice_data.py --base-url http://127.0.0.1:<DEV_PORT>
   ```
4. Verify: open items should appear across all three clients (weekly sweep), and each client should resolve via `search_knowledge_graph(entity_types=["client"])`.

## Notes
- "Today" for overdue-vs-upcoming reasoning is 2026-05-28; due dates in the transcripts straddle that date intentionally.
- Provider values vary (otter/fathom/grain) to exercise the adapter's provider→source mapping.
- Content is fictional and PII-free.
