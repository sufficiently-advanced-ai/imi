# imi Documentation

Documentation for operating, configuring, and building on imi — written for both humans and
AI agents. Every architecture doc cites `file:line` so claims are verifiable against the code.

## Reading paths by role

**"I'm setting up an instance"** (operator — human, or an agent using the
[`imi-onboarding`](../.claude/skills/imi-onboarding/SKILL.md) skill)
1. [Onboarding](getting-started/onboarding.md) — zero to working instance, with verification at every step
2. [Configuration](getting-started/configuration.md) — every knob, and which of the four config systems owns it
3. [Git corpus](getting-started/git-corpus.md) — optional: back the corpus with a GitHub repo (do this before the corpus grows)
4. [Day-to-day usage](getting-started/daily-usage.md) — the feed → review → query loop, and light maintenance
5. [Domain Schemas](customization/domain-schemas.md) — fit the system to your business

**"I want to understand how it works"** (developer, evaluator)
1. [System Overview](architecture/overview.md) — the five-minute mental model + code map
2. [Ingestion Pipeline](architecture/ingestion-pipeline.md) — intake → classify → extract → graph
3. [Entities & Graph](architecture/entities-and-graph.md) — resolution, write-through, graph schema
4. [Signals & Governance](architecture/signals-and-governance.md) — the two-axis trust model (read before writing any code that touches signals)
5. [Memory & Vectors](architecture/memory.md) — governed recall, writeback, vector backends
6. [MCP & API](architecture/mcp-and-api.md) — tool catalog, REST surface, auth

**"I want to customize or extend it"**
1. [Customization Map](customization/README.md) — every extension point, tiered from config to code. **Start here**; most changes need no code
2. [Domain Schemas](customization/domain-schemas.md) — the deep guide to the highest-leverage file
3. The relevant architecture doc's "Customization points" table

**"I'm an agent"**
- Operating a running instance → [Agent Operating Guide](agents/README.md)
- Changing the code → [`CLAUDE.md`](../CLAUDE.md) (any coding agent; see also [`AGENTS.md`](../AGENTS.md))
- Skills: [`domain-config-advisor`](../.claude/skills/domain-config-advisor/SKILL.md) (design a domain schema from business context), [`imi-onboarding`](../.claude/skills/imi-onboarding/SKILL.md) (install + verify)

## Diagrams

Architecture diagrams live in [`diagrams/`](diagrams/) as Excalidraw sources (`.excalidraw`,
editable at [excalidraw.com](https://excalidraw.com)) plus committed SVG exports embedded in
the docs. After editing a source: `node scripts/export_diagrams.mjs`.

## Design records

- [`adr/`](adr/) — Architecture Decision Records. [ADR-002](adr/ADR-002-evidence-instruction-authority-gate.md) (evidence/instruction authority gate) is load-bearing across the whole memory system
- [`prd/`](prd/) — product requirement docs for the memory-governance and decision-state systems
- [`mcp_tool_conventions.md`](mcp_tool_conventions.md) — the contract for adding/consuming MCP tools
- [`world-model-concept.md`](world-model-concept.md) — the concept behind the graph

## Accuracy notes

- The authoritative domain-schema definition is the Pydantic model
  `app/model_schemas/domain_config.py`; the quick reference at
  `config/domains/DOMAIN_SCHEMA.md` and the guide in `customization/domain-schemas.md` defer
  to it. Beware the divergent legacy model at `app/models/domain/config.py`.
- Docs in `getting-started/`, `architecture/`, `customization/`, and `agents/` were written
  against the code in July 2026. When a doc and the code disagree, the code wins — and a PR
  fixing the doc is welcome.
