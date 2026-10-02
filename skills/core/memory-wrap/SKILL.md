---
name: memory-wrap
description: >-
  At the end of a working session, find the decisions, facts and lessons worth keeping and
  save the ones the user approves to their imi knowledge base. Use when the user says "wrap
  up", "anything worth saving", "memory check", "let's close out", "save anything
  important", "before we go", or when a substantive session winds down after real decisions
  were made or new facts surfaced. Works with any imi domain.
---

# Memory Wrap

Turn the durable part of this conversation into memory. The bar: **would knowing this in six
months change how the user works or decides?** If yes, propose it. If it is marginal, skip it.
This is not a log of the conversation.

All tools below are on the imi MCP server (possibly prefixed, e.g. `mcp__imi__capture_thought`).
If no imi tools are available, say so and stop.

## 1. Check what imi already knows

Run 2–3 `memory_recall` queries on the main subjects, people and projects of the session,
with `record_kinds` ["capture", "agent_memory"]. Do not read everything — just enough to avoid
proposing what is already stored. Keep each `request_id`.

## 2. Pick candidates (be skeptical)

- **Decision** — a concrete choice about the work, its direction or process.
- **Fact** — specific, non-obvious, new information about people, organizations, systems,
  commitments.
- **Lesson** — an insight, a named concept, a way of framing something that clicked.

Skip anything already stored, process chatter without a conclusion, things that only matter
inside this conversation, and anything vague. Never propose secrets, credentials, access
tokens, or large pasted code or transcripts.

Aim for 3–7. If you have 10+, tighten the filter. If there is nothing, say so — not every
session produces durable knowledge.

## 3. Ask before saving

```
Found 4 things worth saving — "all", "none", or numbers (e.g. "1, 3"):

1. [DECISION] <one self-contained sentence> · tags: a, b, c
2. [FACT] ...
3. [LESSON] ...
```

Wait for the answer. Save nothing without it.

## 4. Save what was approved

Choose the tool by shape:

- **A few standalone items** — one `capture_thought` per item: `content` = the item, prefixed
  with its type ([DECISION], [FACT], [LESSON]) and expanded enough to stand alone months from
  now; `tags` = the proposed tags plus the type; `source` = "manual". `deduped: true` means
  imi already had it — nothing more to do.
- **An end-of-task batch** (the session finished a defined piece of work) — one
  `memory_writeback` with `memory_payload` holding `decisions`, `lessons`, `next_steps`,
  `unresolved_questions` as lists of strings, and an `idempotency_key` that is stable for this
  session (e.g. the date plus a short slug), so a retry does not duplicate.

What you save is **evidence, pending review** (ADR-002): searchable immediately, but it only
becomes instruction-grade when the user confirms it in imi's review queue. The user's "yes"
here decides what is written, not what is trusted — say so in one line when reporting.
Never try to mark memories confirmed yourself.

## 5. Report and close the loop

One line: how many saved, how many already present. Then call `record_memory_usage` for each
recall `request_id`, listing the record ids that changed what you proposed or skipped.

## Tone

Housekeeping, not ceremony. The list should take ten seconds to review.
