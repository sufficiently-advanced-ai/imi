# Variant staging area

Drafts produced by `scripts/make_variant.py`. **The harness does not load this
directory** — `load_all_fixtures` globs `transcripts/*.json` non-recursively, so
nothing here affects a run or the gate until it is promoted.

A variant copies a base fixture's `gold` verbatim and perturbs only the
transcript with contextual noise (minimization, authority anchoring, social
pressure, time pressure) to test extraction robustness. See
`../SCHEMA.md` -> "Factorial variants" and `../../README.md`.

The committed `.json` files here are uncalibrated example drafts. To promote one
into the gated suite:

1. Review the injected prose and edit the transcript for realism.
2. Calibrate live and reconcile every FP (per `../SCHEMA.md`).
3. Move the `.json` into `../transcripts/`.
4. Rewrite the baseline: `python scripts/run_evals.py --task all --baseline`.
