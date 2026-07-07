# PRD: Memory Governance, Provenance & Retrieval

*Status: Phases G1–G4 implemented (domain logic) — integration pending. Date: 2026-06-05, status updated 2026-06-09. See §10.1.*
*Extends: `docs/prd/decision-state-and-world-model-prd.md` (DecisionRecord lifecycle) and
`docs/adr/ADR-001-signals-decision-records-routing-approval-gates.md` (Signals vs DecisionRecords,
4-way approval gates). North star: `docs/world-model-concept.md`.*
*Source material: an earlier local-memory prototype ("openbrain") whose governance
and retrieval design is the blueprint ported here (concepts/schema, not code).*

---

## 1. Why this doc exists

We built **openbrain**, a local AI memory system. Its conceptual core is a **trust ladder**:
every memory carries provenance + review status and two authority gates — `canUseAsEvidence` and
`canUseAsInstruction` — promoted through a review state machine, corrected by superseding (never
editing), logged in an append-only audit, deduped by content fingerprint, and fed by a `sources`
table that captures web/mail/rss on a schedule. Retrieval is pure-semantic (pgvector HNSW) with an
optional exponential recency half-life.

imi has already specified the *adjacent* half of this. The **Decision State Engine PRD** defines
`DecisionRecord` as a first-class node with a **temporal lifecycle**
(`active/stale/superseded/temporary/zombie/conflicting`) on Semantica's temporal substrate. **ADR-001**
fixes the Signal→DecisionRecord distinction and the 4-way approval gate (Allow/Block/Revise/Escalate).
What neither covers is the **provenance/authority** dimension, a **row-level audit trail**, **general
(non-meeting) capture**, and **semantic retrieval over the signal/decision layer**. openbrain is a
working reference implementation of exactly those gaps.

This PRD layers openbrain's governance, audit, retrieval, and general-capture concepts onto the
existing DecisionRecord model. It does **not** redefine DecisionRecord or its lifecycle — those are
owned by the decision-state PRD.

### Code reality vs concept (2026-06-05)

| Capability | Where it stands | Source |
|------------|-----------------|--------|
| DecisionRecord as first-class node + lifecycle | **Specified, not built.** | decision-state PRD §6 (P2) |
| Signal vs DecisionRecord + 4-way gate | **Decided.** | ADR-001 |
| Signal model (confidence, entities, status) | **Real.** Lacks governance fields. | `app/models/signal.py` |
| Provenance taxonomy (who said it / vouched) | **Missing.** | — (openbrain `provenanceStatus`) |
| Two-tier authority (evidence vs instruction) | **Missing.** | — (openbrain `canUseAs*` + CHECK) |
| Row-level append-only audit log | **Missing.** PRD has a decision-audit *export* only. | — (openbrain `memory_audit`) |
| Content-fingerprint dedup | **Missing.** | — (openbrain `fingerprint.ts`) |
| Semantic retrieval over signals/decisions | **Missing.** `search_signals` is JSON-filter only. | `mcp_tool_definitions.py:173` |
| Semantic retrieval over entities/transcripts | **Real.** | `semantica_search.py:37,112` |
| "Hybrid" search = true fusion | **No.** Vector with graph *fallback*, not fused. | `semantica_search.py:101–110` |
| General (web/mail/manual) capture | **Missing.** Ingest is meeting-only. | `orchestrators/ingest_orchestrator.py` |

## 2. Goals / non-goals

**Goals**
- Add a **provenance + authority** axis to Signals/DecisionRecords, orthogonal to the temporal
  lifecycle the decision-state PRD already defines.
- Enforce a **two-tier authority gate**: content usable as *evidence* (context) vs usable as
  *instruction* (guidance the system acts on). Instruction-grade requires human confirmation.
- Add a **row-level, append-only audit trail** for every memory mutation, independent of the
  decision-audit export artifact.
