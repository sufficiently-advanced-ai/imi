# Practice KB — Solo Consulting Demo Fixtures

Curated entity profile fixtures for the `solo_consulting` domain demo. Each file is a markdown document with YAML frontmatter that the graph loader reads on startup to populate the knowledge graph with rich, pre-profiled nodes and edges.

## Purpose

These profiles provide a complete, internally consistent set of entity nodes (clients, engagements, stakeholders, consultant) for the `solo_consulting` demo scenario. Entity IDs intentionally match the names referenced in the sample meeting transcripts (e.g. `stakeholder-jane`, `client-acme-corp`) so that signals extracted from those transcripts link to fully profiled nodes rather than bare stubs. This makes the graph immediately navigable and relationship-rich from first load.

## Structure

```text
practice_kb/
├── consultants/
│   └── practice.md            # The solopreneur advisory practice
├── clients/
│   ├── acme-corp.md           # Energy client — carbon baseline
│   ├── globex.md              # Finance client — CSRD readiness
│   └── initech.md             # Tech client — ESG ratings & net-zero
├── engagements/
│   ├── acme-carbon-baseline.md
│   ├── globex-csrd.md
│   └── initech-esg-ratings.md
└── stakeholders/
    ├── jane.md                # Head of Sustainability, Acme Corp (champion)
    ├── dani.md                # Procurement Manager, Acme Corp (gatekeeper)
    ├── reema.md               # COO, Acme Corp (economic buyer)
    ├── victor.md              # CFO, Acme Corp (skeptic, economic buyer)
    ├── omar.md                # Data & Analytics Lead, Acme Corp (influencer)
    ├── grace.md               # Facilities & Operations Manager, Acme Corp
    ├── ken.md                 # Plant Manager, Acme Corp (blocker)
    ├── laura.md               # General Counsel, Acme Corp (gatekeeper)
    ├── tomas.md               # Head of Investor Relations, Acme Corp
    ├── sofia.md               # VP Regulatory Affairs, Globex
    ├── marcus.md              # Sustainability Data Lead, Globex
    ├── priya.md               # Director of Investor Relations, Initech
    ├── tom.md                 # Head of Operations, Initech
    └── consultant.md          # Practice owner (as transcript participant)
```

### Acme Corp influence map

Acme is the "hero" client and carries a full influence map — the kind an external
consultant builds to navigate a buying decision. Beyond the day-to-day contacts (Jane,
Dani), it includes the executive sponsors who hold the budget (COO Reema, CFO Victor),
the data lead who quietly sways the CFO (Omar), the operational data owners (Grace, plant
manager Ken), the disclosure gatekeeper (General Counsel Laura), and the investor-relations
voice (Tomás). Stakeholders carry `stance`, `influence`, and `authority`, and are wired to
each other through `reports_to`/`manages` (the formal org chart) and
`influences`/`influenced_by` (informal power lines that don't follow the org chart).

## Entity IDs

| File | ID |
|------|----|
| `consultants/practice.md` | `consultant-practice` |
| `clients/acme-corp.md` | `client-acme-corp` |
| `clients/globex.md` | `client-globex` |
| `clients/initech.md` | `client-initech` |
| `engagements/acme-carbon-baseline.md` | `engagement-acme-carbon-baseline` |
| `engagements/globex-csrd.md` | `engagement-globex-csrd` |
| `engagements/initech-esg-ratings.md` | `engagement-initech-esg-ratings` |
| `stakeholders/jane.md` | `stakeholder-jane` |
| `stakeholders/dani.md` | `stakeholder-dani` |
| `stakeholders/reema.md` | `stakeholder-reema` |
| `stakeholders/victor.md` | `stakeholder-victor` |
| `stakeholders/omar.md` | `stakeholder-omar` |
| `stakeholders/grace.md` | `stakeholder-grace` |
| `stakeholders/ken.md` | `stakeholder-ken` |
| `stakeholders/laura.md` | `stakeholder-laura` |
| `stakeholders/tomas.md` | `stakeholder-tomas` |
| `stakeholders/sofia.md` | `stakeholder-sofia` |
| `stakeholders/marcus.md` | `stakeholder-marcus` |
| `stakeholders/priya.md` | `stakeholder-priya` |
| `stakeholders/tom.md` | `stakeholder-tom` |
| `stakeholders/consultant.md` | `stakeholder-consultant` |

## Loading into a Running Instance

Copy these files into the KB git repo of the target instance, preserving the subdirectory structure, then restart to trigger graph reload:

```bash
# From this repo root — assuming INSTANCE is your instance name
INSTANCE_REPO=/path/to/instance/repo

cp -r tests/fixtures/practice_kb/clients/     $INSTANCE_REPO/clients/
cp -r tests/fixtures/practice_kb/engagements/ $INSTANCE_REPO/engagements/
cp -r tests/fixtures/practice_kb/stakeholders/ $INSTANCE_REPO/stakeholders/
cp -r tests/fixtures/practice_kb/consultants/  $INSTANCE_REPO/consultants/

# Commit in the instance repo so the webhook/graph reload picks it up
cd $INSTANCE_REPO && git add . && git commit -m "chore: load solo_consulting practice KB profiles"
```

Then restart the instance container (or trigger a graph reload via the API) to ingest the new profiles.

## Frontmatter Schema

All files follow the `solo_consulting` domain schema defined in `config/domains/solo_consulting.yaml`. The graph loader extracts:

- `id` — unique entity identifier (`{type}-{slug}`)
- `type` — entity type (`client`, `engagement`, `stakeholder`, `consultant`)
- Attribute fields matching the type's schema (e.g. `industry`, `status`, `email`)
- Relationship fields as YAML lists of target entity IDs (e.g. `has_engagements`, `for_client`)

Relationship targets use **bare slugs** (e.g. `reema`, `acme-corp`); the loader prefixes
them with the target entity type.

Stakeholders additionally carry influence-mapping fields:

- `stance` — `champion` | `supporter` | `neutral` | `skeptic` | `blocker`
- `influence` — `high` | `medium` | `low` (informal organizational power)
- `authority` — `economic_buyer` | `decision_maker` | `influencer` | `gatekeeper` | `end_user`
- `reports_to` / `manages` — formal org-chart edges (stakeholder → stakeholder)
- `influences` / `influenced_by` — informal power edges (stakeholder → stakeholder)
