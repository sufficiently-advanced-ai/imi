#!/usr/bin/env python3
"""Run a use-case pack's proof against a running imi instance (ADR-005 §2).

1. Ingests the pack's ``sample/`` corpus through ``POST /api/ingest`` with
   stable ``source_id``s, in event-time order, waiting for each job.
2. Asks every ``proof.yaml`` question through ``ask_kb`` (MCP, the surface the
   skills use) or ``POST /api/query`` (REST), and scores each answer against
   the expected facts. A question passes when at least ``min_facts`` of its
   facts appear (any listed alternative, case/punctuation-insensitive); the
   proof passes when the share of passing questions reaches ``min_pass_rate``.
   Thresholds, not exact strings — modeled on scripts/check_evals.sh.

Re-runs are free: the server dedups a ``source_id`` it has seen while it is up,
and a local ledger (``build/pack-proof/<host>/<pack>.json``) skips documents
already ingested into that instance across restarts. If both are lost the
re-ingest is still idempotent in the graph (deterministic signal ids, edges
MERGEd on source_id); it only costs model calls.

Proofs assume every optional feature is off: no decision-model endpoint, Local
MCP tier. Point it at a disposable instance — the sample corpus is fictional
but it does land in the knowledge base.

    python scripts/check_pack_proof.py freelance-implementation --url http://localhost:8080
    python scripts/check_pack_proof.py freelance-implementation --skip-ingest --surface rest
    python scripts/check_pack_proof.py freelance-implementation --dry-run      # no network

Needs: pyyaml, httpx, and (for --surface mcp) the ``mcp`` package. httpx honours
system proxy settings; if a local HTTP proxy is configured, set
``NO_PROXY=localhost,127.0.0.1`` or the MCP event stream may stall behind it.
Exit 0 = proof passed, 1 = proof failed, 2 = setup/ingest error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pack_common import REPO_ROOT, load_pack, load_yaml  # noqa: E402

LEDGER_DIR = REPO_ROOT / "build" / "pack-proof"
TERMINAL = {"completed", "failed", "dropped"}


# ---------------------------------------------------------------------------
# Scoring (pure)
# ---------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(text: str) -> str:
    """Lowercase, fold accents we commonly see, punctuation -> single spaces."""
    text = (text or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("’", "'")):
        text = text.replace(a, b)
    return " " + _NON_WORD.sub(" ", text).strip() + " "


def fact_matched(answer: str, alternatives: list[str]) -> str | None:
    """The first alternative found in ``answer`` (word-boundary, normalized), else None."""
    hay = normalize(answer)
    for alt in alternatives:
        needle = normalize(alt)
        if needle.strip() and needle in hay:
            return alt
    return None


@dataclass
class QuestionResult:
    id: str
    ask: str
    answer: str
    matched: list[str | None]
    min_facts: int
    error: str | None = None

    @property
    def hits(self) -> int:
        return sum(1 for m in self.matched if m)

    @property
    def passed(self) -> bool:
        return self.error is None and self.hits >= self.min_facts


def score_question(q: dict[str, Any], answer: str, error: str | None = None) -> QuestionResult:
    facts = q.get("facts") or []
    matched = [fact_matched(answer, f["any"]) for f in facts]
    return QuestionResult(
        id=q["id"], ask=q["ask"], answer=answer, matched=matched,
        min_facts=int(q.get("min_facts", len(facts))), error=error,
    )


@dataclass
class ProofResult:
    results: list[QuestionResult] = field(default_factory=list)
    min_pass_rate: float = 1.0

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.results) and self.pass_rate >= self.min_pass_rate


# ---------------------------------------------------------------------------
# Sample ingest (idempotent)
# ---------------------------------------------------------------------------


def ledger_path(url: str, pack_id: str, base: Path = LEDGER_DIR) -> Path:
    netloc = urlparse(url).netloc or "local"
    return base / re.sub(r"[^A-Za-z0-9_.-]", "_", netloc) / f"{pack_id}.json"


def load_ledger(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_ledger(path: Path, ledger: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")


def sample_documents(pack) -> list[dict[str, Any]]:
    manifest_path = pack.path / pack.manifest["sample"]
    docs = []
    for d in (load_yaml(manifest_path) or {}).get("documents") or []:
        content = (manifest_path.parent / d["file"]).read_text()
        ts = d["timestamp"]
        ts = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        body = {
            "content": content,
            "title": d["title"],
            "source": d["source"],
            "source_id": d["source_id"],
            "timestamp": ts.isoformat(),
        }
        if d.get("participants"):
            body["participants"] = d["participants"]
        docs.append({"body": body, "ts": ts, "sha": hashlib.sha256(content.encode()).hexdigest()})
    docs.sort(key=lambda x: x["ts"])
    return docs


def wait_for_job(client, job_id: str, timeout: float, poll: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/ingest/{job_id}/status")
        r.raise_for_status()
        status = r.json()
        if status.get("status") in TERMINAL:
            return status
        if time.monotonic() >= deadline:
            return {"status": "timeout", "job_id": job_id}
        time.sleep(poll)


def ingest_sample(client, docs, ledger: dict[str, str], *, timeout: float, force: bool = False,
                  log=print, poll: float = 3.0) -> tuple[dict[str, str], list[str]]:
    """Ingest docs not yet in ``ledger``. Returns (updated ledger, errors)."""
    errors: list[str] = []
    for d in docs:
        sid = d["body"]["source_id"]
        if not force and ledger.get(sid) == d["sha"]:
            log(f"  skip     {sid} (ledger)")
            continue
        r = client.post("/api/ingest", json=d["body"])
        if r.status_code not in (200, 202):
            errors.append(f"{sid}: HTTP {r.status_code} {r.text[:200]}")
            log(f"  ERROR    {sid}: HTTP {r.status_code}")
            continue
        resp = r.json()
        if resp.get("status") == "duplicate":
            log(f"  dup      {sid} (server already has it)")
            ledger[sid] = d["sha"]
            continue
        status = wait_for_job(client, resp["job_id"], timeout, poll)
        state = status.get("status")
        if state == "completed":
            ledger[sid] = d["sha"]
            log(f"  ingested {sid} ({status.get('content_type') or '?'})")
        else:
            errors.append(f"{sid}: job {resp['job_id']} ended {state}: {status.get('error') or ''}")
            log(f"  ERROR    {sid}: {state}")
    return ledger, errors


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def ask_rest(client, question: str) -> str:
    r = client.post("/api/query", json={"question": question, "prompt_type": "search"})
    r.raise_for_status()
    data = r.json()
    return data.get("answer") or data.get("response") or ""


async def ask_mcp_all(url: str, questions: list[str], max_steps: int) -> list[tuple[str, str | None]]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    out: list[tuple[str, str | None]] = []
    async with sse_client(url.rstrip("/") + "/api/mcp/sse", timeout=30, sse_read_timeout=600) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for q in questions:
                try:
                    res = await session.call_tool("ask_kb", {"intent": q, "max_steps": max_steps})
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = {"answer": text}
                    if isinstance(payload, dict) and payload.get("error"):
                        out.append(("", str(payload["error"])))
                    else:
                        out.append((str(payload.get("answer", "")) if isinstance(payload, dict) else text, None))
                except Exception as e:  # one bad question must not sink the run
                    out.append(("", f"{type(e).__name__}: {e}"))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_result(proof: ProofResult) -> None:
    for r in proof.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.id}: {r.hits}/{len(r.matched)} facts (need {r.min_facts})")
        if r.error:
            print(f"        error: {r.error}")
        elif not r.passed:
            print(f"        answer: {r.answer[:300].replace(chr(10), ' ')}")
    print(
        f"\nProof {'PASSED' if proof.passed else 'FAILED'}: "
        f"{sum(r.passed for r in proof.results)}/{len(proof.results)} questions "
        f"({proof.pass_rate:.0%}, threshold {proof.min_pass_rate:.0%})"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="pack id (directory under use-cases/) or path")
    ap.add_argument("--url", default="http://localhost:8080", help="imi base URL (default: %(default)s)")
    ap.add_argument("--surface", choices=["mcp", "rest"], default="mcp",
                    help="ask through MCP ask_kb (default) or REST /api/query")
    ap.add_argument("--skip-ingest", action="store_true", help="only ask the questions")
    ap.add_argument("--force-ingest", action="store_true", help="ignore the local ledger (server dedup still applies)")
    ap.add_argument("--job-timeout", type=float, default=300, help="seconds to wait per ingest job")
    ap.add_argument("--max-steps", type=int, default=8, help="ask_kb max_steps")
    ap.add_argument("--report", type=Path, help="write a JSON report here")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    args = ap.parse_args(argv)

    pack = load_pack(args.pack)
    proof_cfg = load_yaml(pack.path / pack.manifest["proof"]) or {}
    questions = proof_cfg.get("questions") or []
    docs = sample_documents(pack)
    ledger_file = ledger_path(args.url, pack.id)

    print(f"Pack {pack.id}@{pack.version} against {args.url} ({len(docs)} sample docs, {len(questions)} questions)")
    if args.dry_run:
        for d in docs:
            print(f"  would ingest {d['body']['source_id']}  {d['ts'].isoformat()}  [{d['body']['source']}]")
        for q in questions:
            print(f"  would ask    {q['id']}: {q['ask']}")
        return 0

    import httpx

    with httpx.Client(base_url=args.url, timeout=120) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as e:
            print(f"Instance not healthy at {args.url}: {e}")
            return 2

        if not args.skip_ingest:
            print("Ingesting sample corpus:")
            ledger, errors = ingest_sample(
                client, docs, load_ledger(ledger_file), timeout=args.job_timeout, force=args.force_ingest
            )
            save_ledger(ledger_file, ledger)
            if errors:
                print("Sample ingest failed; not scoring a partial corpus:")
                for e in errors:
                    print(f"  {e}")
                return 2

        print(f"Asking {len(questions)} questions via {args.surface}:")
        if args.surface == "mcp":
            answers = asyncio.run(ask_mcp_all(args.url, [q["ask"] for q in questions], args.max_steps))
        else:
            answers = []
            for q in questions:
                try:
                    answers.append((ask_rest(client, q["ask"]), None))
                except httpx.HTTPError as e:
                    answers.append(("", f"{type(e).__name__}: {e}"))

    proof = ProofResult(
        results=[score_question(q, a, err) for q, (a, err) in zip(questions, answers, strict=True)],
        min_pass_rate=float(proof_cfg.get("min_pass_rate", 1.0)),
    )
    print_result(proof)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({
            "pack": pack.id, "version": pack.version, "url": args.url, "surface": args.surface,
            "passed": proof.passed, "pass_rate": proof.pass_rate, "min_pass_rate": proof.min_pass_rate,
            "questions": [
                {"id": r.id, "passed": r.passed, "hits": r.hits, "min_facts": r.min_facts,
                 "matched": r.matched, "error": r.error, "answer": r.answer}
                for r in proof.results
            ],
        }, indent=2) + "\n")
    return 0 if proof.passed else 1


if __name__ == "__main__":
    sys.exit(main())