- Make the **signal/decision layer semantically searchable** and add *true* hybrid fusion + recency.
- Add a **general memory capture** layer (web/mail/manual) feeding the same governance ladder.
- Keep the git-backed markdown corpus authoritative; the Neo4j/Semantica graph stays a rebuildable
  cache (inherits decision-state PRD §9).

**Non-goals (this PRD)**
- Re-platforming the embedding/graph stack. openbrain is MLX/pgvector/1024-dim; we are
  FAISS/MiniLM/384-dim + Neo4j + git. We port **concepts and schema, not code**.
- Redefining DecisionRecord or its lifecycle states (owned by decision-state PRD §6).
- Predictive analytics or fully autonomous promotion (human-in-the-loop required, per ADR-001 §3).
- Live meeting-intelligence changes.

## 3. Success criteria

- A DecisionRecord can be queried on **both** axes: "is it still in force?" (lifecycle) and "is it
  trustworthy enough to act on, and who vouched?" (provenance/authority) — and the two compose
  without contradiction.
- An agent reading the corpus can request *instruction-grade only* and provably never receive
  agent-generated, unconfirmed content (the openbrain CHECK-constraint guarantee, reproduced here).
- Every promotion/rejection/supersession leaves an immutable audit row that survives hard deletion.
- A natural-language query over signals/decisions returns semantically-ranked results (not just
  exact-field JSON filtering), with optional recency weighting.
- A web article or manual note can be captured, enriched, deduped, and enters the same ladder as
  meeting-derived signals.

---

## 4. Concept mapping: openbrain → imi

| openbrain concept | What it does | imi target |
|-------------------|-------------|---------------|
| `provenanceStatus` ∈ {observed, inferred, user_confirmed, imported, generated, superseded, disputed} | How the memory came to exist | New field on Signal / DecisionRecord. **Reconcile** `superseded` with the lifecycle state of the same name (see §6 open question). |
| `reviewStatus` + `ReviewMemory` actions (confirm, reject, evidence_only, dispute, restrict_scope, mark_stale, supersede) | Human review state machine | Map onto **ADR-001's 4-way gate**: confirm→Allow, reject→Block, evidence_only/dispute→Revise, restrict_scope/escalate→Escalate. `mark_stale`/`supersede` are lifecycle ops (decision-state PRD), not gate responses. |
| `canUseAsEvidence` (default true) / `canUseAsInstruction` (true only if user_confirmed or imported, enforced by SQL CHECK) | Two-tier authority gate | Two booleans on the record + an enforced invariant: instruction-grade ⇒ human-confirmed provenance. The durable rule worth cutting as **ADR-002**. |
| `supersedes` UUID chain; record immutable, corrected by replacement | Correction lineage | Already in decision-state PRD as `SUPERSEDED_BY`/`SUPERSEDES`. Adopt openbrain's "immutable, correct by superseding" framing explicitly. |
| `memory_audit` (append-only; action ∈ capture/update/review/delete/supersede; not an FK, survives hard delete) | Row-level mutation log | New audit trail beside the signal JSON. Distinct from the PRD's decision-audit *export* (which is an account-level artifact, not a mutation log). |
| `contentFingerprint` = sha256(normalize(content)); advisory, non-unique index | Soft dedup of freeform captures | Dedup key for general capture and re-ingestion. Note: openbrain keeps it **non-unique** because the corpus has legitimate duplicates. |
| `sources` table (kind ∈ mail/rss/webpage; interval; lastSyncedAt) + enrichment (LLM summary/tags/entities) | Scheduled general capture | New capture layer (see §7), separate from the meeting-only ingest pipeline. |
| `memory_links` (write-time top-3 ≥0.75 cosine) | Similarity graph at write time | Optional; our Semantica graph already does query-time traversal. Consider only if write-time precompute beats live queries. |
| Recency half-life blend: `score = sim·(1−w) + e^(−ln2·age/half)·w` | Time-aware ranking | Adopt directly in the new semantic signal/decision search (§5). |

## 5. The two-axis governance model (central contribution)

A DecisionRecord lives at the intersection of **two independent axes**:

