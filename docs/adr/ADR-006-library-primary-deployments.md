# ADR-006: Library-Primary Deployments — Per-Deployment Lane Policy

## Status

Proposed (2026-10-01). Extends ADR-003; changes no invariant of ADR-002 or ADR-003 §1.

## Context

ADR-003 split content into `record` (we were party to it) and `library` (third-party content
we watch), and constrained library hard, because in the deployment it was calibrated on,
library was noise: 83% of captures, fake person nodes, news facts stored as our decisions.

The constraints are hardcoded:

| Behaviour | Where |
|---|---|
| Library decays out of recall after 90/180/365 days by durability | `lane_admission._HORIZONS`, `memory_recall.py:314` |
| Library may only link to existing entities, never create | `ingest_orchestrator.py:1147` (`link_only`) |
| Library never infers entity relationships | `ingest_orchestrator.py:1117` |
| Recall defaults to the record lane | `memory_recall.py:69` |

For a different class of deployment these defaults invert the product. A climate advisory firm
tracking hundreds of sources needs to answer "how has the consensus on X shifted, who moved
first, and when" — and its sources are library by definition. Under ADR-003 as built, that
content decays out of recall within a year, cannot introduce the organizations and
technologies it is about, and is invisible to default recall.

The underlying primitive is right: a library **claim** is attributed (`attributed_to`), dated
(`as_of`), and supersedable (`valid_from`, `superseded_by`). That is exactly a record of how an
idea changes. What is wrong is that the policy around it is global.

## Decision

### 1. Lane policy becomes per-deployment configuration in `config/lanes.yaml`

```yaml
library:
  decay:
    enabled: true            # false: library never goes stale
    horizons_days: [90, 180, 365]
  entities:
    mode: link_only          # link_only | allowlist
    create_types: []         # with allowlist: entity types library may create
  infer_relationships: false
recall:
  default_lanes: [record]    # e.g. [record, library] for library-primary deployments
```

Defaults reproduce today's behaviour exactly; existing deployments see no change.

### 2. What stays fixed regardless of configuration

- Lane is server-assigned (ADR-003 §1).
- Library produces `claim` signals only — never `decision` or `action_item` (ADR-003 §3).
- Library is evidence-grade only; no instruction path (ADR-002).
- Library never feeds entity profiles as first-party fact; claims stay attributed.
- Decay is never deletion (ADR-003 §5).

### 3. Ideas are seeded entities; claims attach to them

The recommended pattern for "track how ideas change", expressible as config:

- The domain schema declares an idea-bearing type (e.g. `Position`, `Thesis`, `Technology`)
  and the operator seeds the watchlist.
- Library stays `link_only` for that type, so claims attach to the curated set of ideas instead
  of minting a node per phrasing — the failure ADR-003 measured.
- `create_types` is used only for types where new nodes are wanted and the entity admission
  gate is trusted (e.g. `Organization`).
- Event time is the claim's publication time (`as_of` → `occurred_at`, ADR-004), so backfilled
  archives produce a correct timeline.

### 4. Allowlist creation goes through entity admission

`create_types` does not bypass the resolver or the entity admission decision model. It removes
the blanket ban, not the gate.

### 5. Attribution resolves to an entity

Today `attributed_to` is a string in signal metadata — the document's authors, else its title
(`ingest_orchestrator._apply_lane_to_signals`). "Who moved first" cannot be answered from a
string. The claim's source is resolved through the entity resolver to a `Person` or
`Organization` and linked with an `ATTRIBUTED_TO` edge from the claim; the string stays as a
fallback when nothing resolves. Attribution targets are subject to the same `link_only` /
`create_types` policy as any other library entity, so a library-primary deployment that wants
new publishers as nodes lists `Organization` in `create_types`.

### 6. A claims timeline is a first-class query, not recall

The library-primary question — "how has the position on X changed, and who moved first" — is
a time-ordered read of claims about an entity, not a similarity search. A new MCP tool (verb
per `docs/mcp_tool_conventions.md`, e.g. `list_claims`) returns claims linked to an entity
within an `occurred_at` window, ordered by event time, each with its attribution and
supersession state, with `max_results`. It reads the graph, not the vector index, so it is
exact and unaffected by recall ranking.

### 7. Multi-lane recall keeps lanes separate

With `recall.default_lanes: [record, library]`, recall keeps today's shape: record results
ranked as the answer, library results ranked separately under `background`. Merging into one
ranking would need weights tuned for library-primary corpora (see Consequences); separate
lists are honest until then.

## Open questions

- **Claim-to-claim relations.** §6 gives a timeline of claims per idea; "source B reversed
  source A's position" is still not explicit. A `contradicts` / `revises` edge between claims
  may be needed; deferred until a pack's proof questions require it.
- **Per-source decay.** Should a high-value source (an IPCC report) be exempt while a news feed
  decays? Possibly a `sources:` override; deferred.

## Consequences

- Library-primary use cases (research firms, policy shops, analyst teams) become packs
  (ADR-005) rather than forks.
- `allowlist` reopens a controlled version of the entity-pollution risk; the admission gate and
  type allowlist are the mitigation, and graph-integrity metrics should be watched per
  deployment.
- Recall ranking was tuned with library as background; library-primary deployments may need
  their own ranking weights (out of scope here).
