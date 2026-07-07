---
name: domain-config-advisor
description: >-
  Read business context (a description, website copy, sample transcripts/documents, or an
  interview with the user) and design, draft, and validate an imi domain schema
  (config/domains/*.yaml). Use when the user wants to adapt imi to their business, asks
  "which domain should I use", wants new entity types, or extraction keeps producing the
  wrong entities.
---

# Domain Config Advisor

You are designing the type system for a knowledge engine. The output is one YAML file that
drives extraction, graph validation, LLM analysis patterns, and UI labels. Read
`docs/customization/domain-schemas.md` for the full format; the authoritative validator is
`app/model_schemas/domain_config.py` (beware the divergent legacy model at
`app/models/domain/config.py`).

## Step 1 — Gather business context

Use whatever is available, in preference order:

1. Documents the user provides (about page, service descriptions, a sample call transcript,
   an org chart).
2. If an imi instance is already running with data: `list_entities` per type and
   `GET /api/type-registry` — **provisional types are unmet demand**: they're what
   users/agents tried to create that the schema didn't cover.
3. A short interview. The five questions that matter:
   - Who do you sell to / serve, and what do you call them? (client? account? member? patient?)
   - What unit of work do you track? (project? engagement? campaign? case? deal?)
   - Which people matter and in what roles? (stakeholders? team? both?)
   - What recurring risks or opportunities should call transcripts surface?
   - What statuses/stages do you actually use today? (these become enums)

## Step 2 — Choose base: existing, extend, or new

Compare against the six shipped schemas (`config/domains/*.yaml`): consulting_firm, b2b_saas,
agency, solo_consulting, member_network, personal_crm. If one covers ~80%, recommend it (or a
copy with small edits) over a new schema. Say which and why.

## Step 3 — Design the entity model

Rules of thumb (justify deviations):

- **3–6 entity types.** Every type dilutes extraction attention. "Would the user want a
  dashboard listing all X?" — if no, it's an attribute or a signal, not an entity.
- Things people *say in meetings* need `ner_labels` (`PERSON`/`PER` for humans,
  `ORG`/`ORGANIZATION` for companies). Add `ner_exclude` for domain jargon that NER mistakes
  for organizations (standards bodies, acronyms, product names) — see
  `solo_consulting.yaml` for a real example.
- `description` fields are read by the extraction LLM — write them as instructions
  ("Client organizations the practice advises"), not documentation.
- Enums beat free strings for anything filterable (status, stage, tier, stance).
- Relationships: model the 2–4 traversals the user will actually ask about. Omit
  `inverse_name` unless a paired edge is needed — if set, the target entity MUST declare the
  reciprocal relationship or **startup fails** (`validate_inverse_names`).
- Add 2–5 `intelligence_patterns` (keyed dict; `pattern_type` ∈ risk/opportunity/escalation/
  commitment/decision/insight) written as natural-language descriptions of situations, not
  rules.
- Include a `ui:` block — renaming "Entities" to the user's own vocabulary is cheap and makes
  the product feel native.

## Step 4 — Draft and validate

1. Write `config/domains/<id>.yaml`. `id` must be snake_case and equal the file stem;
   `id`, `name` required at domain level; `name`, `description`, `plural` required per
   entity; `enum:` list required for enum attributes.
2. Validate mechanically before presenting:
   ```bash
   docker exec imi-dev python -c "
   from pathlib import Path
   import yaml
   from app.model_schemas.domain_config import DomainConfiguration
   data = yaml.safe_load(Path('config/domains/<id>.yaml').read_text())
   cfg = DomainConfiguration(**data.get('domain', data))
   print('OK:', cfg.id, '-', len(cfg.entities), 'entities')
   "
   ```
   (Outside docker, run the same with the repo on PYTHONPATH. A running instance also
   validates at boot — errors name the failing field.)
3. Activate: set `ACTIVE_DOMAIN=<id>` in `.env`, restart the app.

## Step 5 — Prove it with a real ingest

Take a genuine sample document/transcript from the user (or synthesize a realistic one from
their context), post it to `/api/ingest`, and review the delta report and extracted entities/
signals *with the user*. Iterate on `description` and `ner_exclude` until extraction looks
right — one round of this is worth more than any amount of schema theorizing.

## Deliverable

Present: (1) the recommendation (existing/extended/new + why), (2) the YAML, (3) validation
output, (4) results of the test ingest, (5) what was deliberately left out and the signal for
adding it later (watch `/api/type-registry` for provisional types).
