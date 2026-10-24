# ADR-009: Review Authority and Revision Binding

## Status

Accepted (2026-10-23). Tightens ADR-002 for reviews that arrive over the unauthenticated MCP
surface (ADR-008) and for edits made after a review.

## Context

ADR-002 makes human review the only way a signal becomes instruction-grade: `confirm` sets
`provenance_status=user_confirmed` and `can_use_as_instruction=true`, and every transition
writes an audit row naming the `actor`. An external code review (2026-10-23) found two gaps
in how that promise held up, both verified against `main`:

1. **The review was not bound to what was reviewed.** `SignalStore.update_signal` applied a
   `content` edit with `model_copy` and left `review_status`, `provenance_status` and
   `can_use_as_instruction` untouched. A confirmed, instruction-grade signal kept its
   authority after its text was rewritten, by anyone, through REST, chat or MCP. The `Signal`
   model had no record of which revision was confirmed.
2. **The MCP review path trusted the caller's word for who it was.** The MCP `update_signal`
   handler forwarded `review_action` and `actor` straight from client arguments. ADR-008
   ships MCP without authentication by design, so any client that can reach the endpoint
   could confirm a signal and write any name into the audit log as the reviewer. On the
   Remote tier that is anyone on the private network.

The MCP path is also a legitimate review surface. `docs/agents/README.md` tells agents that a
person can direct them to confirm a signal, and for a single user on the Local tier that is
the most convenient way to review. Removing it would break real use. Leaving it unlabelled
makes the audit log claim more than it knows.

ADR-007 already set the pattern for this kind of field: the transport that received a call
stamps what it knows server-side (`channel=mcp`) and never takes it from arguments.

## Decision

### 1. A review binds to a content revision

- `Signal` (and, for consistency, `CapturedMemory` and `AgentMemory`) gains
  `reviewed_content_hash: str | None`, the sha256 of the content exactly as reviewed
  (`signal_governance.content_hash`; no normalization, so any textual change is a new
  revision). `apply_review` sets it on every action that leaves the record in a reviewed
  state: `confirm`, `reject`, `evidence_only`.
- `demote_for_content_change(signal, new_content)` is a pure function in
  `signal_governance.py`. When the signal is in a reviewed state (or is instruction-grade)
  and the new content's hash differs from the reviewed hash, it returns a copy with the new
  content, `review_status="pending"` and `can_use_as_instruction=False`. Records reviewed
  before this ADR have no hash; their current content stands in for the reviewed revision.
- **`provenance_status` is left as it is.** Provenance answers "how did this record come to
  exist, and did a human vouch for its source"; that history does not change when the text
  is edited. The model invariant is one-directional (instruction-grade ⇒ `user_confirmed` or
  `imported` provenance), so clearing `can_use_as_instruction` is sufficient to remove the
  authority, and the record keeps its place in ranking as confirmed-provenance evidence.
  `can_use_as_evidence` is also left alone: demotion only ever removes authority, never
  restores it (a rejected record that is edited stays excluded from evidence until reviewed).
- Status, owner and due-date edits never demote, nor does an edit that leaves the text
  identical.
- Every content edit goes through one chokepoint, `SignalStore.update_signal_governed`
  (which `update_signal` wraps, and which `chat_tools.update_signal` calls for every REST,
  chat and MCP field edit). A demoting edit appends a `content_edited` audit row (gate
  `None`, before/after governance snapshots plus `content_hash`) **before** the signal is
  persisted, under the container lock, with the same failure rules as review transitions:
  no row, no write; a failed write after the row adds `content_edited_failed`.

### 2. MCP stamps the reviewer; the caller's name is a claim

- The MCP `update_signal` handler never forwards `args["actor"]` as the actor. It stamps
  `actor` server-side as `mcp`, or `mcp:<clientInfo.name>` when the session sent a client
  name at initialize. That name is itself self-reported, so it labels the channel, not a
  person.
- The caller's `actor` argument is preserved as `claimed_actor` on the audit row, documented
  everywhere as unverified data.
- The `ask_kb` sub-agent (an MCP tool that can call `update_signal` when
  `allow_mutations=true`) uses the same stamping and gate.
- The tool description says so: review actions over MCP are MCP-originated and `actor` is
  recorded as a claim.

### 3. MCP review is a capability the operator can switch off

- `MCP_REVIEW_ACTIONS: bool = True` in `app/config.py`. When false, MCP refuses any
  `review_action` with an error that points to the review UI and REST review endpoints;
  plain field edits over MCP and all REST/UI review keep working.
- The default stays `true` so existing Local-tier setups, where "a human directs Claude to
  confirm" is the intended flow, are unchanged.
- When the Remote tier is enabled (`MCP_PUBLIC_URL` set) and the flag is true, startup logs a
  WARNING. The documented recommendation for the Remote tier is `MCP_REVIEW_ACTIONS=false`.

### Out of scope

Deriving a **verified** reviewer identity (per-user MCP credentials, OAuth, signed review
tokens) is hosted-edition scope. The community edition records honestly what it knows
(channel and claim) and lets the operator turn the channel off.

## Consequences

- Instruction-grade authority now means "a human reviewed this exact text". Editing a
  confirmed decision puts it back in the review queue; re-confirming it binds the new text.
  Callers see `review_voided: true` and the audit row id in the `update_signal` response.
- Recall needs no change: `memory_recall` re-hydrates governance from the authoritative
  record, so a demoted signal is excluded from `authority="instruction"` results even while
  its vector metadata is stale.
- Audit readers (decision detail, memory inspector, constitution rationale, review UI)
  treat `action` as a free string, so `content_edited` and
  `content_edited_failed` render without changes. Existing rows without `claimed_actor` or
  `reviewed_content_hash` still load (both default to `None`).
- MCP-originated reviews are distinguishable from UI/REST reviews in the log, but the log
  can no longer show a person's name for them unless the caller claimed one.
- Operators of the Remote tier must decide on `MCP_REVIEW_ACTIONS`; the startup warning makes
  the trade-off visible rather than silent.
- Alternatives considered: removing review actions from MCP entirely (breaks the documented
  single-user flow); clearing `provenance_status` on demotion (rewrites history that the edit
  did not change, and conflicts with "demotion only removes authority"); normalizing
  whitespace before hashing (would let a meaningful edit hide behind formatting rules).

Related: [ADR-002](ADR-002-evidence-instruction-authority-gate.md),
[ADR-007](ADR-007-agent-mediated-intake.md), [ADR-008](ADR-008-remote-mcp-access.md).
