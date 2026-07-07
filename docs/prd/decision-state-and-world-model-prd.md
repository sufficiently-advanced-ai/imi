# PRD: Decision State Engine + World Model Surface

*Status: Proposed. Date: 2026-05-29. Revised 2026-05-29 after a direct read of the Semantica layer.*
*North star: `docs/world-model-concept.md`.*

> **Historical PRD.** The 'code reality' snapshots below predate the
> community-edition extraction; meeting-intelligence, pre-meeting-brief, and
> calendar surfaces referenced here are hosted-edition-only and are not part
> of this repo.

> **Correction (2026-05-29).** An earlier draft anchored Phase 1 on adopting
> Graphiti + Kuzu, based on (a) the orphaned `docs/plans/graphiti-kuzu-integration-plan.md`
> and (b) a fast audit that wrongly reported `get_state_at()` as a stub. Both were
> wrong. **Semantica is already the temporal engine** — `get_state_at`,
> `get_active_relationships`, `get_provenance`, and the `TemporalQueryService`
> composites (`what_changed`, `graph_as_of`, `temporal_blast_radius`) are
> implemented and tested, backed by Neo4j validity windows and Semantica's
> `GraphBuilder(enable_temporal=True)`. Graphiti/Kuzu are **not** needed and the
> old plan is dead. Phase 1 below is rewritten accordingly; Phases 2–4 stand.

---

## 1. Why this PRD exists

IMI's pitch promises decisions as **living objects with temporal state** —
active, stale, superseded, conflicting, temporary, zombie — surfaced as a
constitution that monitors for drift. A code audit (2026-05-29) found that the
**capture and storage** half of this is real and working, but the **temporal
reasoning and decision-state** half — the actual differentiator — is largely
scaffolding:

| Capability | Pitch says | Code reality (2026-05-29) |
|------------|-----------|---------------------------|
| MCP server / graph query | Working | **Real.** 19 tools, SSE, full CRUD (`app/routes/mcp_server.py`). |
| Multi-person / cross-meeting capture | Working | **Real.** `speaker_entity_mapper.py`, `meeting_intelligence.analyze_multiple_meetings()`. |
| Git-backed markdown corpus | Working | **Real.** Entities as markdown, signals as JSON, committed to git. |
| Temporal state / "as of T" | Differentiator | **Real.** Implemented in Semantica: `get_state_at`, `get_active_relationships`, `get_provenance` (validity windows); composites in `temporal_queries.py`; tests exist. Single-axis valid-time. |
| Contradiction detection | Differentiator | **Heuristic.** Keyword/sentiment reversal only (`_detect_contradictions`, temporal_queries.py:378–422). Not semantic. |
| Decision lifecycle + supersession | Differentiator | **Missing.** Semantica `ContextGraph` records decisions + traces causality, but no lifecycle states (stale/superseded/zombie), no supersession edges, no staleness evaluation. |
| Constitution / decision-audit export | Primary demo artifact | **Missing.** `meeting_export.py` does per-meeting CSV/JSON/ICS only; no constitution or audit emission. |

This PRD closes those four gaps and reframes the product around the **world
model** surface (see concept doc). It is sequenced so each phase ships something
demonstrable and each builds on the last.

## 2. Goals / non-goals

**Goals**
- Replace the stubbed temporal layer with a real bi-temporal substrate.
- Model decisions as first-class objects with a real lifecycle and supersession lineage.
- Detect cross-person, cross-meeting contradiction and drift semantically, not by keyword.
- Emit the world model (stable constitution + current situation) as portable markdown and render it as the primary surface.
- Keep the git-backed markdown corpus authoritative; the graph stays a rebuildable cache.

**Non-goals (this PRD)**
- Predictive analytics ("this project will fail").
- Fully autonomous promotion (human-in-the-loop is required by design).
- Live meeting intelligence changes (separate, premium track).
- Multi-tenant infra work beyond what export/views require.

## 3. Success criteria