```text
                TRUST / AUTHORITY axis  (this PRD — from openbrain)
                provenance × review × {evidence | instruction}
                            ▲
                            │   user_confirmed, canUseAsInstruction=true
                            │   ───────────────────────────────────────
                            │   imported / evidence_only, canUseAsEvidence=true
                            │   ───────────────────────────────────────
                            │   generated / observed, pending review
                            └───────────────────────────────────────►  LIFECYCLE axis
                                active → stale → zombie / superseded     (decision-state PRD §6 —
                                conflicting (set by contradiction)        temporal validity)
```

- The **lifecycle axis** answers *"is this decision still in force?"* — temporal, evaluated by the
  staleness/zombie job and contradiction detector (decision-state PRD §6–7).
- The **trust axis** answers *"where did this come from, has a human vouched, and may the system act
  on it?"* — provenance + review + the evidence/instruction gate.

They are orthogonal: a decision can be `active` (lifecycle) yet only `evidence`-grade (trust) because
no human confirmed it; or `superseded` (lifecycle) but historically `instruction`-grade. Storage
follows the PRD's rule — **corpus authoritative** (signal JSON + DecisionRecord markdown in git),
**graph is a rebuildable cache** (Neo4j node properties for both axes; Semantica validity windows for
the lifecycle axis).

The **two-tier authority gate** is the load-bearing invariant. openbrain enforces it at the data layer:

```sql
-- openbrain drizzle/0007 (reference)
can_use_as_instruction IS NOT TRUE
  OR COALESCE(provenance_status IN ('user_confirmed','imported'), false)
```

imi has no SQL layer to host a CHECK, so the invariant must be enforced in `signal_promoter`
(the single writer of promotions, per ADR-001 §1) and re-asserted on read in the retrieval path —
agents requesting *instruction-grade* context get a filter that cannot return unconfirmed,
agent-generated records.

## 6. Evidence → fact lifecycle (the promotion state machine)

Fuses openbrain's `ReviewMemory` transitions, ADR-001's 4-way gate, and the decision-state PRD's
promotion path (R2.2). `signal_promoter` owns it; nothing else writes promotions.

```text
  Signal (raw, ephemeral)                     provenance=generated|observed
     │   canUseAsEvidence=true, canUseAsInstruction=false, reviewStatus=pending
     │
     │  promote()  ── confidence threshold OR human "Allow"
     ▼
  Candidate DecisionRecord                    provenance=inferred
     │   gate (ADR-001): Allow / Block / Revise / Escalate
     ├── Block   → rejected            canUseAsEvidence=false
     ├── Revise  → back to proposer with corrections (evidence_only / dispute)
     ├── Escalate→ higher-authority human
     └── Allow   → confirm
                     ▼
  Confirmed DecisionRecord                    provenance=user_confirmed
        canUseAsInstruction=true (gate invariant satisfied)
        lifecycle=active; corrections via SUPERSEDED_BY (immutable, never edited)
```

- **No auto-promotion to instruction-grade** without human sign-off (ADR-001 §3, PRD R2.2).
- A confidence threshold may auto-advance Signal → *candidate* (evidence-grade), but the
  evidence→instruction step is always gated.
- Every transition writes an audit row (§4) and stamps validity windows so lifecycle queries stay
  correct (PRD R1.1).

## 7. Hybrid retrieval over the signal/decision layer

**Problem.** `search_signals` (`mcp_tool_definitions.py:173`) filters signal JSON in memory by exact
fields. Signals/DecisionRecords are never embedded, so there is no semantic recall over the layer that
matters most. And the entity/transcript path's `hybrid_search` (`semantica_search.py:37`) is
vector-with-graph-*fallback* (lines 101–110), not true fusion.

**Approach.**
- **Index** signals and DecisionRecords into the existing Semantica vector store via a new method
  beside `index_entity` (`semantica_search.py:206`) and `index_transcript_chunk` (line 259), with
  `content_type="signal"|"decision"` plus governance metadata (provenance, review, lifecycle,
  evidence/instruction, tenant) so filters compose with similarity.
