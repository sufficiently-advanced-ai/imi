# AGENTS.md

Agent guidance for this repository lives in two places, split by what you're doing:

- **Changing the code** (features, fixes, refactors): read [`CLAUDE.md`](CLAUDE.md) —
  commands, invariants, known traps, conventions. It applies to any coding agent, not just
  Claude.
- **Operating a running imi instance** (installing, configuring, ingesting, querying over
  MCP): read [`docs/agents/README.md`](docs/agents/README.md).

Documentation index with reading paths by role: [`docs/README.md`](docs/README.md).

Reusable skills for agent frameworks that support them (Claude Code and compatible):

- `.claude/skills/domain-config-advisor/` — read business context, draft and validate a
  domain schema (`config/domains/*.yaml`).
- `.claude/skills/imi-onboarding/` — install, configure, and verify an instance end to end.
