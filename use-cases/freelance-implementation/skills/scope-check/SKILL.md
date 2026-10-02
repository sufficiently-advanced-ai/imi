---
name: scope-check
description: >-
  Check a client request against the engagement's statement of work in imi — in scope, out
  of scope, or unclear — and, when it is out of scope, draft a change request with work, fee
  and schedule impact for the user to send. Use when the user says "is this in scope", "the
  client just asked for X", "do I need a change request for Y", "draft a CR", or describes a
  new ask on a fixed-fee or SOW-bound engagement.
---

# Scope Check

For a freelancer on fixed-fee work, the expensive mistake is doing out-of-scope work without
a written change. This skill answers one question — is this request covered? — from what the
statement of work and later decisions actually say, and drafts the change request if not.

All tools below are on the imi MCP server (possibly prefixed, e.g. `mcp__imi__ask_kb`). If
no imi tools are available, say so and stop.

## 1. Identify the engagement and the request

From the user's message: the client, the engagement (if there are several), and the request
in one sentence. Resolve the engagement with `get_entity_by_name` or
`search_knowledge_graph` (entity type `engagement` in this pack's domain).

## 2. Find what governs it

1. `ask_kb` with the intent: "What does the statement of work for <engagement> say is in
   scope and out of scope, and what are its change-control terms?" Pass the engagement's
   entity id in `entity_context`.
2. `search_signals` with the engagement's `entity_id` and `signal_type` "decision" — later
   decisions can widen or narrow scope (an approved change request is one).
3. `memory_recall` with the request in plain words, to catch earlier discussions of the same
   ask. Keep the `request_id`.

Quote the SOW wording you rely on. If imi has no SOW for the engagement, say so — the answer
is then "unclear", and the user should file the SOW (see this pack's Drive recipe).

## 3. Decide

- **In scope** — the SOW or an approved change covers it. Cite the section or decision.
- **Out of scope** — the SOW excludes it, or it adds deliverables, systems or data sources
  beyond the scope list. Cite the exclusion.
- **Unclear** — the SOW is silent or ambiguous. Say what is ambiguous and suggest the one
  question to ask the client.

## 4. Draft the change request (out of scope only)

```
Subject: Change request <CR-nn> — <short name>

Work: <what will be done, in the client's terms>
Fee: <the user's number — ask if you don't have one; never invent a price>
Schedule impact: <effect on milestones and the go-live date, or "none">
Approval: per <SOW section>, this takes effect once <approver> approves it in writing.
```

Number it after the highest CR number imi already knows for this engagement. Use the
approver the SOW names. Do not send anything — the user sends it.

## 5. Close the loop

Call `record_memory_usage` with the recall `request_id` and the record ids you relied on.
Do not write the decision back yourself; once the client approves, the approval email reaches
imi through the Gmail recipe.
