# Eval Fixture Schema

One JSON file per synthetic meeting transcript, in `evals/fixtures/transcripts/`.
Each fixture is labeled for all four eval tasks (a task may be opted out by
setting its gold key to `null`).

**Fixtures are IMMUTABLE once committed and recorded in `evals/baselines/baseline.json`.**
The loader verifies a content hash; corrections create a successor file
(e.g. `004b_decisions_vs_opinions.json`), never edits.

All transcripts are synthetic — no real client data.

## Top-level shape

```jsonc
{
  "id": "004_decisions_vs_opinions",          // must match filename stem
  "version": 1,
  "description": "What this fixture stresses, and any traps it contains.",
  "meeting": {
    "title_context": "Weekly delivery sync",  // input context only — NOT the gold title
    "date": "2026-06-01",
    "participants": ["Elena Vasquez", "Marcus Webb"],
    "transcript": "**Elena Vasquez**: Morning everyone...\n**Marcus Webb**: ..."
    // Speaker lines MUST use the production "**Full Name**:" header format —
    // app/prompts/transcript_entity_extract.xml depends on it.
  },
  "gold": {
    "entities": [...],            // or null to opt out of the entities task
    "forbidden_entities": [...],
    "relationships": [...],       // or null to opt out
    "forbidden_relationships": [...],
    "signals": [...],             // or null to opt out
    "forbidden_signals": [...],
    "summary": {...},             // or null to opt out
    "profiles": {...}             // or null to opt out of the profiles task
  },
  "replay": {                     // OPTIONAL — recorded model output for offline runs
    "entities":      {"prompt_sha256": "…", "llm_response": "raw model text"},
    "signals":       null,
    "relationships": null,
    "summary":       null,
    "profiles":      {"prompt_sha256": "…", "llm_response": "raw model text"}
  }
}
```

## Gold labels

### entities

```jsonc
{
  "canonical_id": "account-northwind",   // slug an ideal pipeline would produce
  "canonical_name": "NorthWind",
  "type": "account",                       // person | account | project | team
  "aliases": ["North Wind", "Northwind"],  // any of these = correct match
  "required": true                         // false ⇒ extracting it is fine but
                                           // missing it costs no recall
}
```

### forbidden_entities — explicit traps

Extracting one of these is a false positive AND a "trap hit" (reported
separately, because traps are the failure modes under iteration).

```jsonc
{ "name": "Salesforce", "type": "account",
  "reason": "passing tooling mention — not a client account" }
```

### relationships — typed triples

Subject/object are gold `canonical_id`s. Predicates use the canonical
vocabulary (see `evals/harness/matching.py::PREDICATE_MAP`, anchored to
`config/domains/consulting_firm.yaml`): `reports_to`, `managed_by`,
`works_on`, `member_of_team`, `collaborates_with`, `belongs_to_account`.

```jsonc
{ "subject": "person-barry-chen", "predicate": "reports_to",
  "object": "person-stephen-cole",
  "evidence": "I report to Stephen",       // human note, not used by scorer
  "required": true }
```

`forbidden_relationships` use the same shape plus `reason`.

### signals

Matching is by author-chosen keywords against the extracted signal content
(case-insensitive substring): every `keywords_all` term must appear, and at
least one `keywords_any` term (if the list is present and non-empty).

```jsonc
{ "gold_id": "sig-1",
  "type": "decision",                      // decision | action_item | key_point | insight
  "keywords_all": ["staging", "deploy"],
  "keywords_any": ["gate", "sign-off"],
  "owner": "Dana Okafor",                  // optional, action_items only
  "required": true }
```

`forbidden_signals`: keywords plus optional `type` (omit `type` to trap any
signal type) plus `reason`.

```jsonc
{ "type": "decision", "keywords_all": ["beard"],
  "reason": "personal banter must not become a decision" }
```

### summary

