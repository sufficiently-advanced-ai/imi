# Domain Schema — quick reference

The files in this directory define imi's type system: entity types, attributes,
relationships, NER steering, intelligence patterns, and UI labels. One schema is active per
process, selected by the `ACTIVE_DOMAIN` env var (file stem, e.g.
`ACTIVE_DOMAIN=consulting_firm`) and loaded at startup — switching requires a restart.

**Authoritative definition:** the Pydantic model `app/model_schemas/domain_config.py` —
every schema is validated against it at boot, and a malformed schema fails startup with the
offending field named. (A divergent legacy model exists at `app/models/domain/config.py`; do
not write schemas against it.)

**Full authoring guide** (format reference, worked examples, validation steps, common
errors): [`docs/customization/domain-schemas.md`](../../docs/customization/domain-schemas.md)

## Shape at a glance

```yaml
domain:
  id: my_domain            # required; snake_case; must equal the file stem
  name: "My Domain"        # required
  version: "1.0.0"

  entities:                # dict keyed by entity type id
    client:
      name: client         # required
      description: "..."   # required — read by the extraction LLM
      plural: clients      # required
      label: Client        # optional display name; icon: <lucide-name>
      ner_labels: ["ORG"]  # NER labels mapped to this type
      ner_exclude: []      # names to suppress (case-insensitive)
      attributes:
        - name: status     # snake_case
          type: enum       # string|number|date|datetime|boolean|enum
          enum: ["active", "paused"]   # required for enum type
      relationships:
        - type: has_engagements
          target: engagement           # must be another entity key
          cardinality: one_to_many     # one_to_one|one_to_many|many_to_one|many_to_many
          inverse_name: for_client     # optional — target MUST declare the
                                       # reciprocal or startup fails

  intelligence_patterns:   # dict keyed by pattern id
    renewal_risk:
      name: "Renewal Risk Detection"
      pattern_type: risk   # risk|opportunity|escalation|commitment|decision|insight
      priority: high       # high|medium|low
      triggers: [{ entity: client, condition: dissatisfaction_mentioned, weight: 0.8 }]
      actions: ["Flag for follow-up"]

  extraction_priorities:   # source type -> {pattern, priority}
    meetings: { pattern: entity_extraction, priority: high }

  success_metrics:         # list
    - { name: client_retention, type: percentage, target: 95 }
                           # type: count|percentage|time|ratio|score

  ui:                      # frontend labels — no rebuild needed
    app_name: "Practice Brain"
    entity_label: "Clients & Engagements"
    graph_label: "Practice Map"
    terminology: { entity: "record", signal: "commitment" }
    nav_groups: { ... }    # see solo_consulting.yaml for a full example
```

Notes that save debugging time:

- The **dict key** under `entities:` is the entity type id used everywhere (graph labels,
  APIs, extraction). Some shipped files also carry an `id:` field per entity — it is ignored.
- `inverse_name` symmetry is validated at load (`validate_inverse_names` in the model):
  target must exist, must declare the named inverse, and it must point back. Omit
  `inverse_name` unless you need the paired traversal.
- Validate a schema without booting the app:

```bash
python3 -c "
from pathlib import Path; import yaml
from app.model_schemas.domain_config import DomainConfiguration
data = yaml.safe_load(Path('config/domains/<id>.yaml').read_text())
cfg = DomainConfiguration(**data.get('domain', data))
print('OK:', cfg.id, '-', len(cfg.entities), 'entities')"
```
