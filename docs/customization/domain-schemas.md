# Authoring Domain Schemas

> **Audience:** anyone adapting imi to a business — including agents drafting a schema from
> business context (see the `domain-config-advisor` skill in `.claude/skills/`) ·
> **Source of truth:** `app/model_schemas/domain_config.py` (the Pydantic model that
> validates every schema at startup) ·
> **Examples:** six shipped schemas in `config/domains/*.yaml`

The domain schema is one YAML file that shapes the whole system: what entity types the
extractor looks for, what the graph will accept, what relationships are valid, what patterns
the intelligence layer watches for, and what everything is called in the UI. It is the first
and usually the only thing you need to customize.

> ⚠️ **Two accuracy warnings before you start.**
> 1. The authoritative schema definition is the Pydantic model at
>    `app/model_schemas/domain_config.py`. A second, divergent model exists at
>    `app/models/domain/config.py` (legacy) — never write schemas against it. When in doubt,
>    trust the Pydantic model and the shipped YAML examples.
> 2. Validation runs **at process startup** and fails fast. A broken schema = app won't boot.
>    That's the designed feedback loop — use it.

## How the schema is consumed

| Section | Read by | Effect |
|---|---|---|
| `entities` (+ attributes) | `app/services/domain_prompt_builder.py` | builds the LLM extraction prompts |
| `entities[].ner_labels` / `ner_exclude` | `app/services/semantica_extraction.py:183` | maps NER labels → your types; suppresses false positives |
| `entities` + `relationships` | `app/services/graph/neo4j_schema.py`, `neo4j_graph.py:2071` | graph constraints, indexes, and write-time validation |
| `intelligence_patterns` | `app/services/pattern_detection_service.py` | natural-language context injected into LLM analysis |
| `extraction_priorities`, `success_metrics` | schema-validated only | accepted and validated, but not consumed by the community pipeline today — available to downstream extensions |
| `ui` | frontend via `GET /api/domain/config` | app name, nav, terminology — no rebuild needed |

Selection: `ACTIVE_DOMAIN=<file-stem>` in `.env`, restart. Unset ⇒ first YAML alphabetically
(set it explicitly). Runtime switching is intentionally disabled.

## Schema reference

Everything nests under a top-level `domain:` key.

```yaml
domain:
  id: my_domain          # REQUIRED. snake_case, ^[a-z][a-z0-9_]*$, must equal the file stem
  name: "My Domain"      # REQUIRED. human-readable
  version: "1.0.0"       # optional

  entities: {}                 # dict keyed by entity type id — see below
  intelligence_patterns: {}    # dict keyed by pattern id — see below
  extraction_priorities: {}    # source type -> {pattern, priority}
  success_metrics: []          # list — see below
  ui: {}                       # frontend labels — see below
```

### Entities

`entities` is a **dict keyed by the entity type id** (the key is the id used everywhere —
graph labels, APIs, extraction):

```yaml
entities:
  client:                          # <- the type id
    name: client                   # REQUIRED
    description: >-                # REQUIRED — the extractor reads this; write it for an LLM
      Client organizations the practice advises
    plural: clients                # REQUIRED
    label: Client                  # optional display name (falls back to name)
    plural_label: Clients
    icon: building-2               # optional Lucide icon name for the UI nav

    ner_labels: ["ORG", "ORGANIZATION"]   # NER labels that map to this type
    ner_exclude: ["GRI", "MSCI"]          # names to suppress (case-insensitive) —
                                          # use for standards/acronyms that NER
                                          # misreads as organizations

    attributes:
      - name: name                 # snake_case (validated)
        type: string               # string | number | date | datetime | boolean | enum
        required: true
      - name: engagement_status
        type: enum
        enum: ["prospect", "active", "paused", "completed"]   # REQUIRED for enum type
      - name: contract_value
        type: number
        unit: "USD"

    relationships:
      - type: has_engagements      # relationship name (becomes HAS_ENGAGEMENTS in Neo4j)
        target: engagement         # must be another entity key in this file
        cardinality: one_to_many   # one_to_one | one_to_many | many_to_one | many_to_many
        inverse_name: for_client   # OPTIONAL — see the symmetry rule below
```

