# ADR-001: Signals vs DecisionRecords, Declarative Routing, and Human-in-the-Loop Approval Gates

## Status

Accepted

## Context

Architecture session May 8, 2026. Three interconnected design questions surfaced that needed formal resolution before implementation drifted in conflicting directions:

1. **What is the difference between a Signal and a DecisionRecord, and when does one become the other?** The codebase had been treating these as loosely interchangeable, leading to ambiguity in the data model and processing pipelines.

2. **How should events and signals be routed through the processing pipeline?** Imperative glue code was accumulating — ad hoc conditionals scattered across webhook handlers, background processors, and chat tools — making the routing logic hard to reason about or audit.

3. **Where should humans be inserted into agentic pipelines, and what are the valid response options?** As the system began taking consequential actions (signal promotion, entity mutation, decision recording), the question of when to pause and ask became urgent.

External validation landed May 11, 2026: Lindy, JPM, and OpenAI independently shipped the same pattern — a separate validator/judge layer with a 4-way escalation model — as the standard answer to agent permission overreach. This directly confirmed the direction from the May 8 session.

## Decision

### 1. Signals vs DecisionRecords

**Signals** are raw, ephemeral observations extracted from meeting transcripts or other input streams. They are unverified, may be contradictory, and carry no authority. A Signal says "someone said X in this meeting."

**DecisionRecords** are promoted, structured artifacts. A Signal graduates to a DecisionRecord when:
- It has been verified against existing context (no unresolved contradiction)
- It has been attributed to an authoritative source (a named decision-maker, not just an observation)
- It has been explicitly promoted — either by a human approval action or by the automated signal promoter meeting a confidence threshold

A DecisionRecord is immutable after creation. Corrections are made by superseding, not editing.

The `signal_promoter` service owns the graduation path. Nothing else in the system should write DecisionRecords directly.

### 2. Declarative Routing

Event and signal routing is declared in domain configuration (`config/domains/*.yaml`), not scattered as imperative conditionals in application code.

Each signal type maps to a named processing path. The routing engine reads this configuration at startup and dispatches accordingly. Adding a new signal type or changing how a signal is handled requires a config change, not a code change.

The central agent is the runtime enforcement point — it consults the routing table before passing signals to downstream processors. No processor should receive a signal that wasn't explicitly routed to it.

Benefits: the routing table is auditable, testable, and visible to non-engineers. Processing failures are attributable to a specific path, not an opaque call chain.

### 3. Human-in-the-Loop Approval Gates

Approval gates are inserted at the signal promotion boundary — i.e., before a Signal becomes a DecisionRecord — and at any point where the system would take an action with external consequences (sending a message, updating a ClickUp task, modifying an entity that other agents read).

The valid response options at any gate are exactly four:

| Response | Meaning |
|----------|---------|
| **Allow** | Proceed as proposed |
| **Block** | Do not proceed; discard the action |
| **Revise** | Do not proceed; return to the proposing agent with human-provided corrections |
| **Escalate** | Do not proceed; route to a human with higher authority or more context |

No other response shapes are valid at a gate. Agents must be designed to handle all four.

Gate placement is declared in domain config alongside routing. A gate can be set to `auto-allow` for low-stakes actions (reducing human burden) but the gate still exists in the architecture — it can be tightened without a code change.

## Consequences

**Easier:**
- Auditing what decisions were made and when — DecisionRecords are the canonical source of truth
- Onboarding new signal types — add a routing entry and optionally a gate, no application code changes
- Explaining agent behavior to external stakeholders — the routing table and gate config are human-readable
- Tightening or relaxing human oversight per action type without deploys

**Harder:**
- Signal promotion requires passing through `signal_promoter` — no shortcuts
- Any new external action type must be registered with a gate, even if set to auto-allow
- The routing config becomes a critical file — changes need review

## Related

- `config/domains/*.yaml` — where routing and gate declarations live