- **Search** via a new `search_signals_semantic` path (or an upgraded `search_signals`) that does
  **true hybrid fusion** — Reciprocal Rank Fusion (or score-blend) over a vector list and a keyword
  list — rather than fallback. Layer openbrain's recency half-life on top:
  `score = sim·(1−w) + e^(−ln2·age/half)·w`, default `w=0` (pure similarity), `half=90d`.
- **Trust-aware retrieval:** the authority gate (§5) becomes a first-class filter — an agent can ask
  for *instruction-grade only*, *evidence-or-better*, or *include candidates*, enforced in the query.
- **Surface:** new/updated MCP tool defs in `mcp_tool_definitions.py`; dispatch in
  `routes/mcp_server.py`; implementation in `chat_tools.py`, calling Semantica. Indexing triggers from
  `signal_store.save()` and the promotion writer (`graph/signal_graph_writer.py`), best-effort/non-blocking.

## 8. General memory capture layer

**Problem.** Ingestion is meeting-only (`orchestrators/ingest_orchestrator.py`, `routes/ingest.py`):
classify → build meeting → promote signals → enrich graph → persist. There is no path for a web
article, an email, or a manual note to enter the system.

**Approach.** Port openbrain's `sources` model as a **separate capture surface** feeding the *same*
governance ladder:
- A `sources`-style registry (kind ∈ webpage/mail/manual/rss; interval; lastSyncedAt; lastError) for
  recurring or one-off capture.
- An **enrichment** step (LLM extracts summary/tags/entities) mirroring openbrain's pipeline, writing
  evidence-grade memories (`provenance=imported`, `canUseAsEvidence=true`, `canUseAsInstruction=false`
  until confirmed).
- **Dedup** via content fingerprint (§4) on (source, source_id) and on normalized content, so the same
  article captured twice (or re-synced) does not duplicate.
- These memories are **not** meeting signals; they are general evidence that can be linked to entities
  and, if a human confirms, promoted to instruction-grade — but they do **not** auto-create
  DecisionRecords.

**Boundary.** Keep this distinct from the meeting ingest pipeline; share only the governance ladder,
the entity-linking step, and the vector index. Open question (§11): is general capture community
core or hosted edition?

## 9. Multi-tenancy hook

openbrain ships forward-compat `workspaceId` / `projectId` / `visibility` columns, unused in its
single-user build but designed for exactly the tenant-context primitive in our open-core split.
**Decision:** the governance ladder
is tenant-scoped from day one — provenance, review, audit rows, the authority gate, and every vector
index entry carry a tenant key, and retrieval filters on it. Retrofitting tenant scope after the fact
is the expensive path; openbrain's columns are the cheap one.

## 10. Phasing (interlocks with the decision-state PRD)

| Phase | This PRD adds | Depends on / interlocks | Ships |
|-------|---------------|-------------------------|-------|
| G1 Governance fields + authority gate | provenance/review fields, `canUseAs*` invariant in `signal_promoter`, tenant key | Lands with/around decision-state **P2** (DecisionRecord) | Trust-axis queryable; instruction-grade guarantee |
| G2 Audit trail | append-only mutation log surviving hard delete | G1 | Provenance/audit on every mutation |
| G3 Semantic retrieval | index signals/decisions; `search_signals_semantic`; true fusion + recency | Independent — **shippable early** | NL search over the signal/decision layer |
| G4 General capture | `sources` registry + enrichment + fingerprint dedup | G1 (ladder), G3 (index) | Web/mail/manual evidence in the ladder |

G3 is decoupled and the fastest visible win (the vector store already exists). G1 should ride with
decision-state P2 so DecisionRecord is born with both axes. G4 is a later, separable track.

**Net-new vs already specified.** G1's lifecycle/supersession is *already* in decision-state PRD §6;
this PRD adds only the *trust axis* fields and the gate invariant. G2/G3/G4 are net-new.

## 10.1 Implementation status & remaining integration (2026-06-09)

