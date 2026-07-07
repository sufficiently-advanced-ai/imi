#!/usr/bin/env python3
"""
Full Temporal Demo E2E Test Suite — 42 questions across 8 temporal tool categories.

Uses the Meridian Partners synthetic knowledge base.
Sends questions to POST /api/chat/stream and evaluates:
1. Whether the agent discovers and uses the expected temporal tools
2. Whether the response is meaningful (not empty or error)
3. Tool invocation details and latency

Usage:
    python3 tests/manual/test_temporal_demo_full.py [--report]
"""

import json
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8084"
CHAT_URL = f"{BASE_URL}/api/chat/stream"
TIMEOUT = 120


# ══════════════════════════════════════════════════════════════════════
# Test Questions — 42 total, 5-6 per tool category
# ══════════════════════════════════════════════════════════════════════

TEST_CASES = [
    # ── entity_at_time (6) ────────────────────────────────────────
    {"id": "EAT-1", "category": "entity_at_time",
     "question": "What did we know about Priya Kapoor on January 15, 2026?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},
    {"id": "EAT-2", "category": "entity_at_time",
     "question": "What was the state of Project Atlas on February 10, 2026?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},
    {"id": "EAT-3", "category": "entity_at_time",
     "question": "What did we know about Kai Novak on February 15, 2026? Did he exist in our system at that time?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},
    {"id": "EAT-4", "category": "entity_at_time",
     "question": "What was Tom Halstead's role on March 1, 2026? Was he still with the firm?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},
    {"id": "EAT-5", "category": "entity_at_time",
     "question": "What did we know about the Vanguard Replatform project on January 30, 2026?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},
    {"id": "EAT-6", "category": "entity_at_time",
     "question": "What was Marcus Chen's role on February 1, 2026 versus March 10, 2026?",
     "expected_tools": ["entity_at_time", "get_entity_by_name"]},

    # ── what_changed (5) ──────────────────────────────────────────
    {"id": "WC-1", "category": "what_changed",
     "question": "What changed about Priya Kapoor since February 1, 2026?",
     "expected_tools": ["what_changed", "get_entity_by_name"]},
    {"id": "WC-2", "category": "what_changed",
     "question": "What changed about Project Atlas since January 6, 2026?",
     "expected_tools": ["what_changed", "get_entity_by_name"]},
    {"id": "WC-3", "category": "what_changed",
     "question": "What changed about Marcus Chen since February 15, 2026?",
     "expected_tools": ["what_changed", "get_entity_by_name"]},
    {"id": "WC-4", "category": "what_changed",
     "question": "What changed about Tom Halstead since January 1, 2026?",
     "expected_tools": ["what_changed", "get_entity_by_name"]},
    {"id": "WC-5", "category": "what_changed",
     "question": "What changed about Project Beacon since January 6, 2026?",
     "expected_tools": ["what_changed", "get_entity_by_name"]},

    # ── what_changed_between (5) ──────────────────────────────────
    {"id": "WCB-1", "category": "what_changed_between",
     "question": "What changed about Project Atlas between February 1 and February 28, 2026?",
     "expected_tools": ["what_changed_between", "what_changed", "get_entity_by_name"]},
    {"id": "WCB-2", "category": "what_changed_between",
     "question": "What changed about Elena Voss between January 6 and March 10, 2026?",
     "expected_tools": ["what_changed_between", "what_changed", "get_entity_by_name"]},
    {"id": "WCB-3", "category": "what_changed_between",
     "question": "What changed about the Beacon project between February 3 and March 14, 2026?",
     "expected_tools": ["what_changed_between", "what_changed", "get_entity_by_name"]},
    {"id": "WCB-4", "category": "what_changed_between",
     "question": "What changed about Sam Okoro between January 1 and March 1, 2026?",
     "expected_tools": ["what_changed_between", "what_changed", "get_entity_by_name"]},
    {"id": "WCB-5", "category": "what_changed_between",
     "question": "How did the Vanguard Group account situation change between January 27 and March 3, 2026?",
     "expected_tools": ["what_changed_between", "what_changed", "get_entity_by_name"]},

    # ── graph_as_of (5) ───────────────────────────────────────────
    {"id": "GAO-1", "category": "graph_as_of",
     "question": "Show me the knowledge graph around Project Atlas as of January 15, 2026. What entities were connected to it?",
     "expected_tools": ["graph_as_of", "get_entity_by_name"]},
    {"id": "GAO-2", "category": "graph_as_of",
     "question": "What was the graph around Tom Halstead as of February 15, 2026?",
     "expected_tools": ["graph_as_of", "get_entity_by_name"]},
    {"id": "GAO-3", "category": "graph_as_of",
     "question": "Show the graph around Marcus Chen as of March 15, 2026. How many entities are connected to him?",
     "expected_tools": ["graph_as_of", "get_entity_by_name"]},
    {"id": "GAO-4", "category": "graph_as_of",
     "question": "What was the graph around Polaris Health as of February 25, 2026?",
     "expected_tools": ["graph_as_of", "get_entity_by_name"]},
    {"id": "GAO-5", "category": "graph_as_of",
     "question": "Show the graph around the Beacon Squad team as of March 1, 2026.",
     "expected_tools": ["graph_as_of", "get_entity_by_name"]},

    # ── find_contradictions (6) ───────────────────────────────────
    {"id": "FC-1", "category": "find_contradictions",
     "question": "Are there any contradicting signals about Project Atlas?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},
    {"id": "FC-2", "category": "find_contradictions",
     "question": "Are there contradictions about the Vanguard Replatform project? Any conflicting promises?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},
    {"id": "FC-3", "category": "find_contradictions",
     "question": "Are there contradicting signals about the Beacon project budget?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},
    {"id": "FC-4", "category": "find_contradictions",
     "question": "Are there contradictions about Nadia Reeves? Any conflicting information about her plans?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},
    {"id": "FC-5", "category": "find_contradictions",
     "question": "Are there contradictions about Derek Osman's performance? Has the assessment changed over time?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},
    {"id": "FC-6", "category": "find_contradictions",
     "question": "Are there any conflicting signals about Tom Halstead's situation at the firm?",
     "expected_tools": ["find_contradictions", "get_entity_by_name"]},

    # ── temporal_blast_radius (5) ─────────────────────────────────
    {"id": "TBR-1", "category": "temporal_blast_radius",
     "question": "What is the blast radius of Tom Halstead's departure? What entities were affected when he left on February 28?",
     "expected_tools": ["temporal_blast_radius", "get_entity_by_name"]},
    {"id": "TBR-2", "category": "temporal_blast_radius",
     "question": "What is the blast radius around Nadia Reeves as of March 5, 2026? What depends on her?",
     "expected_tools": ["temporal_blast_radius", "get_entity_by_name"]},
    {"id": "TBR-3", "category": "temporal_blast_radius",
     "question": "What entities are connected to Priya Kapoor as of March 10? Show me her impact radius.",
     "expected_tools": ["temporal_blast_radius", "get_entity_by_name"]},
    {"id": "TBR-4", "category": "temporal_blast_radius",
     "question": "What is the blast radius of Project Atlas as of February 21, the crisis point?",
     "expected_tools": ["temporal_blast_radius", "get_entity_by_name"]},
    {"id": "TBR-5", "category": "temporal_blast_radius",
     "question": "What is the blast radius around the Beacon project as of March 14 when it was paused?",
     "expected_tools": ["temporal_blast_radius", "get_entity_by_name"]},

    # ── active_relationships_at_time (5) ──────────────────────────
    {"id": "ART-1", "category": "active_relationships_at_time",
     "question": "What relationships did Priya Kapoor have on January 15, 2026?",
     "expected_tools": ["active_relationships_at_time", "get_entity_by_name"]},
    {"id": "ART-2", "category": "active_relationships_at_time",
     "question": "What relationships did Priya Kapoor have on March 1, 2026? How did they change?",
     "expected_tools": ["active_relationships_at_time", "get_entity_by_name"]},
    {"id": "ART-3", "category": "active_relationships_at_time",
     "question": "What relationships did Elena Voss have on March 10, 2026?",
     "expected_tools": ["active_relationships_at_time", "get_entity_by_name"]},
    {"id": "ART-4", "category": "active_relationships_at_time",
     "question": "What relationships did Marcus Chen have on January 10 versus March 15? How did his role expand?",
     "expected_tools": ["active_relationships_at_time", "get_entity_by_name"]},
    {"id": "ART-5", "category": "active_relationships_at_time",
     "question": "What relationships did the Vanguard Group account have on February 1 versus March 5?",
     "expected_tools": ["active_relationships_at_time", "get_entity_by_name"]},

    # ── get_entity_provenance (5) ─────────────────────────────────
    {"id": "GEP-1", "category": "get_entity_provenance",
     "question": "Where did information about Kai Novak come from? What are the original sources?",
     "expected_tools": ["get_entity_provenance", "get_entity_by_name"]},
    {"id": "GEP-2", "category": "get_entity_provenance",
     "question": "What is the provenance trail for the Polaris Onboarding project? When was it first mentioned?",
     "expected_tools": ["get_entity_provenance", "get_entity_by_name"]},
    {"id": "GEP-3", "category": "get_entity_provenance",
     "question": "Where did information about Jess Nolan come from? What meetings mention her?",
     "expected_tools": ["get_entity_provenance", "get_entity_by_name"]},
    {"id": "GEP-4", "category": "get_entity_provenance",
     "question": "What is the provenance of information about Derek Osman? How has our understanding of him evolved?",
     "expected_tools": ["get_entity_provenance", "get_entity_by_name"]},
    {"id": "GEP-5", "category": "get_entity_provenance",
     "question": "Where did the information about the Atlas project come from? Trace its sources.",
     "expected_tools": ["get_entity_provenance", "get_entity_by_name"]},
]


# ══════════════════════════════════════════════════════════════════════
# Test Runner
# ══════════════════════════════════════════════════════════════════════


def send_chat_question(question: str) -> dict:
    """Send a question to the chat API and collect SSE events."""
    result = {
        "tools_used": [],
        "tool_details": [],
        "answer": "",
        "error": None,
        "duration_s": 0,
    }
    start = time.time()

    try:
        with httpx.stream("POST", CHAT_URL, json={"query": question}, timeout=TIMEOUT) as response:
            if response.status_code != 200:
                result["error"] = f"HTTP {response.status_code}"
                return result

            buffer = ""
            for chunk in response.iter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")
                    if etype == "tool_start":
                        tool_name = event.get("tool_name", "")
                        result["tools_used"].append(tool_name)
                        result["tool_details"].append({
                            "tool": tool_name,
                            "args": event.get("tool_args", {}),
                        })
                    elif etype == "claude_response":
                        result["answer"] += event.get("content", "")
                    elif etype == "workflow_complete":
                        answer = event.get("result", {}).get("answer", "")
                        if answer and not result["answer"]:
                            result["answer"] = answer
                    elif etype == "workflow_failed":
                        result["error"] = event.get("error", "Unknown error")

    except httpx.TimeoutException:
        result["error"] = "Timeout"
    except Exception as e:
        result["error"] = str(e)

    result["duration_s"] = round(time.time() - start, 1)
    return result


def evaluate_result(test_case: dict, result: dict) -> dict:
    """Evaluate whether the expected temporal tool was used."""
    tools_used = set(result["tools_used"])
    expected = set(test_case["expected_tools"])

    temporal_tools_used = {t for t in tools_used if any(exp in t for exp in expected)}
    any_temporal_tool = len(temporal_tools_used) > 0
    has_answer = len(result["answer"]) > 30
    has_error = result["error"] is not None

    passed = any_temporal_tool and has_answer and not has_error

    return {
        "passed": passed,
        "temporal_tools_used": sorted(temporal_tools_used),
        "all_tools_used": sorted(tools_used),
        "answer_length": len(result["answer"]),
        "has_error": has_error,
        "error": result["error"],
        "duration_s": result["duration_s"],
    }


def generate_report(results: list, output_dir: str):
    """Generate markdown and JSON reports."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # JSON report
    json_path = f"{output_dir}/temporal_demo_results.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Markdown report
    md_path = f"{output_dir}/temporal_demo_report.md"
    with open(md_path, "w") as f:
        total = len(results)
        passed = sum(1 for r in results if r["evaluation"]["passed"])
        failed = total - passed

        f.write("# Temporal Knowledge Graph — E2E Demo Report\n\n")
        f.write(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Dataset**: Meridian Partners Q1 2026 (synthetic)\n")
        f.write(f"**Results**: {passed}/{total} passed ({passed/total*100:.0f}%)\n\n")

        # Per-category summary
        categories = {}
        for r in results:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = {"passed": 0, "failed": 0, "total_time": 0}
            if r["evaluation"]["passed"]:
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1
            categories[cat]["total_time"] += r["evaluation"]["duration_s"]

        f.write("## Results by Tool Category\n\n")
        f.write("| Category | Passed | Failed | Avg Latency |\n")
        f.write("|----------|--------|--------|-------------|\n")
        for cat, stats in sorted(categories.items()):
            total_cat = stats["passed"] + stats["failed"]
            avg_time = stats["total_time"] / total_cat
            f.write(f"| `{cat}` | {stats['passed']}/{total_cat} | {stats['failed']} | {avg_time:.1f}s |\n")

        f.write("\n## Detailed Results\n\n")
        for r in results:
            status = "PASS" if r["evaluation"]["passed"] else "FAIL"
            f.write(f"### [{status}] {r['id']} — {r['category']}\n\n")
            f.write(f"**Question**: {r['question']}\n\n")
            f.write(f"**Tools used**: {', '.join(r['evaluation']['all_tools_used'])}\n\n")
            f.write(f"**Temporal tools hit**: {', '.join(r['evaluation']['temporal_tools_used'])}\n\n")
            f.write(f"**Duration**: {r['evaluation']['duration_s']}s | **Answer length**: {r['evaluation']['answer_length']} chars\n\n")
            if r["evaluation"]["has_error"]:
                f.write(f"**Error**: {r['evaluation']['error']}\n\n")
            # Answer snippet
            answer = r.get("answer", "")
            if answer:
                snippet = answer[:500].replace("\n", "\n> ")
                f.write(f"**Answer preview**:\n> {snippet}\n\n")
            f.write("---\n\n")

    print(f"\n  Reports written to:")
    print(f"    JSON: {json_path}")
    print(f"    Markdown: {md_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Generate full report files")
    parser.add_argument("--category", type=str, help="Run only a specific category")
    args = parser.parse_args()

    print("=" * 70)
    print("  Temporal Knowledge Graph — Full E2E Demo Test Suite")
    print("  Dataset: Meridian Partners Q1 2026 (synthetic)")
    print(f"  Questions: {len(TEST_CASES)}")
    print("=" * 70)

    # Health check
    try:
        r = httpx.get(f"{BASE_URL}/api/entities/list?size=1", timeout=10)
        if r.status_code == 200:
            print(f"\n  API health: OK (status {r.status_code})")
        else:
            print(f"\n  WARNING: API returned {r.status_code}")
    except Exception as e:
        print(f"\n  ERROR: Cannot reach {BASE_URL}: {e}")
        sys.exit(1)

    cases = TEST_CASES
    if args.category:
        cases = [tc for tc in TEST_CASES if tc["category"] == args.category]
        print(f"  Filtered to category: {args.category} ({len(cases)} questions)")

    results = []
    passed = 0
    failed = 0

    for i, tc in enumerate(cases, 1):
        print(f"\n{'─' * 70}")
        print(f"  [{tc['id']}] ({i}/{len(cases)}) {tc['category']}")
        print(f"  Q: {tc['question'][:75]}...")
        print(f"  Sending...", end=" ", flush=True)

        result = send_chat_question(tc["question"])
        evaluation = evaluate_result(tc, result)

        status = "PASS" if evaluation["passed"] else "FAIL"
        if evaluation["passed"]:
            passed += 1
        else:
            failed += 1

        print(f"{status} ({evaluation['duration_s']}s)")
        print(f"  Tools: {evaluation['all_tools_used']}")
        if evaluation["has_error"]:
            print(f"  ERROR: {evaluation['error']}")
        if result["answer"]:
            print(f"  Answer: {result['answer'][:120].replace(chr(10), ' ')}...")

        results.append({
            "id": tc["id"],
            "category": tc["category"],
            "question": tc["question"],
            "expected_tools": tc["expected_tools"],
            "answer": result["answer"],
            "evaluation": evaluation,
        })

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {passed} passed, {failed} failed out of {len(cases)} tests")

    # Per-category breakdown
    cats = {}
    for r in results:
        cat = r["category"]
        cats.setdefault(cat, []).append(r["evaluation"]["passed"])
    print(f"\n  By category:")
    for cat, passes in sorted(cats.items()):
        p = sum(passes)
        t = len(passes)
        print(f"    {cat:35s} {p}/{t}")

    print(f"{'=' * 70}")

    if args.report:
        generate_report(results, "tests/manual/reports")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