- A real pilot-scale corpus answers "what did we know about account X on date D?" and "what changed since D?" with correct results — no synthetic data.
- For a given account, the system produces a decision audit ("N decisions; X stale, Y superseded, Z contradicting") that a human verifies as accurate against the transcripts.
- At least one **cross-person contradiction** is detected that keyword matching would miss (semantic, not lexical).
- A user can export an account's world model to markdown, open it outside IMI, and it is legible and accurate (the portability test).
- The pilot success line holds: someone says "I wouldn't have caught that without this," driven by a world-model artifact.

---

## 4. Phasing overview

```
Phase 1  Temporal substrate        (Semantica — verify/harden)  ── already implemented
Phase 2  Decision lifecycle        (state machine + lineage)     ── builds on Semantica ContextGraph
Phase 3  Semantic contradiction    (LLM/embedding comparison)    ── replaces keyword heuristic
Phase 4  World Model surface       (view + portable export)      ── v0 can start now; richens as P2–3 land
```

Phase 4's **export v0** is intentionally decoupled: the temporal substrate
already works, so it can ship against today's data to give us a demonstrable
artifact early, then gain decision-state depth as Phases 2–3 complete.

---

## 5. Phase 1 — Temporal substrate (Semantica: verify & harden, do NOT migrate)

**Status.** Already implemented. Semantica provides the temporal substrate via
Neo4j validity windows (`valid_from`/`valid_to`) and `GraphBuilder(enable_temporal=True)`.
The following are real and tested:
- `SemanticaKnowledge.get_state_at(entity_id, t)` — point-in-time entity state.
- `SemanticaKnowledge.get_active_relationships(entity_id, t)` — relationships valid at t.
- `SemanticaKnowledge.get_provenance(entity_id)` — source/creation timeline.
- `TemporalQueryService`: `what_changed`, `what_changed_between`, `graph_as_of`,
  `temporal_blast_radius` (temporal_queries.py).
- Tests: `test_temporal_queries.py`, `test_temporal_mcp_tools.py`.

**Do not adopt Graphiti + Kuzu.** That plan predates the Semantica integration
and proposes to rebuild what Semantica already does. Semantica is installed
(`semantica[llm-anthropic,graph-neo4j]`), wired in `main.py`, and is the chosen
engine. Migrating would throw away working, tested code.

**Remaining work (hardening, not building)**
- R1.1 **Validity-window coverage:** ensure decisions/signals and their edges
  consistently set `valid_from` (and `valid_to` on supersession — see Phase 2).
  Today entities set `valid_from` from metadata; confirm signals/decisions do too.
- R1.2 **Validate on real data:** run the temporal queries against a
  pilot-scale corpus (not synthetic) and confirm "state at date D" / "what changed
  since D" return correct results. This is the real-data gap, not a code gap.
- R1.3 **Expose temporal queries via MCP** if not already (so external harnesses
  can ask "as of T"). Verify against the current 19-tool set.
- R1.4 **Decide on bi-temporal need.** Semantica tracks valid-time (when a fact
  was true). If we need transaction-time too ("what did we *know* on date X"),
  evaluate whether `get_provenance` + `created_at` suffice or whether a second
  time axis is worth adding. Default assumption: not needed now.

**Exit:** temporal queries verified correct on a real transcript set; decisions
and signals reliably carry validity windows.

## 6. Phase 2 — Decision lifecycle + supersession lineage

**Problem.** Decisions aren't modeled as stateful objects. The lifecycle states
in our messaging (active/stale/superseded/temporary/zombie/conflicting) don't
exist in code; there's no old→new lineage.

**Approach.** Define a `DecisionRecord` as a first-class node (aligns with the
May 8 ADR: Signals vs DecisionRecords). A *signal* is a raw observation from a
transcript; a *DecisionRecord* is a promoted, stateful decision in the
constitution. Model lifecycle transitions and lineage edges over Graphiti's
bi-temporal substrate so state changes are time-anchored.

**Decision lifecycle states**
- `active` — current, in force.
- `stale` — not referenced/actioned within a configurable window.
- `superseded` — explicitly replaced; linked to successor via `SUPERSEDED_BY`.
- `temporary` — marked short-term with an intended revisit date.
- `zombie` — temporary whose revisit date passed without action.
- `conflicting` — contradicts another active decision (set by Phase 3).