The four phases' **domain logic** is built, TDD'd, and CodeRabbit-reviewed as a stacked PR chain
(merge in order: G1→G2→G3→G4). What remains is **integration wiring** — turning the library code
into a feature reachable end-to-end. None of the wiring below is in the four PRs.

**Shipped (domain logic).**

| Phase | PR | Key files | Tests |
|-------|----|-----------|-------|
| G1 trust/governance axis on `Signal` | #913 | `app/models/signal.py`, `app/services/signal_governance.py` (`apply_review` + 4-way gate) | 22 |
| G2 append-only audit trail | #915 (closes #914) | `app/services/signal_audit.py` (`SignalAuditRecord`, `review_with_audit`, `SignalAuditStore`) | 10 |
| G3 semantic + hybrid retrieval | #916 | `app/services/signal_retrieval.py` (`index_signal`, `search_signals_semantic`, RRF, recency), `semantica_search.py` facade | 19 |
| G4 general capture | #917 | `app/models/captured_memory.py`, `app/services/memory_capture.py` (`content_fingerprint`, `CaptureStore`) | 9 |

What is **enforced today**: the two-tier authority invariant (instruction-grade ⇒ confirmed/imported
provenance) at the model layer; the review state machine; append-only audit records; governance-aware
semantic ranking primitives; and fingerprint dedup. These are unit-tested with fakes — they do **not**
yet run against the live Semantica/Neo4j/git stack.

**Remaining integration work** (each is a discrete, trackable task; spin issues from this list):

- [x] **MCP exposure** — `search_signals_semantic` tool def in `app/services/mcp_tool_definitions.py`,
  dispatch in `app/routes/mcp_server.py`, impl in `app/services/chat_tools.py`. (Today only the
  JSON-filter `search_signals` is exposed.) *(landed 2026-06-09 — authority filter per ADR-002)*
- [x] **Index-on-write** — call `index_signal` from `signal_store.save()` and the promotion path so
  signals become searchable; **backfill** existing signals into the vector index (one-off migration).
  *(landed 2026-06-09 — `app/services/signal_indexing.py` glue; `scripts/backfill_signal_index.py`;
  caveat: FAISS appends rather than upserts — re-index from scratch to dedup)*
- [x] **Audited promotion** — route signal review through `review_with_audit` (G2) so every transition
  emits an audit row; have `signal_promoter` stamp provenance explicitly rather than relying on model
  defaults. *(landed 2026-06-09 — `update_signal` MCP tool gained `review_action`/`actor`; promoter
  stamps LLM→`inferred`, regex→`observed`, both `pending`. Note: signals persisted before this date
  carry the old default `generated`; retrieval treats both as evidence-grade, no migration needed)*
- [x] **Git persistence** — commit the audit JSONL (`SignalAuditStore`) and capture JSONL
  (`CaptureStore`) via `GitOps` at the persistence boundary (the stores produce files + repo-relative
  paths; the commit hook is not yet wired). *(landed 2026-06-09 for the audit path; 2026-07-03 for the
  capture path — `capture_service.capture_and_persist` is the first capture producer: one commit per
  capture covering the record + its audit row)*
- [x] **G4 capture loop live** *(landed 2026-07-03, OB1 absorption Phase 1)* — `capture_thought` MCP
  tool + chat-agent wrapper, `/api/captures` REST (create/list/detail/review), LLM enrichment
  (`capture_enrichment`, OB1 `extractMetadata` port, fallback-on-any-failure), vector indexing
  (`index_capture`, `content_type="capture"`), audited review over captures via the SHARED state
  machine (`review_with_audit(record_kind=...)`; capture audit rows at `memory/audit/`), and the
  `/memory` frontend page (quick capture + governance badges + inline review).
- [x] **Neo4j governance mirroring** — write provenance/review/authority/tenant onto `:Signal` nodes in
  `app/services/graph/signal_graph_writer.py` (best-effort), so graph/Cypher queries see the trust axis.
  (§12 noted audit-mirroring only; the *governance fields on the node* are also needed.)
  *(landed 2026-06-09 — UPSERT + `update_signal_properties` carry all five fields)*
