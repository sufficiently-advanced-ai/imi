# ADR-005: Use-Case Packs — One Setup Path, Many Use Cases

## Status

Proposed (2026-10-01).

## Context

imi is deliberately general: the domain schema, lanes, inference routing and MCP surface
let one codebase serve a solo consultant, an agency, or a research firm tracking hundreds of
sources. That generality is the product's strength and its adoption problem. A prospect does
not see themselves in "a self-hosted knowledge engine"; they see themselves in "a freelance
consultant doing technical implementations" or "a climate advisory firm tracking how ideas
change across sources".

Every such deployment makes the same five decisions:

1. **Install** — identical for everyone (`imi-onboarding` skill).
2. **Ontology** — the domain schema (`domain-config-advisor` skill, `config/domains/*.yaml`).
3. **Lanes** — which sources are `record` vs `library` (`config/lanes.yaml`, ADR-003).
4. **Inbound** — how content arrives; increasingly, Claude reading a connector and writing to
   imi on a schedule (ADR-007).
5. **Use** — the skills that turn the graph into work: briefs, the constitution, "what changed".

Today only 1 and 2 are encoded in the repo. Lanes are absent from onboarding. Inbound is
limited to Zapier, the git webhook and Grain. The consumption skills that make imi valuable
in practice (pre-meeting brief, constitution review, memory wrap) live outside the repo in one
operator's personal skill set. A use case is currently tribal knowledge.

## Decision

### 1. A use case is a pack: a directory of configuration, not code

```
use-cases/<id>/
  pack.yaml          # manifest: name, version, audience, which files below apply
  README.md          # the setup guide a human reads — the "how-to"
  domain.yaml        # copied to config/domains/<id>.yaml (or names a shipped domain)
  lanes.yaml         # copied to config/lanes.yaml
  inbound/           # one recipe per input: connector, schedule, task prompt, source mapping
  skills/            # use-case skills (SKILL.md each)
  sample/            # a small synthetic corpus for the proof ingest (never real client data)
  proof.yaml         # questions the sample corpus must answer, with expected facts
```

A pack contains **no Python**. If a use case cannot be expressed as a pack, the gap is a
platform gap and gets its own ADR (ADR-006 is the first). Packs are the forcing function that
keeps imi's generality honest.

### 2. Setup is agent-managed

`imi-onboarding` takes an optional pack id. With one, it installs, copies `domain.yaml` and
`lanes.yaml`, sets `ACTIVE_DOMAIN`, restarts the backend (domain switching is restart-only),
walks the inbound recipes, installs the skills, ingests `sample/`, and runs `proof.yaml` —
reporting each proof question as pass/fail. "Set up imi as a climate advisory firm" becomes
one sentence to Claude.

Copied files are stamped with `pack: <id>@<version>` so a re-run of onboarding can tell an
untouched copy (safe to upgrade) from a locally edited one (show the diff, ask). Domain
loading reads `config/domains/` only (`app/core/domain_config/active_domain.py`), so copying
is the mechanism for the domain; lanes could instead point `LANES_CONFIG_PATH` at the pack,
but both use the same copy-and-stamp path for one upgrade story.

`proof.yaml` doubles as an acceptance test, modeled on `scripts/check_evals.sh`: proof
questions pass on thresholds, not exact strings, and run nightly and on changes to
`app/prompts/` or the entity resolver — not on every PR, since a proof ingest makes many
non-deterministic LLM calls. Proofs run with every optional feature off (no decision-model
endpoint, Local tier), which is the baseline every pack must meet.

### 3. Skills split into core and pack

- **Core consumption skills** — generic over any domain: brief before a meeting/engagement,
  constitution review, what-changed-since, memory wrap. These ship once, in the repo, built only
  on the documented MCP tools (`ask_kb`, `get_constitution`, `memory_recall`, `find_changes`,
  `memory_writeback`).
- **Pack skills** — use-case specific (e.g. "position shift report" for climate advisory).

Skill source of truth is the repo. Distribution is a generated Claude plugin (core + the
selected pack's skills), so a non-developer installs them in one step instead of copying
folders. Never maintain a second hand-edited copy outside the repo.

### 4. The guide is the pack's README, and every guide has the same five sections

Install → Ontology → Lanes → Inbound → Use. The use case supplies the vocabulary, examples and
proof questions; the spine never changes.

A guide documents **how one operator runs imi for that use case** — not a prescription to
reproduce it exactly. Optional infrastructure (a decision-model endpoint for lane and entity
judgment, remote access, authentication) is named where it applies along with what changes
without it, and acquiring it is the reader's call. Packs must work on a stock community
install with every optional feature off.

## Alternatives considered

- **Domain schemas alone (status quo).** Covers only step 2; the rest stays tribal.
- **Docs-only how-tos.** Drift from the code with no test; nothing an agent can execute.
- **A repo per use case.** Forks the platform; fixes stop flowing.

## Consequences

- New use cases cost a directory, not a release. Prospect-facing guides become cheap to make.
- The core skills become a maintained product surface with tests, not personal tooling.
- Nightly CI cost grows with each pack's proof ingest and needs an API key in CI.
- Inbound recipes depend on ADR-007 (the intake contract). Scheduled cloud recipes need the
  Remote tier (ADR-008); until then a pack's guide covers the Local and Relayed tiers.
- First packs: `freelance-implementation` (fits today's platform; extracted from the operator's
  own setup, with a synthetic sample corpus) and `climate-advisory` (blocked on ADR-006).
