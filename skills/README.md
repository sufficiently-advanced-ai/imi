# imi consumption skills

> **Audience:** people using a running imi instance from Claude, and contributors to these
> skills · **Decision record:** [ADR-005 §3](../docs/adr/ADR-005-use-case-packs.md)

`skills/core/` holds the skills that turn an imi knowledge base into work. They are generic
over any domain and use only documented MCP tools (catalog:
[`app/services/mcp_tool_definitions.py`](../app/services/mcp_tool_definitions.py)).

| Skill | Use it for |
|---|---|
| [`brief`](core/brief/SKILL.md) | before a meeting or a piece of engagement work |
| [`constitution-review`](core/constitution-review/SKILL.md) | the standing rules and commitments in force |
| [`what-changed`](core/what-changed/SKILL.md) | what changed since a date, about one subject or everything |
| [`memory-wrap`](core/memory-wrap/SKILL.md) | saving a session's decisions and facts as evidence pending review |

Use-case packs add their own skills under `use-cases/<pack>/skills/`.

## Why here and not `.claude/skills/`

`.claude/skills/` holds skills for working **on this repo** — `imi-onboarding`,
`domain-config-advisor` — and Claude Code loads them automatically in a checkout. The
consumption skills are for people **using an instance**, usually from another directory or a
cloud session, so they ship as a plugin instead. Keeping them out of `.claude/skills/` also
keeps them out of every contributor's session.

## Install

The repo is the only source of truth; the plugin is generated:

```bash
python scripts/build_plugin.py [--pack <pack id>] [--mcp-url http://localhost:8080/api/mcp/sse]
claude plugin marketplace add ./build/plugins/imi-marketplace
claude plugin install imi@imi-local
```

Skills appear namespaced as `imi:brief`, `imi:what-changed`, and so on. They expect an imi MCP
server in the session; pass `--mcp-url` to have the plugin declare one, or keep using the one
you already configured. After pulling changes, rebuild and run
`claude plugin marketplace update imi-local`. Never edit `build/` by hand.

## Conventions

- Name tools exactly as the server registers them, in backticks (`ask_kb`, `memory_recall`);
  `scripts/validate_packs.py` fails on a reference to a tool that does not exist.
- No personal names, clients, hostnames or paths. Examples use fictional organizations.
- Respect ADR-002: skills write evidence, never instructions, and never confirm memories on
  the user's behalf.
- Respect ADR-004: report when things happened, not when imi recorded them.
- Close the recall loop with `record_memory_usage`.
