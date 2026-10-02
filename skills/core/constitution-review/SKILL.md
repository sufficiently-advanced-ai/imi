---
name: constitution-review
description: >-
  Produce a constitution review from the user's imi knowledge base — the standing rules,
  protocols, scope boundaries and commitments currently in force, with what is deferred,
  superseded or in conflict. Use when the user asks "what are our standing rules", "show me
  the constitution", "what have we decided as policy", "what's still in force for [client or
  project]", "what would I tell someone new about how we operate", or wants to audit drift
  between what was decided and what is happening. Works with any imi domain.
---

# Constitution Review

A constitution is not every decision ever made. It is the decisions that still govern what
happens next: protocols, scope boundaries, commitments, policies. "We closed the SSO
project" is a fact for meeting notes. "Change requests need written sponsor approval" is
constitutional.

All tools below are on the imi MCP server (possibly prefixed, e.g. `mcp__imi__get_constitution`).
If no imi tools are available, say so and stop.

## 1. Scope

- **Global** — no organization or project named: everything.
- **Scoped** — one named: resolve it with `get_entity_by_name` (or `search_knowledge_graph`)
  and keep its entity id. Scoped reviews also include global rules that apply to it.

## 2. Load the decisions

1. `get_constitution` — the server's current decision constitution as Markdown: active,
   conflicting, stale and superseded decisions with owners, dates and **governance
   authority** (instruction-grade, evidence-grade, blocked). This is the backbone.
2. `list_decisions` with `state` "active", then again with "stale" and "superseded" — the
   lifecycle states are computed server-side; trust them over your own date arithmetic. Add
   `client_id` when scoped and the domain has client entities; otherwise filter by reading.
3. `search_signals` with `signal_type` "insight" (and the `entity_id` when scoped) — open
   flags that represent decisions not yet made.
4. For any decision whose lineage matters (it replaced or was replaced by another), call
   `get_decision` with its id to read the supersession chain.

## 3. Classify

For each decision ask: **does it govern future behaviour?**

- **Standing rule** — protocols, scope boundaries ("X is out of scope for Y"), ongoing
  commitments, policies, deferred decisions with a revisit date.
- **Situational fact** — something that happened and is finished. Leave it out.

When in doubt, include it: hiding an active rule is worse than showing a closed one.

## 4. Respect authority (ADR-002)

imi separates **instruction-grade** decisions (a human confirmed them) from
**evidence-grade** ones (extracted, not yet reviewed). Keep that line visible:

- Mark each rule *confirmed* or *unreviewed*, from the authority the constitution reports.
- Never present an unreviewed decision as settled policy. If the user wants one promoted,
  point them to the review queue or `update_signal` with a review action — do not confirm
  decisions yourself unless the user explicitly tells you to, item by item.

## 5. Write it

```
## <Organization / client / project> — Constitution
As of <today> · <N> decisions reviewed · <M> confirmed

### Standing rules
- <rule, present tense> — *confirmed|unreviewed* *(decided <date>, <source>)*

### Active commitments
Promises still in motion that bind the organization (not individual to-dos).

### Deferred
Decided but on hold, with the revisit date if known.

### Open flags
Insights that point at a rule nobody has made yet.

### Conflicts and drift
Rules that pull against each other, or later events that contradict a rule. State them
neutrally — they are things to resolve, not accusations.
```

Group standing rules under headings if there are many (engagement protocols, scope
boundaries, commercial terms). Omit empty sections; never write "none". Dates are when the
decision was made, not when imi recorded it.

## Tone

A handbook, not a report: present tense, one sentence per rule. If it is getting long,
situational facts slipped through — filter harder.