**Requirements**
- R2.1 `DecisionRecord` node type with content, rationale, owner, participants, decided-at, status, expiry (for temporary), and lineage edges (`SUPERSEDES` / `SUPERSEDED_BY`, `DEPENDS_ON`).
- R2.2 Promotion path: signal → candidate DecisionRecord → (human-confirmed) DecisionRecord. No auto-promotion to `active` without human sign-off (ties to Phase 4 ritual).
- R2.3 Staleness + zombie evaluation job: runs on a schedule and on graph change; uses Phase 1 temporal data.
- R2.4 Supersession detection: when a new decision covers the same subject as a prior one, propose `SUPERSEDED_BY` (human-confirmed).
- R2.5 Lifecycle transitions are time-anchored and queryable ("what was this decision's state on date D?").
- R2.6 MCP tools expose DecisionRecords and lineage (extend the existing tool set).

**Exit:** decisions carry real, queryable state and lineage on real data; staleness/zombie surface correctly.

## 7. Phase 3 — Semantic contradiction & drift detection

**Problem.** `_detect_contradictions` is keyword/sentiment matching — it can't
catch a real cross-person contradiction phrased differently in two meetings.

**Approach.** LLM-based semantic comparison between DecisionRecords (and between
a new signal and standing decisions). Reuse/upgrade `CompareStatementsTool`
(`app/services/tools/compare_statements.py`), which already defines comparison
types (contradictions/inconsistencies/changes/conflicts) but needs to operate on
structured decisions, not raw strings. Graphiti's edge-invalidation gives a
substrate signal; the LLM layer adds decision-level judgment.

**Requirements**
- R3.1 Semantic contradiction check: given a new/changed decision, compare against active decisions on the same subject/entity; emit a `CONFLICTS_WITH` candidate with rationale and the two sources.
- R3.2 Drift detection: flag when a current conversation moves against a standing decision (the Andon Cord trigger). Output the discrepancy + both sources, never an accusation (compass-not-camera framing).
- R3.3 Confidence scoring + threshold; below threshold → current layer only, above → surfaced for review. Avoid false-positive fatigue (this kills trust).
- R3.4 Cross-person attribution: contradictions name the speakers/meetings (leverages `speaker_entity_mapper`).
- R3.5 Every flag is a *candidate* requiring human confirmation before it changes a decision's status to `conflicting`.

**Open risk:** precision. A noisy contradiction detector is worse than none —
it trains users to ignore alerts. Tune for precision over recall; measure
false-positive rate on the pilot corpus before shipping push alerts.

**Exit:** at least one real, semantically-detected cross-person contradiction on
pilot data that keyword matching misses, at acceptable precision.

## 8. Phase 4 — World Model surface (view + portable export)

**Problem.** No constitution/audit export exists; the intelligence is trapped in
the graph. This is both the primary demo artifact and the answer to the
portability/lock-in critique.

**Approach.** Render each account/engagement as a **world model**: stable layer
(constitution: confirmed DecisionRecords + standing rules) above a current layer
(situation: what changed, drifting, stale, candidate promotions). Emit it as
portable markdown mirroring the two-layer world-model file structure (stable
above a `---`, current below). Provide a governed **review/promotion** flow
that productizes the weekly review ritual described in the concept doc.

**Requirements**
- R4.1 **Export v0 (ship early):** generate a constitution markdown file for an account from current data — confirmed decisions, owners, rationale, status — committed to git and downloadable. Legible outside IMI.
- R4.2 **Decision audit artifact:** "Across N meetings: D decisions, X stale, Y superseded, Z conflicting," with links to sources. Markdown + in-app view.
- R4.3 **World model view:** two-layer rendering (stable constitution / current situation). Reuses existing renderers where possible.
- R4.4 **Promotion ritual:** present current→stable diff; human confirms/corrects/promotes; approved changes write to the stable layer (DecisionRecords + markdown) and stamp a review date. No write without sign-off. Mirror the skill's "fabricated deadline" guard — flag intended-vs-completed drift.
- R4.5 **Pre-meeting brief** = the account's world-model slice for an upcoming meeting (reuses the brief surface, now backed by real state).
- R4.6 Human-authored standing-rules section in the stable layer (the deliberate operating contract), alongside extracted decisions.
- R4.7 Export formats: markdown (authoritative/portable) first; docx/PDF optional later for sharing.