```jsonc
{
  "must_mention_keywords": [["staging", "gate"], ["NorthWind"]],
  //   each inner list is any-of; every inner list must be satisfied
  "must_not_mention_keywords": ["beard"],
  "title_max_words": 10,
  "title_must_contain_any": ["delivery", "sync", "NorthWind"]
}
```

### profiles

Exercises the entity narrative-profile prompts (`{type}_update.xml`). One
subject per fixture. The transcript should contain at least one co-participant
whose distinct activity is the misattribution **trap**, and the subject must
have at least one genuinely-grounded fact — so the eval distinguishes "correctly
grounded" from "leaked from a co-attendee".

```jsonc
{
  "entity_type": "person",            // person | project | team — selects {type}_update.xml
  "entity_id": "person-sarah-chen",   // the subject the profile is generated for
  "existing_profile": "",             // optional prior profile fed as {{existing_profile}}
  "grounded_facts": "## Recent Signals\n### Decisions\n- Sarah owns the staging-gate rollout (2026-05-12)",
  //   the AUTHORITATIVE block fed as {{grounded_facts}}: only the subject's own
  //   signals + typed relationships. A correct profile attributes from this, not
  //   from un-attributed transcript text.
  "must_mention_keywords": [["staging", "gate"]],   // subject's grounded facts; any-of per group
  "forbidden_attribution_keywords": ["latency", "mesh"],  // THE TRAP: co-participant activity terms
  //   that must NOT appear on this subject's profile (deterministic hit = misattribution)
  "forbidden_attributions": [          // judge-facing detail documenting the trap
    { "owner": "person-dana-okafor", "text": "the API latency investigation",
      "keywords_any": ["latency"],
      "reason": "Dana raised the latency item; Sarah only co-attended" }
  ]
}
```

## Replay blobs

`replay.<task>.llm_response` is the raw model output text recorded from a live
run. With `--offline`, runners score the replay instead of calling the API —
used by `tests/test_eval_harness.py` and for free scorer iteration.
`prompt_sha256` is the SHA-256 of the prompt template at recording time; a
mismatch means the prompt has changed since recording, and the runner skips
the replay with a warning (the recorded output no longer reflects the prompt).

## Authoring guidance

- 3–6 speakers, 30–80 transcript lines; realistic small talk is encouraged —
  it is exactly what the pipeline must learn to ignore.
- Every fixture should contain at least one trap per labeled task.
- **Calibrate gold before recording a baseline**: run the task live and review
  every reported FP. A legitimate extraction your labels missed gets added as
  gold with `required: false` (counts toward precision, no recall penalty) —
  otherwise precision measures your labeling, not the prompt.
- Reuse canonical_ids across fixtures to test cross-meeting consistency
  (002 and 006 deliberately share entities under different surface forms).

## Factorial variants

A *variant* tests robustness to contextual framing: take a base fixture, copy
its `gold` **verbatim**, and perturb ONLY the transcript with domain-general
contextual noise — minimization, authority anchoring, social pressure, or time
pressure. The premise (from Nate's "Your AI Agent Knows the Answer. It
Recommends the Opposite."): hold the gold constant and vary the framing; if the
extraction shifts under a context-only change, that is an anchoring bug the
aggregate score hides.

- Generate drafts with `scripts/make_variant.py <base_id> --variation
  {minimize,authority,social,time}` (see its `--help`). Drafts land in the
  staging dir `evals/fixtures/variants/`, which the harness does **not** load.
- Naming: `<base_id>__<variation>.json`; the `id` equals the filename stem.
- **Gold is copied verbatim** — same required signals, same `forbidden_*` traps.
  That is the whole point. Do not relabel gold for a variant.
- Promotion is deliberate: the gate is a *global aggregate* across every loaded
  fixture, so adding one means a full baseline rewrite. The flow is
  review prose → calibrate live (reconcile every FP, as above) → move the file
  into `transcripts/` → `run_evals.py --task all --baseline`.
- Interpretation: **a per-fixture score drop on a variant vs its base is an
  anchoring bug, not noise** — compare the variant's per-fixture scores to the
  base's, not the aggregate.