**The inverse-name symmetry rule** (the most common authoring error): if you set
`inverse_name: for_client`, the `engagement` entity **must** declare a relationship named
`for_client` targeting `client` whose own `inverse_name` points back
(`validate_inverse_names`, `domain_config.py:300-356`). Violations fail startup. Simplest
policy: omit `inverse_name` unless you want the paired traversal.

Note: the shipped examples carry an `id:` field on each entity; the model ignores it — the
dict key is authoritative.

### Intelligence patterns

A **keyed dict** (not a list). These are natural-language instructions to the LLM analysis
layer — describe the situation to watch for; don't write rules:

```yaml
intelligence_patterns:
  renewal_risk:
    name: "Renewal Risk Detection"
    pattern_type: risk         # risk | opportunity | escalation | commitment | decision | insight
    priority: high             # high | medium | low
    triggers:
      - entity: client
        condition: dissatisfaction_mentioned
        weight: 0.8
    actions:
      - "Flag for follow-up"
```

### Extraction priorities & success metrics

```yaml
extraction_priorities:
  meetings:  { pattern: entity_extraction, priority: high }
  documents: { pattern: project_analysis,  priority: medium }

success_metrics:
  - name: client_retention
    type: percentage           # count | percentage | time | ratio | score
    target: 95
```

### UI block

Renames the product for your domain — no frontend rebuild:

```yaml
ui:
  app_name: "Practice Brain"
  entity_label: "Clients & Engagements"
  graph_label: "Practice Map"
  terminology: { entity: "record", signal: "commitment" }
  nav_groups:
    knowledge_base:
      label: "Practice"
      items:
        /entities: { label: "Clients & Engagements", description: "Manage clients" }
```

## Step-by-step: creating a new domain

1. **Copy the closest shipped example**: `cp config/domains/solo_consulting.yaml
   config/domains/my_domain.yaml`. Filename stem must equal `domain.id`.
2. **Model the 3–6 entity types that matter.** Fewer is better — every type dilutes extraction
   attention. Ask: "what nouns do I want a dashboard of?" Write `description` fields for the
   LLM that will read them.
3. **Add attributes sparingly** — extraction fills what it can; enums beat free strings for
   anything you'll filter on (status, stage, tier).
4. **Wire relationships**, omitting `inverse_name` unless you need the paired edge.
5. **Steer NER**: `ner_labels` (`PERSON`/`PER`, `ORG`/`ORGANIZATION` are the common ones) and
   `ner_exclude` for domain jargon that looks like org names.
6. **Add 2–5 intelligence patterns** for the risks/opportunities you'd want surfaced from
   calls.
7. **Validate by booting**: set `ACTIVE_DOMAIN=my_domain`, `docker compose restart app`, and
   watch the logs — schema errors raise immediately with the failing field. (Programmatic
   validation also exists via `DomainConfigCLI` in `app/cli/domain_config_cli.py`.)
8. **Test with a real document**: ingest a representative transcript, then check the delta
   report and `/explorer` — are the right entities and types coming out? Iterate on
   descriptions and `ner_exclude`.

## Switching domains on a live corpus

Switching `ACTIVE_DOMAIN` changes validation and extraction going forward; existing entities
keep their old types. Unknown types encountered at write time become **provisional** in the
type registry (`app/services/graph/type_registry.py`) rather than erroring — inspect them at
`/api/type-registry` and either add them to the schema (promote to canonical) or clean them
up. For a clean slate, `NEO4J_REBUILD_ON_STARTUP=true` (default) rebuilds the graph from the
file corpus using the current schema.
