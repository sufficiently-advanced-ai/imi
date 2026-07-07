#!/usr/bin/env python3
"""Eval runner for the meeting-extraction value chain.

Runs the production prompts over frozen synthetic fixtures and scores them
against gold labels. See evals/README.md and evals/fixtures/SCHEMA.md.

Usage
-----
    python scripts/run_evals.py --task all
    python scripts/run_evals.py --task signals --fixture 004_decisions_vs_opinions
    python scripts/run_evals.py --task all --offline          # replay-only, no API
    python scripts/run_evals.py --task all --label my-variant
    python scripts/run_evals.py --task all --baseline         # rewrite committed baseline
    python scripts/run_evals.py --compare evals/results/A.json evals/results/B.json

Inside the dev container:
    docker exec imi-dev python scripts/run_evals.py --task all

Exit codes
----------
    0 — ran, no gated-metric regression vs evals/baselines/baseline.json
    1 — regression beyond tolerance, fixture immutability violation, or error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# sys.path bootstrap — run directly or via pytest importlib import
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger(__name__)

_FIXTURES_DIR = Path(_ROOT) / "evals" / "fixtures" / "transcripts"
_RESULTS_DIR = Path(_ROOT) / "evals" / "results"
_BASELINE_PATH = Path(_ROOT) / "evals" / "baselines" / "baseline.json"

ALL_TASKS = ("entities", "relationships", "signals", "summary", "profiles")


def _get_runner(task: str):
    """Lazily import the runner for a task (runners import app services)."""
    if task == "entities":
        from evals.harness.runners.entities import EntitiesRunner

        return EntitiesRunner()
    if task == "signals":
        from evals.harness.runners.signals import SignalsRunner

        return SignalsRunner()
    if task == "summary":
        from evals.harness.runners.summary import SummaryRunner

        return SummaryRunner()
    if task == "relationships":
        from evals.harness.runners.relationships import RelationshipsRunner

        return RelationshipsRunner()
    if task == "profiles":
        from evals.harness.runners.profiles import ProfilesRunner

        return ProfilesRunner()
    raise ValueError(f"Unknown task: {task}")


async def run_tasks(
    tasks: list[str],
    fixtures: list[dict],
    offline: bool,
    client=None,
) -> tuple[dict, dict]:
    """Run each task over each labeled fixture. Returns (task_results, prompt_shas)."""
    from app.services.prompt_loader import prompt_sha
    from evals.harness import scoring
    from evals.harness.loader import labeled_for

    if client is None and not offline:
        from app.services.claude_client import get_claude_client

        client = get_claude_client()

    task_results: dict = {}
    prompt_shas: dict = {}

    for task in tasks:
        runner = _get_runner(task)
        prompt_shas[task] = (
            prompt_sha(runner.prompt_name) if runner.prompt_name else None
        )

        per_fixture: dict = {}
        details: dict = {}
        for fixture in fixtures:
            if not labeled_for(fixture, task):
                continue
            result = await runner.run(fixture, client, offline)
            if result.skipped:
                per_fixture[fixture["id"]] = {
                    "skipped": True,
                    "skip_reason": result.skip_reason,
                }
                continue
            per_fixture[fixture["id"]] = result.scores
            details[fixture["id"]] = result.details

        scored = {fid: s for fid, s in per_fixture.items() if not s.get("skipped")}
        if task in ("summary", "profiles"):
            if per_fixture and not scored and not offline:
                # Every labeled fixture skipped on a LIVE run = a broken run
                # (API/judge errors), not a pass. Force a failing aggregate so
                # baseline gating can't be silently bypassed. (Offline skips are
                # expected when no replay is recorded — left as an empty micro.)
                micro = {"must_pass_rate": 0.0}
            else:
                micro = _aggregate_summary(scored)
        else:
            micro = scoring.aggregate_micro(scored) if scored else {}
        task_results[task] = {
            "micro": micro,
            "per_fixture": per_fixture,
            "details": details,
        }

    return task_results, prompt_shas


def _aggregate_summary(scored: dict) -> dict:
    if not scored:
        return {}
    n = len(scored)
    out = {
        "must_pass_rate": sum(s.get("must_pass_rate", 0.0) for s in scored.values())
        / n,
    }
    should = [s["should_pass_rate"] for s in scored.values() if "should_pass_rate" in s]
    if should:
        out["should_pass_rate"] = sum(should) / len(should)
    if any("consistency_violations" in s for s in scored.values()):
        out["consistency_violations"] = sum(
            s.get("consistency_violations", 0) for s in scored.values()
        )
    if any("attribution_violations" in s for s in scored.values()):
        out["attribution_violations"] = sum(
            s.get("attribution_violations", 0) for s in scored.values()
        )
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="Run extraction-quality evals")
    parser.add_argument("--task", default="all", help=f"one of {ALL_TASKS} or 'all'")
    parser.add_argument("--fixture", metavar="ID", help="run a single fixture by id")
    parser.add_argument(
        "--offline", action="store_true", help="replay-only; no API calls"
    )
    parser.add_argument("--label", help="label recorded in the run report filename")
    parser.add_argument(
        "--baseline", action="store_true", help="rewrite evals/baselines/baseline.json"
    )
    parser.add_argument(
        "--tolerance", type=float, default=None, help="gate tolerance (default 0.02)"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="repeat the run N times and gate on the mean (measures variance)",
    )
    parser.add_argument(
        "--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="diff two run reports"
    )
    args = parser.parse_args(argv)

    if args.tolerance is not None and args.tolerance < 0:
        print("ERROR: --tolerance must be non-negative", file=sys.stderr)
        return 1
    if args.runs < 1:
        print("ERROR: --runs must be >= 1", file=sys.stderr)
        return 1
    if args.runs > 1 and args.baseline:
        print(
            "ERROR: --baseline requires a single deterministic run (drop --runs)",
            file=sys.stderr,
        )
        return 1

    from evals.harness import report as report_mod

    if args.compare:
        try:
            a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
            b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not read comparison report: {exc}", file=sys.stderr)
            return 1
        report_mod.print_comparison(a, b)
        return 0

    tasks: list[str] = list(ALL_TASKS) if args.task == "all" else [args.task]
    unknown = set(tasks) - set(ALL_TASKS)
    if unknown:
        print(f"ERROR: unknown task(s): {sorted(unknown)}", file=sys.stderr)
        return 1

    from evals.harness.loader import load_all_fixtures

    try:
        fixtures = load_all_fixtures(_FIXTURES_DIR)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.fixture:
        fixtures = [f for f in fixtures if f["id"] == args.fixture]
        if not fixtures:
            print(f"ERROR: fixture not found: {args.fixture}", file=sys.stderr)
            return 1

    try:
        baseline = report_mod.load_baseline(_BASELINE_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read baseline {_BASELINE_PATH}: {exc}", file=sys.stderr)
        return 1

    violations = report_mod.check_fixture_immutability(baseline, fixtures)
    if violations:
        for v in violations:
            print(f"ERROR: {v}", file=sys.stderr)
        return 1

    print(
        f"Loaded {len(fixtures)} fixtures; tasks: {', '.join(tasks)}"
        f"{' (offline)' if args.offline else ''}"
    )

    runs = args.runs
    if runs > 1 and args.offline:
        # Offline is deterministic replay; repeating it adds no variance signal.
        print("Note: --offline is deterministic; ignoring --runs > 1", file=sys.stderr)
        runs = 1

    from app.config import settings

    models = {
        "extract": settings.CLAUDE_HAIKU_MODEL,
        "judge": settings.CLAUDE_SONNET_MODEL,
    }

    reports: list[dict] = []
    for i in range(runs):
        if runs > 1:
            print(f"\n--- run {i + 1}/{runs} ---")
        task_results, prompt_shas = asyncio.run(
            run_tasks(tasks, fixtures, args.offline)
        )
        report = report_mod.build_report(
            label=args.label,
            repo_root=_ROOT,
            task_results=task_results,
            prompt_shas=prompt_shas,
            fixtures=fixtures,
            models=models,
        )
        if runs > 1:
            report["run_id"] = f"{report['run_id']}_r{i + 1}"
        path = report_mod.write_report(report, _RESULTS_DIR)
        reports.append(report)
        if runs == 1:
            report_mod.print_report(report, baseline)
            print(f"\nReport written to {path}")

    # Gate (and trend) on the mean across runs when there is more than one.
    gate_report = report_mod.mean_report(reports) if runs > 1 else reports[0]
    if runs > 1:
        report_mod.print_runs_summary(reports, baseline)

    # One committed trend line per invocation (the aggregate). Offline scores
    # are not comparable to live runs, so they are kept out of the trend.
    if not args.offline:
        report_mod.append_history(gate_report, _RESULTS_DIR)

    report = gate_report

    if args.baseline:
        # A baseline must reflect a full, live run — refuse to commit a
        # snapshot from a task/fixture subset or an offline replay, which
        # would weaken all future regression gating.
        if args.fixture or args.task != "all" or args.offline:
            print(
                "ERROR: --baseline requires a full live run "
                "(no --fixture, --task must be 'all', no --offline)",
                file=sys.stderr,
            )
            return 1
        _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE_PATH.write_text(
            json.dumps(report_mod.to_baseline(report), indent=2), encoding="utf-8"
        )
        print(f"Baseline rewritten at {_BASELINE_PATH}")
        return 0

    tolerance = (
        args.tolerance if args.tolerance is not None else report_mod.DEFAULT_TOLERANCE
    )
    regressions = report_mod.diff_against_baseline(report, baseline, tolerance)
    if regressions:
        print("\nFAIL — gated metric regressions vs baseline:", file=sys.stderr)
        for r in regressions:
            print(
                f"  {r['task']}.{r['metric']}: {r['baseline']:.3f} -> "
                f"{r['current']:.3f} ({r['delta']:+.3f})",
                file=sys.stderr,
            )
        return 1
    if baseline:
        print("PASS — no gated-metric regression vs baseline")
    else:
        print("No baseline yet — run with --baseline to record one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
