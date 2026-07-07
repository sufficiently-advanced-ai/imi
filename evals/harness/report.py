"""Run reports, console output, and baseline regression gating.

A run report is a JSON document; the committed baseline
(evals/baselines/baseline.json) is the same shape minus per-item details,
plus fixture content hashes for the immutability check.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

# Gated metrics per task: regression beyond tolerance vs baseline => exit 1.
GATED_METRICS: dict[str, list[str]] = {
    "entities": ["precision", "recall", "canonicalization_rate"],
    "relationships": ["precision", "recall"],
    "signals": ["precision", "recall"],
    "summary": ["must_pass_rate"],
    "profiles": ["must_pass_rate"],
}
DEFAULT_TOLERANCE = 0.02

# Non-gated metrics worth trending alongside the gated ones (failure-mode
# counters). Surfaced in history.jsonl so regressions in these show up early,
# even before they are promoted to GATED_METRICS.
HISTORY_EXTRA_METRICS = ("trap_hits", "consistency_violations", "attribution_violations")


def git_sha(repo_root: str | Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = out.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    # Fall back to CI/container-injected provenance: the documented run path
    # is inside the dev container, where a git work-tree is often absent.
    import os

    env_sha = os.environ.get("GIT_COMMIT") or os.environ.get("GITHUB_SHA")
    return (env_sha.strip()[:12] or None) if env_sha else None


def _safe_label(label: str | None) -> str | None:
    """Reduce a user-supplied label to a path-safe slug (no separators).

    The label flows into run_id and then into the report filename, so an
    unsanitized label like "../../etc/foo" could escape the results dir.
    """
    if not label:
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.")
    return slug or None


def build_report(
    label: str | None,
    repo_root: str | Path,
    task_results: dict,
    prompt_shas: dict,
    fixtures: list[dict],
    models: dict | None = None,
) -> dict:
    label = _safe_label(label)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    return {
        "run_id": f"{now}_{label}" if label else now,
        "label": label,
        "generated_at": now,
        "git_sha": git_sha(repo_root),
        "models": models or {},
        "prompt_shas": prompt_shas,
        "fixture_hashes": {f["id"]: f["_hash"] for f in fixtures},
        "tasks": task_results,
    }


def write_report(report: dict, results_dir: str | Path) -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def to_baseline(report: dict) -> dict:
    """Strip per-item details so the committed baseline stays reviewable."""
    baseline_tasks = {}
    for task, data in report["tasks"].items():
        baseline_tasks[task] = {
            "micro": data.get("micro", {}),
            "per_fixture": {
                fid: {
                    k: v
                    for k, v in scores.items()
                    if isinstance(v, (int, float)) or k == "skipped"
                }
                for fid, scores in (data.get("per_fixture") or {}).items()
            },
        }
    return {
        "generated_from_run": report["run_id"],
        "git_sha": report.get("git_sha"),
        "prompt_shas": report.get("prompt_shas", {}),
        "fixture_hashes": report.get("fixture_hashes", {}),
        "tasks": baseline_tasks,
    }


def load_baseline(path: str | Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check_fixture_immutability(
    baseline: dict | None, fixtures: list[dict]
) -> list[str]:
    """Return violation messages for fixtures whose content hash changed since
    they were recorded in the baseline."""
    if not baseline:
        return []
    recorded = baseline.get("fixture_hashes", {})
    violations = []
    for f in fixtures:
        old = recorded.get(f["id"])
        if old and old != f["_hash"]:
            violations.append(
                f"Fixture {f['id']} has been edited since the baseline was recorded "
                "(fixtures are immutable — create a successor file instead)"
            )
    return violations


def diff_against_baseline(
    report: dict, baseline: dict | None, tolerance: float = DEFAULT_TOLERANCE
) -> list[dict]:
    """Return regressions: gated metrics that dropped more than tolerance."""
    if not baseline:
        return []
    regressions = []
    for task, metrics in GATED_METRICS.items():
        new = (report["tasks"].get(task) or {}).get("micro") or {}
        old = (baseline["tasks"].get(task) or {}).get("micro") or {}
        for metric in metrics:
            if metric in new and metric in old:
                delta = new[metric] - old[metric]
                if delta < -tolerance:
                    regressions.append(
                        {
                            "task": task,
                            "metric": metric,
                            "baseline": old[metric],
                            "current": new[metric],
                            "delta": round(delta, 4),
                        }
                    )
    return regressions


def gated_metric_values(report: dict) -> dict:
    """{task: {metric: value}} for the gated metrics present in a report.

    Used for variance aggregation across repeated runs.
    """
    out: dict = {}
    for task, metrics in GATED_METRICS.items():
        micro = (report["tasks"].get(task) or {}).get("micro") or {}
        vals = {m: micro[m] for m in metrics if isinstance(micro.get(m), (int, float))}
        if vals:
            out[task] = vals
    return out


def history_metrics(report: dict) -> dict:
    """{task: {metric: value}} for the trend log: gated metrics plus the
    monitored failure-mode counters in HISTORY_EXTRA_METRICS."""
    out: dict = {}
    for task, data in report["tasks"].items():
        micro = (data or {}).get("micro") or {}
        vals = {}
        for metric in (*GATED_METRICS.get(task, []), *HISTORY_EXTRA_METRICS):
            if isinstance(micro.get(metric), (int, float)):
                vals[metric] = micro[metric]
        if vals:
            out[task] = vals
    return out


def append_history(report: dict, results_dir: str | Path) -> Path:
    """Append one summary line to the committed trend log (history.jsonl).

    Full run reports stay gitignored; this single line per run is committed so
    prompt/model changes leave a reviewable trend. Trend lines beat snapshots.
    """
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    line = {
        "run_id": report["run_id"],
        "generated_at": report.get("generated_at"),
        "git_sha": report.get("git_sha"),
        "label": report.get("label"),
        "prompt_shas": report.get("prompt_shas", {}),
        "models": report.get("models", {}),
        "metrics": history_metrics(report),
    }
    path = results_dir / "history.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")
    return path


def summarize_runs(reports: list[dict]) -> dict:
    """Aggregate gated metrics across repeated runs into mean/min/max/spread.

    Extraction runs at production temperatures, so single-run deltas inside the
    gate tolerance are noise — look at the spread before keep-or-revert calls.
    """
    agg: dict = {}
    for rep in reports:
        for task, vals in gated_metric_values(rep).items():
            for metric, value in vals.items():
                agg.setdefault(task, {}).setdefault(metric, []).append(value)
    summary: dict = {}
    for task, metrics in agg.items():
        summary[task] = {}
        for metric, values in metrics.items():
            summary[task][metric] = {
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "spread": max(values) - min(values),
                "n": len(values),
            }
    return summary


def mean_report(reports: list[dict]) -> dict:
    """A report whose gated micro metrics are the mean across runs.

    Used to gate a multi-run invocation against the baseline. Non-gated
    structure is taken from the final run; only numeric gated micro values are
    replaced with their means.
    """
    base = copy.deepcopy(reports[-1])
    summary = summarize_runs(reports)
    for task, metrics in summary.items():
        micro = (base["tasks"].get(task) or {}).get("micro")
        if not isinstance(micro, dict):
            continue
        for metric, stats in metrics.items():
            micro[metric] = stats["mean"]
    base["run_id"] = f"{base['run_id']}_mean{len(reports)}"
    return base


def print_runs_summary(reports: list[dict], baseline: dict | None = None) -> None:
    """Print a mean ± spread table over repeated runs of the gated metrics."""
    summary = summarize_runs(reports)
    n = len(reports)
    print(f"\n== VARIANCE OVER {n} RUNS (gated metrics) ==")
    base_col = "  baselineΔ" if baseline else ""
    print(
        f"  {'task.metric':<30} {'mean':>8} {'min':>8} {'max':>8} {'spread':>8}{base_col}"
    )
    for task in sorted(summary):
        for metric in sorted(summary[task]):
            s = summary[task][metric]
            delta = ""
            if baseline:
                base_micro = (
                    (baseline.get("tasks", {}).get(task) or {}).get("micro") or {}
                )
                if metric in base_micro:
                    delta = f"  {s['mean'] - base_micro[metric]:+.3f}"
            print(
                f"  {task + '.' + metric:<30} {s['mean']:>8.3f} {s['min']:>8.3f} "
                f"{s['max']:>8.3f} {s['spread']:>8.3f}{delta}"
            )


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_report(report: dict, baseline: dict | None = None) -> None:
    print()
    print(f"Run: {report['run_id']}  git: {report.get('git_sha') or '?'}")
    for task, data in report["tasks"].items():
        micro = data.get("micro") or {}
        per_fixture = data.get("per_fixture") or {}
        print()
        print(f"== {task.upper()} ==")
        if not micro and not per_fixture:
            print("  (no fixtures ran)")
            continue
        header = sorted(k for k, v in micro.items() if isinstance(v, (int, float)))
        base_micro = ((baseline or {}).get("tasks", {}).get(task) or {}).get(
            "micro"
        ) or {}
        for key in header:
            delta = ""
            if key in base_micro:
                d = micro[key] - base_micro[key]
                delta = f"  (baseline {_fmt(base_micro[key])}, Δ {d:+.3f})"
            print(f"  {key:<22} {_fmt(micro[key])}{delta}")
        for fid, scores in per_fixture.items():
            if scores.get("skipped"):
                print(f"    {fid:<36} SKIPPED ({scores.get('skip_reason', '?')})")
                continue
            parts = [
                f"{k}={_fmt(v)}"
                for k, v in scores.items()
                if isinstance(v, (int, float))
                and k
                in (
                    "precision",
                    "recall",
                    "canonicalization_rate",
                    "trap_hits",
                    "must_pass_rate",
                    "should_pass_rate",
                    "type_errors",
                    "consistency_violations",
                )
            ]
            print(f"    {fid:<36} {'  '.join(parts)}")


def print_comparison(report_a: dict, report_b: dict) -> None:
    """Side-by-side micro metrics for two run reports."""
    print()
    print(
        f"{'TASK/METRIC':<36} {report_a.get('label') or 'A':>12} {report_b.get('label') or 'B':>12} {'DELTA':>9}"
    )
    print("-" * 72)
    tasks = sorted(set(report_a["tasks"]) | set(report_b["tasks"]))
    for task in tasks:
        a = (report_a["tasks"].get(task) or {}).get("micro") or {}
        b = (report_b["tasks"].get(task) or {}).get("micro") or {}
        for metric in sorted(set(a) | set(b)):
            va, vb = a.get(metric), b.get(metric)
            if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
                continue
            print(
                f"{task + '.' + metric:<36} {_fmt(va):>12} {_fmt(vb):>12} {vb - va:>+9.3f}"
            )
