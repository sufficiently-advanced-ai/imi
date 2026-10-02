---
name: what-changed
description: >-
  Report what changed in the user's imi knowledge base since a date — about one client,
  project, person or system, or across everything — separating new facts, replaced
  decisions, new relationships, and material that was only recorded late. Use when the user
  asks "what changed since last week", "what's new on [client] since our last call", "catch
  me up on [project]", "what happened while I was out", "anything new about [person]", or
  wants a weekly review. Works with any imi domain.
---

# What Changed

Answer "what is different now than on date D?" — and be exact about time. imi dates things
by **when they happened** (meeting date, sent date, decision date), not when they were
recorded; content added late about an earlier time is reported separately as *recorded
late*. Keep that distinction in the report.

All tools below are on the imi MCP server (possibly prefixed, e.g. `mcp__imi__find_changes`).
If no imi tools are available, say so and stop.

## 1. Pin the window and the subject

- **Since when?** Use the date the user gives. "Since our last call" → find that meeting with
  `list_meetings` and use its date. "Last week" → seven days before today. No date → ask once,
  or default to 7 days and say so.
- **About what?** One or more named entities, or "everything".

## 2. One subject

1. Resolve it with `get_entity_by_name` (fall back to `search_knowledge_graph`).
2. `find_changes` with `entity_id` and `date_from` (and `date_to` if the user bounded the
   window). It returns, in date order: documents mentioning it, new signals, signals that
   were replaced, relationships stated for the first time, entities first named alongside
   it, and `recorded_late`.
3. For replaced signals, say what replaced what. For decisions, `get_decision` shows the
   lineage when it is not obvious.
4. Optional context: `get_entity_at_time` at the window start gives the "before" picture
   when the user wants a before/after comparison.

## 3. Everything

There is no single global diff call, so build it in waves:

1. **Seed** — `list_meetings` (newest first, `max_results` 50) and keep meetings inside the
   window; `search_signals` with `date_from` (and `date_to`), `max_results` 50, for new
   decisions and action items; `memory_recall` with a broad query ("decisions, commitments
   and changes since <date>") and `recency_weight` above 0 for captures and notes.
2. **Cluster** — group what you found by the entities it mentions. The entities with the
   most new material are the storylines.
3. **Dive** — run `find_changes` on the top 3–5 storyline entities (step 2 above).
4. **Synthesize** — one paragraph per storyline, then the cross-cutting picture: what moved
   together, what contradicts what.

If the user also watches third-party content (articles, newsletters), `memory_recall` with
`lanes` ["record", "library"] adds it under `background`. Keep the two apart in the report:
what the world said is not what we decided.

## 4. Supersession rule

Never report an older claim as current when a newer one replaced it. Sort each storyline's
material by event date; the newest wins, and the report says what it replaced
("cutover moved from April 14 to April 28 — VPN delay").

## 5. Write it

```
## What changed: <subject or "everything"> · <date_from> → <date_to or today>

### Headline
Two or three sentences: the changes that matter most.

### <Storyline / entity>
- <change> *(when it happened, source)*
- Replaced: <old> → <new> *(date)*
- New: <relationship or entity first seen>

### Recorded late
Material about earlier dates that only arrived in this window — it may change history the
user already knew.

### Still open
Questions the new material raises and nothing answers yet.
```

Omit empty sections. Every dated line uses event time.

## 6. Close the loop

For every `memory_recall` you made, call `record_memory_usage` with its `request_id`, the
full record ids you used, and the ones you deliberately ignored.
