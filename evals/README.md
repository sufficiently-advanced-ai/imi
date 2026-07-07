# Extraction-Quality Eval Suite

Measures the meeting-extraction value chain — entity extraction, relationship
extraction, signal/decision classification, and meeting finalization — against
frozen synthetic transcripts with hand-labeled gold. Built to tune the prompts
and workflows; fixture + prompt + rubric are treated as a unit, fixtures are
immutable, rubrics use MUST/SHOULD checklists, and automated precision/recall
scoring runs on top.

## Layout

```text
evals/
├── fixtures/
│   ├── SCHEMA.md            # fixture format + authoring/calibration guide
│   ├── transcripts/         # frozen synthetic fixtures (001-008)
│   └── variants/            # staging for factorial-variant drafts (NOT loaded)
├── rubrics/                 # MUST/SHOULD checklists (summary task)
├── harness/                 # loader, matching, scoring, judge, consistency, report, runners/
├── baselines/baseline.json  # committed scores; runs gate against this
└── results/                 # timestamped run reports + history.jsonl trend log (gitignored)
scripts/run_evals.py         # CLI entry point
scripts/make_variant.py      # scaffold a factorial fixture variant
scripts/draft_fixture.py     # scaffold a fixture from an observed failure
scripts/check_evals.sh       # zero-API pre-merge gate (offline tests + immutability)
tests/test_eval_harness.py   # offline tests for the harness itself (no API)
```

## Running

Inside the dev container (evals/ and scripts/ are bind-mounted):

```bash
docker exec imi-dev python scripts/run_evals.py --task all
docker exec imi-dev python scripts/run_evals.py --task signals --fixture 004_decisions_vs_opinions
docker exec imi-dev python scripts/run_evals.py --task all --label my-prompt-variant
docker exec imi-dev python scripts/run_evals.py --compare evals/results/A.json evals/results/B.json
docker exec imi-dev python scripts/run_evals.py --task all --baseline   # rewrite baseline (deliberate!)
```

`--offline` re-scores recorded `replay` blobs without API calls (skips
fixtures that have none). `--runs N` repeats a live run N times and gates on the
mean, printing a mean ± spread table (see "Cost & variance"). Each live run
appends one line to the local trend log `results/history.jsonl`.

Before opening an eval- or prompt-touching PR, run the zero-API gate:

```bash
bash scripts/check_evals.sh        # offline harness tests + fixture immutability
```

Offline harness tests alone: `pytest tests/test_eval_harness.py` (runs anywhere,
no API key).

Exit code 1 = a gated metric regressed beyond tolerance (default 0.02) vs the
committed baseline, or a committed fixture was edited (immutability check).

## What each task measures

| Task | Exercises | Gated metrics |
|---|---|---|
| `entities` | `app/prompts/transcript_entity_extract.xml` via the EntityService prompt path | precision, recall, canonicalization_rate |
| `relationships` | `InferRelationshipsTool` (transcript + resolved entities → typed triples) | precision, recall |
| `signals` | `SignalPromoter.promote` with `app/prompts/signal_promote.xml` | precision, recall |
| `summary` | finalization via `app/prompts/meeting_finalize.xml`, graded by `rubrics/summary_rubric.md` (deterministic + Sonnet judge) | must_pass_rate |

Trap hits (forbidden entities/relationships/signals that got extracted) are
reported separately from plain FPs — they are the known production failure
modes: passing-mention companies, banter decisions, reporting relationships
downgraded to `collaborates_with`, `RELATED_TO` coercions.

### Consistency checks (non-gated)

`harness/consistency.py` adds cheap, deterministic, non-LLM checks that run
outside the model and catch reasoning/output inconsistencies the probabilistic
scorers miss (validation outside the LLM): decision language in a
signal typed as something softer, a relationship endpoint outside the entity
set, a capitalized name in a summary that never appears in the transcript. They
surface as a `consistency_violations` count per task (in the report, per-fixture
display, and trend log) and as a `consistency` list in run-report details. They
are **report-only** — deliberately kept out of `GATED_METRICS` until a check's
false-positive behavior has been calibrated against real runs.

## Tuning loop

1. Edit the production prompt (in `app/prompts/*.xml` — evals always read what
   ships; the run report records each prompt's sha256).
2. `run_evals.py --task <task> --label <variant>`
3. Compare against the baseline (printed automatically) or another run
   (`--compare`).
4. Keep or revert. When a change lands, rewrite the baseline with
   `--baseline` and commit it (reviewed like a snapshot).

## Factorial variants

Beyond the one-off traps, the suite supports *factorial variants*: a base
fixture with its gold copied verbatim and the transcript perturbed by
domain-general contextual noise (minimization, authority anchoring, social
pressure, time pressure). Same gold, different framing — if the extraction
shifts, that's an anchoring bug the aggregate hides.

```bash
# scaffold a draft into evals/fixtures/variants/ (staging — not loaded)
python scripts/make_variant.py 004_decisions_vs_opinions \
    --variation authority --anchor "move the whole platform to Kubernetes"
```

Drafts must be calibrated and promoted before they gate: review the injected
prose, run the variant live and reconcile FPs, move it into `transcripts/`, then
rewrite the baseline. See `fixtures/SCHEMA.md` ("Factorial variants").
**Read a per-fixture score drop on a variant vs its base as an anchoring bug,
not noise.**

## Cost & variance

A full `--task all` run is ~20 Haiku calls + ~4 Sonnet calls (finalization +
judge) — roughly $0.10–0.30. Extraction runs at the production temperatures
(0.1–0.3), so scores vary run to run; differences inside the 0.02 gate
tolerance are noise. For decisions that matter (baseline rewrites,
keep-or-revert calls on a prompt change), pass `--runs N` (e.g. `--runs 3`) to
repeat the run and print a mean ± spread table; the gate then evaluates the
mean. Look at the spread rather than trusting a single run.

## Failure-to-regression flywheel

Every extraction failure a human catches in production should become a permanent
fixture, so the suite compounds into a real failure library (Nate: "month six
the suite has hundreds of real failure cases"). The raw material already lives in
the product — decisions rejected in the `/decisions` Review tab, signals
corrected via `update_signal`/`delete_signal`.

The loop:

1. `scripts/draft_fixture.py --transcript <path|-> --id <new_id> [--decision-id <id>]`
   emits a schema-correct skeleton into `fixtures/variants/` (staging).
2. Label gold — and encode the specific failure you saw as a `forbidden_*` trap.
3. Calibrate live and reconcile every FP (per `fixtures/SCHEMA.md`).
4. Move into `transcripts/`, then rewrite the baseline (`--baseline`).

## Prompt-extraction pairing rule

When inline prompts are extracted to `app/prompts/*.xml`, land each extraction
with (a) coverage in an eval runner — *new prompt chain → new eval* — and (b) its
known failure modes written as explicit **MUST-NOT** constraints in the XML,
mirrored as `forbidden_*` traps in a fixture. Prompt constraints and eval traps
move in lockstep.

## Authoring fixtures

See `fixtures/SCHEMA.md`. Short version: synthetic only, production speaker
format (`**Full Name**:`), at least one trap per labeled task, and calibrate
gold against a live run before the fixture enters the baseline — every
reported FP is either a genuine model failure (leave it) or an under-label
(add `required: false` gold).