**Exit:** a user exports an account world model to markdown, opens it elsewhere,
and it's accurate; the audit artifact drives the "I wouldn't have caught that"
moment on real data.

---

## 9. Cross-cutting requirements

- **Corpus stays authoritative.** Stable layer (DecisionRecords + markdown + git)
  is the source of truth; the Semantica/Neo4j graph is a rebuildable cache. Never
  let the graph become the only home of institutional truth (the lock-in test).
- **Human-in-the-loop gates.** Nothing reaches the stable layer without
  confirmation. Aligns with the permissions framework (Bucket 2 reversible
  writes for local corpus; promotion is explicitly gated).
- **Compass, not camera.** All drift/contradiction output is framed as
  navigation, attributed to sources, and routed to human judgment — never
  enforcement.
- **Precision over recall** on all detectors that drive alerts.

## 10. Sequencing, dependencies, effort

| Phase | Depends on | Rough effort | Ships |
|-------|-----------|--------------|-------|
| 1 Temporal substrate | already built (Semantica) | low — verify + harden on real data | Verified "as of T" / "changed since" |
| 2 Decision lifecycle | Semantica ContextGraph | medium | Stateful decisions + supersession lineage |
| 3 Semantic contradiction | P2 decision objects | medium | Real cross-person conflict detection |
| 4 World model surface | export v0 independent; full depth needs P2–3 | medium | Constitution + audit + review ritual |

Suggested order of visible wins: **Export v0 (R4.1–R4.2)** first — the temporal
substrate already supports it — for an immediate demo artifact, then P2 → P3,
folding each into the world model view as it lands.

## 11. Risks

- **Contradiction precision** — false positives destroy trust; gate behind
  measured precision before enabling push alerts.
- **Validity-window gaps** — if decisions/signals don't reliably carry
  `valid_from`/`valid_to`, temporal queries silently degrade. Audit coverage in P1.
- **Scope creep into live intelligence** — explicitly out of scope here.
- **Promotion-flow friction** — if the review ritual is heavy, users skip it and
  the stable layer drifts. Keep it conversational and fast (the skill is the model).
- **Temptation to re-platform** — resist swapping the graph engine (Graphiti/Kuzu
  or otherwise) unless a concrete, validated need appears. Semantica works.

## 12. What changes in the codebase (pointers, not exhaustive)

- P1: audit `valid_from`/`valid_to` coverage on signals/decisions; validate
  `temporal_queries.py` on real data; expose temporal queries via MCP if missing.
  **No new graph engine, no `get_state_at` deletion — it's real.**
- P2: new `DecisionRecord` model + lifecycle service; supersession/lineage edges;
  staleness/zombie evaluation job. Build on Semantica `ContextGraph`.
- P3: upgrade `app/services/tools/compare_statements.py` to operate on
  DecisionRecords; replace keyword `_detect_contradictions` (temporal_queries.py)
  with semantic comparison using Semantica embeddings/ContextGraph.
- P4: world-model export + view service; promotion endpoint(s).
- Extend: MCP tool set for DecisionRecords, lineage, world-model export.
- Tests: temporal queries on real data, lifecycle transitions, contradiction
  precision on pilot corpus, export legibility.
- Housekeeping: mark `docs/plans/graphiti-kuzu-integration-plan.md` superseded.

## 13. Open questions (carried from concept doc)

- World-model granularity (account vs engagement vs team) — likely domain-configurable.
- Review cadence: weekly pull vs post-meeting push vs both.
- Who can promote (role-based vs single owner) — ties to permissions framework.
- Resolution path when two humans' promotions conflict (the genuinely multi-person problem).
