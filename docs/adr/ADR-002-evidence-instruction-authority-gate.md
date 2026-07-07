# ADR-002: The Evidence/Instruction Two-Tier Authority Gate

## Status

Accepted (2026-06-09).

## Context

The Signal/DecisionRecord model (ADR-001) and the decision-state PRD define a *temporal* lifecycle
for knowledge (active → stale → superseded). The memory-governance sprint (G1–G4,
`docs/prd/memory-governance-and-retrieval-prd.md`) added an orthogonal *trust/authority* axis ported
from an earlier local-memory prototype ("openbrain"): where a piece of knowledge came from, and
whether the system may **act on it** versus merely **reference it**.

That distinction needs a binding, cross-cutting rule because it spans editions and storage layers:

- It is enforced today in the core as a Pydantic `@model_validator` on `Signal` and `CapturedMemory`.
- It will be enforced again in the hosted edition as a native SQL `CHECK` constraint once the memory
  stores move to Postgres.
- It governs what an agent receives when it requests context over MCP.

A rule reproduced in three places (model layer, SQL layer, retrieval filter) is exactly what an ADR
is for: one canonical statement that each implementation must satisfy.

## Decision

Knowledge in the system carries **two independent authority booleans**:

| Flag | Meaning | Default |
|---|---|---|
| `can_use_as_evidence` | May be surfaced as context / cited as evidence | `true` |
| `can_use_as_instruction` | May be used as guidance the system **acts on** | `false` |

**The invariant:** a record may be `can_use_as_instruction = true` **only if** its `provenance_status`
is `user_confirmed` or `imported`.

- **Agent-generated, observed, or inferred** content is **evidence-grade only** — it can inform, but
  the system will not act on it as authoritative.
- Promotion to **instruction-grade requires a human-vouched provenance** (`user_confirmed` via the
  review state machine, or `imported` from a trusted source). This is the openbrain
  `chk_memories_instruction_grade` rule, generalized.
- The invariant is enforced **at the data layer**, not per caller — so no code path (promoter, store,
  retrieval, capture, or a future API) can mint instruction-grade content without confirmed provenance.

**Per-edition implementation (the rule is identical; the mechanism differs):**

| Deployment | Mechanism |
|---|---|
| `imi` (core, SQLite) | Pydantic `@model_validator(mode="after")` on `Signal` / `CapturedMemory` (`app/services/signal_governance.py::instruction_grade_permitted`) |
| Postgres-backed deployments | Native Postgres `CHECK` on the memory tables, NULL-safe via `COALESCE` |
| Retrieval (both) | `search_signals_semantic(authority="instruction")` returns **only** records satisfying the invariant |

**Authority fields are server-injected, never client-settable.** As with `tenant_id`, the
governance fields (`provenance_status`, `review_status`, `can_use_as_*`) must not be
accepted from MCP/API input — a client cannot self-promote a write to `imported`/`user_confirmed`.
Trusted internal callers (promoter, capture connectors, import) set them via server-side options.

## Consequences

**Easier:**
- An agent can request *instruction-grade only* context and is provably never handed unconfirmed,
  agent-generated content.
- The same guarantee holds whether the store is the core's files or the hosted Postgres — the ADR is
  the contract both satisfy, verified by the cross-edition test suite.
- Human-in-the-loop is structural: promotion to authority requires the review action, aligning with
  ADR-001's approval gates.

**Harder:**
- Every store that holds governed knowledge must implement the invariant (model validator *and* SQL
  CHECK) — duplicated enforcement is the price of defense-in-depth across editions.
- The MCP/API surface must keep authority fields out of client input; a new write path that forgets
  this is a privilege-escalation bug. Covered by a negative test.

## Related

- ADR-001 (Signals vs DecisionRecords, approval gates) — the lifecycle axis this complements.
- `docs/prd/memory-governance-and-retrieval-prd.md` §5–6 — the two-axis model and review state machine.