- [ ] **G4 depth** — scheduled `sources` polling (rss/mail) and entity-linking captures into the
  graph. Separate, later track. *(LLM enrichment landed 2026-07-03 with the capture loop; polling and
  graph edges remain — polling was scoped OUT of the 2026-07 OB1 absorption plan.)*
- [ ] **DecisionRecord** — the confirmed/instruction-grade tier; owned by decision-state PRD Phase 2,
  the natural home for promoted facts. The trust axis is built and waiting for it.

Exit for "feature live, not just library": a natural-language query returns governance-filtered
signals via MCP on real data, promotions leave audit rows committed to git, and the graph reflects the
trust axis.

## 11. Open questions

- **`superseded` collision.** The lifecycle axis (PRD §6) and the provenance axis (openbrain) both have
  a `superseded` value. Are they the same event or two? Proposal: one supersession event sets the
  lifecycle state; provenance `superseded` is derived, not independently set.
  **RESOLVED 2026-07-03:** one supersession event — `apply_review("supersede")` atomically sets
  `review_status=merged`, `superseded_by`, `valid_to`, and provenance `superseded` as a derived
  side-effect. No write path may set provenance `superseded` independently; the lifecycle state stays
  computed (`decision_states`).
- **ADR-002?** Does adopting the two-tier evidence/instruction gate as the canonical governance model
  warrant a formal ADR now, or does it ride inside decision-state P2? (Recommendation: cut ADR-002 —
  it is a binding, cross-cutting invariant.)
- **Dedup semantics** across meeting-derived vs general-capture content — is a fingerprint match across
  sources a merge, a link, or independent records?
  **RESOLVED 2026-07-03:** advisory link, never merge. Within a store, fingerprint matches fold
  (existing behavior); across record kinds they record `related_record_ids` on the newer record —
  both keep independent governance state, because merging would launder provenance.
- **General capture: core or hosted?** Ties to the open-core split — is scheduled web/mail capture
  community core or hosted edition?
  **RESOLVED 2026-07-03:** the capture loop (manual/API capture, enrichment, review, UI) is community
  core. Scheduled pollers and third-party capture integrations were scoped out of the OB1 absorption
  plan entirely (neither core nor hosted, for now).
- **Recall half-life default.** openbrain defaults `w=0` (time-invariant). Do decisions want a non-zero
  default so stale-but-active records rank below fresh ones?

## 12. What changes in the codebase (pointers, not exhaustive)

- G1: add governance fields to `app/models/signal.py` (and the DecisionRecord model from decision-state
  P2); enforce the authority invariant in `app/services/signal_promoter.py` (`promote`, line 75); thread
  a tenant key through `signal_store.py` and `graph/signal_graph_writer.py`.
- G2: append-only audit writer beside `signal_store.save()`; audit rows committed to git, mirrored to
  Neo4j best-effort.
- G3: new `index_signal`/`index_decision` + `search_signals_semantic` in
  `app/services/semantica_search.py`; tool defs in `app/services/mcp_tool_definitions.py`; dispatch in
  `app/routes/mcp_server.py`; impl in `app/services/chat_tools.py`. True RRF/score-blend replacing the
  vector-or-fallback shape.
- G4: `sources` registry + enrichment service; fingerprint util (port the openbrain
  fingerprint normalization exactly); keep separate from `orchestrators/ingest_orchestrator.py`.
- Tests: gate invariant (instruction-grade never returns unconfirmed), audit immutability, semantic
  recall on real signals, dedup correctness, tenant isolation.
- Housekeeping: if ADR-002 is cut, link it from ADR-001 and this PRD.

## 13. References

- `docs/prd/decision-state-and-world-model-prd.md` — DecisionRecord + lifecycle (the temporal axis).
- `docs/adr/ADR-001-signals-decision-records-routing-approval-gates.md` — Signal/DecisionRecord + gate.
- `docs/world-model-concept.md` — north star.
- openbrain reference: the openbrain schema and review-flow implementation.
