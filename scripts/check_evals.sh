#!/usr/bin/env bash
# Zero-API pre-merge gate for the eval suite: offline harness tests + fixture
# immutability. No API key required, no model calls. Run before opening any
# eval- or prompt-touching PR — a block-merge-if-fail gate that costs $0.
#
#   bash scripts/check_evals.sh
#
# Runs inside the dev container by default (EVAL_CONTAINER, default imi-dev).
# Set EVAL_LOCAL=1 to run in the current Python environment instead.
set -euo pipefail

run() {
  if [[ "${EVAL_LOCAL:-0}" == "1" ]]; then
    "$@"
  else
    docker exec -i "${EVAL_CONTAINER:-imi-dev}" "$@"
  fi
}

# Inside the container "python" exists; on a bare host it may be python3 only.
PY_BIN=python
if [[ "${EVAL_LOCAL:-0}" == "1" ]] && ! command -v python >/dev/null 2>&1; then
  PY_BIN=python3
fi

echo "==> offline harness tests (tests/test_eval_harness.py)"
run "$PY_BIN" -m pytest tests/test_eval_harness.py -q

echo "==> fixture immutability vs committed baseline"
run "$PY_BIN" - <<'PY'
import sys
from evals.harness.loader import load_all_fixtures
from evals.harness.report import check_fixture_immutability, load_baseline

fixtures = load_all_fixtures("evals/fixtures/transcripts")
baseline = load_baseline("evals/baselines/baseline.json")
violations = check_fixture_immutability(baseline, fixtures)
if violations:
    for v in violations:
        print("IMMUTABILITY:", v, file=sys.stderr)
    sys.exit(1)
print(f"OK: {len(fixtures)} fixtures unchanged vs baseline")
PY

echo "All eval pre-merge checks passed."
