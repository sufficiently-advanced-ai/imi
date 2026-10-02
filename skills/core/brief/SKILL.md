---
name: brief
description: >-
  Brief the user before a meeting, call, or piece of engagement work, from their imi
  knowledge base: open items, recent decisions, who is in the room, and threads still
  unresolved. Use when the user says "brief me before my call with X", "prep me for the
  Y meeting", "what do I need to know before I talk to Z", "I have a session with [client
  or person] in an hour", or names an upcoming meeting together with a client, project or
  person. Works with any imi domain.
---

# Brief

Walk the user into a meeting with full context and nothing important hiding in a meeting
they missed. Surface what is relevant and unresolved; do not tell them what to do.

All tools below are on the imi MCP server. Depending on the client they may appear with a
prefix (for example `mcp__imi__search_signals`); use whichever form is present. If no imi
tools are available, say so and stop — do not brief from memory.

## 1. Pull the subject out of the request

From the user's message, take:

- the **organization, engagement or project** the meeting is about;
- the **people** attending, if named;
- the **meeting type or topic**, if given (kickoff, check-in, renewal, a named decision).

If something is ambiguous, make a reasonable guess, proceed, and state the assumption in one
line at the top of the brief. Do not ask clarifying questions before looking things up.

## 2. Resolve entities

For each organization, engagement and person, call `get_entity_by_name` to get its entity id.
Use the shortest unambiguous name ("Acme", not "Acme Industrial Holdings Inc."). If a
lookup fails, try `search_knowledge_graph` with the name and pick the best match of the right
type. Entity types come from the instance's domain schema — do not assume names like
`client` or `account`; use what the lookup returns.

If nothing resolves, say the knowledge base has nothing on that subject yet and stop.

## 3. Gather, in parallel

1. **Subject signals** — `search_signals` with the organization's or engagement's
   `entity_id`, `max_results` 30, no type filter. You want open action items, recent
   decisions, and insights (risks, scope flags, blockers).
2. **Attendee signals** — for each named attendee, `search_signals` with their `entity_id`,
   `max_results` 20. This shows commitments and moments they were part of.
3. **Connections** — `find_related_entities` on the subject (`mode` "neighbors") for the
   people, engagements and systems around it; `list_entity_profiles` for attendee roles.
4. **Recent meetings** — `list_meetings` and keep the ones whose title or participants match
   the subject; note the latest date.
5. **What changed lately** — `find_changes` on the subject with `date_from` set to the date
   of the last meeting you found (or 30 days ago). Its dates are when things happened.
6. **Anything said in passing** — `memory_recall` with a query naming the subject and
   meeting topic. Keep the default record lane. Note the `request_id`.

For a fuzzy subject ("the migration project"), `ask_kb` with the question in plain words is a
good first step to find the right entities; then continue with the steps above.

## 4. Filter before writing

- **Flags** (insights about risk, scope, blockers, drift): always surface, whatever their age.
- **Open items**: action items whose status is open or empty. Skip done ones.
- **Decisions**: the 3–5 most recent relevant ones. If a decision was superseded (a later
  signal changed it), report only the current one and say what it replaced.
- **Key points**: only those that illuminate something specific and still unresolved.

A brief that takes three minutes to read has failed. Be selective.

## 5. Write the brief

```
## Brief: <subject> — <meeting type if known>
<attendees if known> · <today's date> · last met <date of latest meeting, if any>

### Flags
Risks, scope questions, blockers, conflicts. Omit the section if there are none.

### Open items
**Owner** — what they owe *(source meeting or document, date)*

### Recent decisions
One line each, current version only. *(source, date)*

### Who's in the room
Per attendee: role, what they are connected to, and anything notable from their history
(recurring asks, past commitments). Two lines each at most.

### Still open
Threads raised earlier and never resolved, stated neutrally. Omit if none.
```

Dates are when things happened (meeting date, sent date), not when imi recorded them.
Omit empty sections; never write "none".

## 6. Close the loop

Call `record_memory_usage` with the `request_id` from step 3.6, listing the record ids that
made it into the brief as `used_memory_ids` and any you deliberately left out in `ignored`.
This improves future recall ranking.

## Tone

A compass, not a camera. Facts and open threads, no editorializing, no advice unless asked.
Readable in under 90 seconds.
