#!/usr/bin/env python3
"""
Integration test: Temporal Knowledge Graph via Chat API — Issue #864.

Sends temporal questions to POST /api/chat/stream and evaluates:
1. Whether the agent discovers and uses temporal MCP tools
2. Whether the response is meaningful (not an error or empty)
3. Which tools were invoked for each question

Usage:
    python3 tests/manual/test_temporal_chat_integration.py
"""

import json
import sys
import time
import httpx

BASE_URL = "http://127.0.0.1:8084"
CHAT_URL = f"{BASE_URL}/api/chat/stream"
TIMEOUT = 120  # seconds per question

# ── Test questions designed to trigger temporal tools ──────────────────

TEST_CASES = [
    {
        "id": "T1",
        "question": "What do we know about Jordan Reyes? When was he first mentioned and how has our understanding of him changed over time?",
        "expected_tools": ["entity_at_time", "what_changed", "get_entity_provenance", "get_entity_by_name"],
        "description": "Should trigger entity lookup + temporal tools for provenance/change tracking",
    },
    {
        "id": "T2",
        "question": "Show me the knowledge graph around the EMI project as it looked a week ago. What entities were connected to it?",
        "expected_tools": ["graph_as_of", "entity_at_time"],
        "description": "Should trigger graph_as_of for historical subgraph reconstruction",
    },
    {
        "id": "T3",
        "question": "What changed about Dan Cowpie since March 1st 2026?",
        "expected_tools": ["what_changed"],
        "description": "Direct what_changed query with explicit date",
    },
    {
        "id": "T4",
        "question": "Are there any contradictory signals or conflicting information about the Proof of Concept project?",
        "expected_tools": ["find_contradictions"],
        "description": "Should trigger find_contradictions for the project entity",
    },
    {
        "id": "T5",
        "question": "If something changed about Barry Goldberg, what other entities would be affected? Show me the blast radius.",
        "expected_tools": ["temporal_blast_radius"],
        "description": "Should trigger temporal_blast_radius for impact analysis",
    },
    {
        "id": "T6",
        "question": "What relationships did the Consulting Team have active two weeks ago?",
        "expected_tools": ["active_relationships_at_time"],
        "description": "Should trigger active_relationships_at_time",
    },
    {
        "id": "T7",
        "question": "Where did the information about Melinda Fountain come from? What are the original sources?",
        "expected_tools": ["get_entity_provenance"],
        "description": "Should trigger get_entity_provenance for source attribution",
    },
]


def send_chat_question(question: str) -> dict:
    """Send a question to the chat API and collect SSE events."""
    result = {
        "tools_used": [],
        "tool_details": [],
        "answer": "",
        "error": None,
        "events": [],
        "duration_s": 0,
    }

    start = time.time()

    try:
        with httpx.stream(
            "POST",
            CHAT_URL,
            json={"query": question},
            timeout=TIMEOUT,
        ) as response:
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

                    data_str = line[5:].strip()
                    if not data_str:
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")
                    result["events"].append(event_type)

                    if event_type == "tool_start":
                        tool_name = event.get("tool_name", "")
                        result["tools_used"].append(tool_name)
                        result["tool_details"].append({
                            "tool": tool_name,
                            "args": event.get("tool_args", {}),
                        })

                    elif event_type == "tool_complete":
                        pass  # already captured in tool_start

                    elif event_type == "claude_response":
                        content = event.get("content", "")
                        if content:
                            result["answer"] += content

                    elif event_type == "workflow_complete":
                        answer = event.get("result", {}).get("answer", "")
                        if answer and not result["answer"]:
                            result["answer"] = answer

                    elif event_type == "workflow_failed":
                        result["error"] = event.get("error", "Unknown error")

    except httpx.TimeoutException:
        result["error"] = "Timeout"
    except Exception as e:
        result["error"] = str(e)

    result["duration_s"] = round(time.time() - start, 1)
    return result


def evaluate_result(test_case: dict, result: dict) -> dict:
    """Evaluate whether the test case passed."""
    tools_used = set(result["tools_used"])
    expected = set(test_case["expected_tools"])

    # Check if any expected temporal tool was used
    temporal_tools_used = {t for t in tools_used if any(exp in t for exp in expected)}
    any_temporal_tool = len(temporal_tools_used) > 0

    # Check answer quality
    has_answer = len(result["answer"]) > 50
    has_error = result["error"] is not None

    # Overall pass: used at least one expected tool and got a real answer
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


def main():
    print("=" * 70)
    print("  Temporal Knowledge Graph — Chat API Integration Test")
    print("  Issue #864 — Testing temporal MCP tools via chat agent")
    print("=" * 70)

    # Quick health check
    try:
        r = httpx.get(f"{BASE_URL}/api/health", timeout=5)
        print(f"\n  Health check: {r.status_code}")
    except Exception as e:
        print(f"\n  ERROR: Cannot reach {BASE_URL}: {e}")
        sys.exit(1)

    results = []
    passed = 0
    failed = 0

    for tc in TEST_CASES:
        print(f"\n{'─' * 70}")
        print(f"  [{tc['id']}] {tc['description']}")
        print(f"  Question: {tc['question'][:80]}...")
        print(f"  Expected tools: {tc['expected_tools']}")
        print(f"  Sending...", end=" ", flush=True)

        result = send_chat_question(tc["question"])
        evaluation = evaluate_result(tc, result)

        status = "PASS ✓" if evaluation["passed"] else "FAIL ✗"
        if evaluation["passed"]:
            passed += 1
        else:
            failed += 1

        print(f"{status} ({evaluation['duration_s']}s)")
        print(f"  Tools used: {evaluation['all_tools_used']}")
        print(f"  Temporal tools hit: {evaluation['temporal_tools_used']}")
        print(f"  Answer length: {evaluation['answer_length']} chars")

        if evaluation["has_error"]:
            print(f"  ERROR: {evaluation['error']}")

        # Print answer snippet
        if result["answer"]:
            snippet = result["answer"][:200].replace("\n", " ")
            print(f"  Answer preview: {snippet}...")

        results.append({
            "test_id": tc["id"],
            "question": tc["question"],
            **evaluation,
        })

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {passed} passed, {failed} failed out of {len(TEST_CASES)} tests")
    print(f"{'=' * 70}")

    # Write detailed results to file
    output_path = "tests/manual/temporal_test_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Detailed results written to {output_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
