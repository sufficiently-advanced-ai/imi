#!/usr/bin/env python3
"""Classify a corpus's memory records into lanes — read-only, report only.

The memory store mixes two things recall should not rank together: a *library*
(articles, newsletters, videos, news Scott read or received) and *memory*
(his own decisions, notes, conversations and commitments). This script judges
every record and writes verdicts; it never modifies the corpus.

Records judged
  capture   memory/captures/*.json        lane: memory | library | junk
  document  meetings/**/*.md with signals kind: conversation | own_note | third_party | junk
  signal    signals/*.json (per signal)   lane inherited from its document,
                                          plus a type check and a standalone check
  agent     memory/agent/**/*.json        rules only

Deterministic rules run first (test fixtures, DMARC/transactional mail, the
same URL captured twice); everything else goes to the decision model (Jev,
operations ``memory_lane_capture`` / ``memory_lane_document`` /
``memory_signal_check``) — one ``decide()`` per record, all questions batched.

    python scripts/classify_memories.py classify --corpus <repo dir> --out <dir> [--limit N]
    python scripts/classify_memories.py report   --corpus <repo dir> --out <dir>

``classify`` appends to ``<out>/verdicts.jsonl`` and skips ids already there,
so it can be interrupted and resumed. ``report`` writes ``<out>/report.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import glob
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from app.services.inference.decisions import (  # noqa: E402
    Choice,
    DecisionClient,
    DecisionUnavailable,
    Noul,
)

OWNER = "Scott Jennings"
CAPTURE_CHARS = 2500  # Jev degrades on large, noisy state; the head is enough to judge a lane
DOC_CHARS = 2500
SIGNAL_DOC_CHARS = 600
LOW_CONFIDENCE = 0.60
# DigitalOcean caps /v1/systemone at ~1000 requests/min per team (429
# "systemone_requests_per_minute"; measured 2026-09-26). Pace below it — the
# cap is shared with holodeck's live ingest.
DEFAULT_RPM = 900

# Self-contained so the script runs anywhere the key is set, independent of the
# host's config/inference.yaml routing.
JEV_CONFIG = {
    "endpoints": {
        "do-jev": {
            "type": "digitalocean",
            "model": "typesafe-jev-1.13.0",
            "api_key_env": "DIGITALOCEAN_MODEL_ACCESS_KEY",
            "pricing": {"input": 0.042},
            "timeout": 30,
            "max_concurrency": 8,
        }
    },
    "default": "do-jev",
}

# ---- corpus loading ---------------------------------------------------------


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def load_captures(root: str) -> list[dict]:
    return [load_json(p) for p in sorted(glob.glob(f"{root}/memory/captures/*.json"))]


def load_agent_memories(root: str) -> list[dict]:
    return [load_json(p) for p in sorted(glob.glob(f"{root}/memory/agent/**/*.json", recursive=True))]


def load_signal_files(root: str) -> list[dict]:
    return [load_json(p) for p in sorted(glob.glob(f"{root}/signals/*.json"))]


_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.S)


def load_documents(root: str, wanted: set[str]) -> dict[str, dict]:
    """meeting_id -> {title, participants, body} for the documents signals cite.
    Signal files reference the frontmatter ``meeting_id``, not the file name."""
    docs: dict[str, dict] = {}
    for p in glob.glob(f"{root}/meetings/**/*.md", recursive=True):
        with open(p, errors="replace") as f:
            text = f.read()
        m = _FM.match(text)
        if not m:
            continue
        fm, body = m.groups()
        mid = re.search(r"^meeting_id:\s*\"?([^\"\n]+)", fm, re.M)
        if not mid or mid.group(1).strip() not in wanted:
            continue
        title = re.search(r"^title:\s*\"?(.*?)\"?$", fm, re.M)
        parts = re.search(r"^participants:\n((?:\s+- .*\n?)+)", fm, re.M)
        docs[mid.group(1).strip()] = {
            "path": p,
            "title": title.group(1) if title else "",
            "participants": re.findall(r"- (.*)", parts.group(1))[:12] if parts else [],
            "body": body,
        }
    return docs


# ---- text helpers -----------------------------------------------------------

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean(text: str, limit: int) -> str:
    """Strip image/link markup and collapse whitespace so the head of a page
    carries text, not URLs. Not a content cleaner — just state hygiene."""
    text = _MD_IMAGE.sub("", text or "")
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:limit]


def capture_url(c: dict) -> str | None:
    sid = c.get("source_id") or ""
    if sid.startswith("http"):
        url = sid
    else:
        m = re.search(r"Source URL: (\S+)", c.get("content") or "")
        if not m:
            return None
        url = m.group(1)
    url = url.split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", url).lower() or None


def mail_sender(content: str) -> str:
    m = re.search(r"^From: (.+)$", content or "", re.M)
    return m.group(1).strip() if m else ""


# ---- deterministic rules ----------------------------------------------------

_TEST_SOURCES = {"probe", "lcars-test"}
_TEST_TEXT = re.compile(
    r"\b(e2e[- ]?(test|probe|check|capture)|purple elephant|parameter isolation probe|"
    r"test capture|enrichment latency|probe\d*)\b",
    re.I,
)
_AUTOMATED_SENDERS = re.compile(
    r"dmarc|mimecastreport|no\.reply\.alerts@chase|noreply@.*(statement|billing)",
    re.I,
)


def capture_rule(c: dict) -> tuple[str, str] | None:
    """(lane, reason) when a rule decides the capture outright."""
    content = c.get("content") or ""
    if c.get("source") in _TEST_SOURCES:
        return "junk", f"test source '{c['source']}'"
    if len(content) < 400 and (_TEST_TEXT.search(content) or _TEST_TEXT.search(str(c.get("source_id")))):
        return "junk", "test/probe fixture"
    if len(content.strip()) < 20:
        return "junk", "empty content"
    sender = mail_sender(content)
    if sender and _AUTOMATED_SENDERS.search(sender):
        return "junk", f"automated mail ({sender[:60]})"
    return None


def cross_source_duplicates(captures: list[dict]) -> dict[str, str]:
    """capture id -> id of the copy to keep, for URLs captured more than once.
    Keep the longest body (the full text beats an openbrain summary)."""
    by_url: dict[str, list[dict]] = collections.defaultdict(list)
    for c in captures:
        u = capture_url(c)
        if u:
            by_url[u].append(c)
    dup_of: dict[str, str] = {}
    for group in by_url.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda c: len(c.get("content") or ""))
        for c in group:
            if c is not keep:
                dup_of[c["id"]] = keep["id"]
    return dup_of


# ---- Jev questions ----------------------------------------------------------

LANE_CRITERIA = {
    "memory": (
        f"Personal memory: about {OWNER}'s own work, life, projects, clients, relationships, "
        "decisions, plans, commitments, lessons, or conversations and communities he took part in "
        "(his meetings, his AI Circle peer group, his own notes, ideas and retrospectives). "
        "Includes meeting summaries and mail written to him personally by people he works with."
    ),
    "library": (
        "Library/reference: third-party published content he read, watched or subscribed to — "
        "articles, blog posts, newsletters, videos and their transcripts, news, product "
        "announcements, papers, documentation. About the outside world, not about his own life. "
        "A blog post or essay written in the first person by someone else is library: 'I' there is "
        "the author, not him."
    ),
    "junk": (
        "No durable value: automated or transactional notices (statements, bills, receipts, "
        "alerts, verification codes, shipping, account or security notifications, marketing "
        "promos), system test fixtures and probes, error / login / paywall / suspended pages, "
        "or content that is empty or mostly navigation and boilerplate."
    ),
}

CAPTURE_QUESTIONS = {
    "lane": Choice(
        instructions=(
            f"This is one record from {OWNER}'s personal knowledge store. Which kind of record is it? "
            "Judge by what the content is, not by its tags."
        ),
        criteria=LANE_CRITERIA,
    ),
    "durable": Noul(
        instructions=(
            f"Would {OWNER} plausibly want this recalled when working on something related six or "
            "more months from now? Answer no for ephemeral, generic, promotional or trivial items."
        ),
    ),
    "clean_body": Noul(
        instructions=(
            "Is the content a clean, readable body of text? Answer no if it is mostly site "
            "navigation, link lists, cookie or subscribe banners, markup, email chrome, or if it "
            "stops before reaching the substance."
        ),
    ),
}

DOC_KIND_CRITERIA = {
    "conversation": (
        f"A conversation {OWNER} took part in: a meeting or call transcript, meeting notes, or a "
        "discussion in a group or community he belongs to (e.g. AI Circle)."
    ),
    "own_note": (
        f"Something {OWNER} wrote or dictated himself: notes, plans, ideas, retrospectives, "
        "decision records, drafts."
    ),
    "third_party": (
        "Third-party published content: an article, newsletter issue, blog post, video, podcast, "
        "news item, announcement, paper or documentation that he received or read."
    ),
    "junk": (
        "No durable value: transactional or automated mail, test fixtures, error or login pages, "
        "empty or boilerplate-only content."
    ),
}

DOC_QUESTIONS = {
    "kind": Choice(
        instructions=(
            f"This document was ingested into {OWNER}'s knowledge store and signals were extracted "
            "from it. What kind of document is it? The title field may be a machine-written "
            "summary; judge from the body."
        ),
        criteria=DOC_KIND_CRITERIA,
    ),
}

SIGNAL_TYPES = {
    "decision": (
        "A decision: a choice actually made or agreed by the people in the conversation (or by "
        f"{OWNER} himself) about what they will do. A company's news or an author's claim is not "
        "a decision."
    ),
    "action_item": (
        "An action item: a concrete task a specific person in the conversation committed to or "
        "was assigned. General advice, recommendations to readers, and things to read or study "
        "are not action items."
    ),
    "key_point": "A key point: a factual statement of what was said, reported or established.",
    "insight": "An insight: an interpretation, lesson, pattern or implication drawn from the material.",
}


# The stored type is deliberately NOT in the state: with it present, Jev's
# choice anchors on it (calibration run, 2026-09-26). Typed blind, a mismatch
# with the stored type is the finding.
SIGNAL_QUESTIONS = {
        "type": Choice(
            instructions=(
                "The statement was extracted from the source document. Which signal type is it, "
                "given who said it and where it came from?"
            ),
            criteria=SIGNAL_TYPES,
        ),
        "standalone": Noul(
            instructions=(
                "Does the statement carry specific, useful information on its own, understandable "
                "without the source document? Answer no for vague, generic, truncated or "
                "boilerplate statements."
            ),
        ),
}


# ---- states -----------------------------------------------------------------


def capture_state(c: dict) -> dict:
    s = {
        "source": c.get("source"),
        "source_id": c.get("source_id"),
        "summary": c.get("summary") or None,
        "content": clean(c.get("content") or "", CAPTURE_CHARS),
    }
    return {k: v for k, v in s.items() if v}


def doc_state(doc: dict) -> dict:
    return {
        "title": doc["title"],
        "participants": doc["participants"],
        "body": clean(doc["body"], DOC_CHARS),
    }


def signal_state(sig: dict, doc: dict | None, doc_kind: str | None) -> dict:
    s = {
        "statement": sig.get("content"),
        "owner": (sig.get("owner") or {}).get("name"),
        "source_document": {
            "kind": doc_kind,
            "title": (doc or {}).get("title") or sig.get("source_meeting_title"),
            "opening": clean((doc or {}).get("body", ""), SIGNAL_DOC_CHARS) or None,
        },
    }
    s["source_document"] = {k: v for k, v in s["source_document"].items() if v}
    return {k: v for k, v in s.items() if v}


# ---- classify ---------------------------------------------------------------


def _choice(res, name):
    a = res.choice(name)
    return {"choice": a.choice, "p": round(a.probabilities.get(a.choice, 0.0), 3),
            "probs": {k: round(v, 3) for k, v in a.probabilities.items()}}


class Writer:
    def __init__(self, path: Path):
        self.path = path
        self.done: set[str] = set()
        if path.exists():
            for line in path.open():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("status") != "error":
                    self.done.add(r["key"])
        self.f = path.open("a")
        self.cost = 0.0
        self.n = 0
        self.errors = 0

    def write(self, rec: dict) -> None:
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()
        self.n += 1
        if rec.get("status") == "error":
            self.errors += 1
        if self.n % 250 == 0:
            print(f"  … {self.n} written, {self.errors} errors, ${self.cost:.4f}", file=sys.stderr)


class Pacer:
    """Spaces request starts evenly to stay under a requests-per-minute cap."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / rpm
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self.next > now:
                await asyncio.sleep(self.next - now)
            self.next = max(now, self.next) + self.interval


PACER: Pacer | None = None


async def _judge(client, w: Writer, key: str, base: dict, state, questions, operation, parse):
    if PACER:
        await PACER.wait()
    try:
        res = await client.decide(state, questions, operation=operation)
    except (DecisionUnavailable, ValueError) as e:
        w.write({**base, "key": key, "status": "error", "error": str(e)[:300]})
        return None
    w.cost += res.cost_usd
    rec = {**base, "key": key, "status": "ok", **parse(res)}
    w.write(rec)
    return rec


async def classify(corpus: str, out: Path, limit: int | None, seed: int, rpm: int) -> None:
    global PACER
    PACER = Pacer(rpm)
    out.mkdir(parents=True, exist_ok=True)
    w = Writer(out / "verdicts.jsonl")
    client = DecisionClient(JEV_CONFIG)
    rng = random.Random(seed)

    captures = load_captures(corpus)
    agents = load_agent_memories(corpus)
    sig_files = load_signal_files(corpus)
    docs = load_documents(corpus, {s["meeting_id"] for s in sig_files})
    dup_of = cross_source_duplicates(captures)
    print(f"captures={len(captures)} agent={len(agents)} signal_files={len(sig_files)} "
          f"docs_found={len(docs)} url_dups={len(dup_of)} already_done={len(w.done)}", file=sys.stderr)

    if limit:  # a random slice of each kind, for calibration runs
        captures = rng.sample(captures, min(limit, len(captures)))
        sig_files = rng.sample(sig_files, min(max(1, limit // 5), len(sig_files)))

    # Rules-only records
    for a in agents:
        key = f"agent:{a['id']}"
        if key not in w.done:
            is_test = bool(re.search(r"\be2e\b", f"{a.get('content')} {a.get('task_id')} {a.get('idempotency_key')}", re.I))
            w.write({"kind": "agent", "id": a["id"], "key": key, "status": "ok",
                     "lane": "junk" if is_test else "memory",
                     "rule": "e2e test fixture" if is_test else None,
                     "snippet": (a.get("content") or "")[:160]})

    sem = asyncio.Semaphore(32)  # bounds queued tasks; the client bounds in-flight HTTP

    async def one_capture(c):
        key = f"capture:{c['id']}"
        if key in w.done:
            return
        base = {"kind": "capture", "id": c["id"], "source": c.get("source"),
                "len": len(c.get("content") or ""), "has_summary": bool(c.get("summary")),
                "snippet": clean(c.get("content") or "", 160),
                "duplicate_of": dup_of.get(c["id"])}
        rule = capture_rule(c)
        if rule:
            w.write({**base, "key": key, "status": "ok", "lane": rule[0], "rule": rule[1]})
            return
        async with sem:
            await _judge(client, w, key, base, capture_state(c), CAPTURE_QUESTIONS, "memory_lane_capture",
                         lambda r: {"lane_verdict": _choice(r, "lane"),
                                    "durable": round(r.noul("durable"), 3),
                                    "clean_body": round(r.noul("clean_body"), 3)})

    async def one_document(sf):
        """Judge the document, then each of its signals with the document kind in state."""
        mid = sf["meeting_id"]
        doc = docs.get(mid)
        dkey = f"document:{mid}"
        kind = None
        if dkey in w.done:
            kind = w_doc_kind.get(mid)
        elif doc is None:
            w.write({"kind": "document", "id": mid, "key": dkey, "status": "ok", "missing": True,
                     "title": sf.get("meeting_title"), "n_signals": len(sf["signals"])})
        else:
            async with sem:
                rec = await _judge(client, w, dkey,
                                   {"kind": "document", "id": mid, "title": doc["title"][:160],
                                    "n_signals": len(sf["signals"]), "snippet": clean(doc["body"], 160)},
                                   doc_state(doc), DOC_QUESTIONS, "memory_lane_document",
                                   lambda r: {"kind_verdict": _choice(r, "kind")})
            kind = rec["kind_verdict"]["choice"] if rec else None

        async def one_signal(sig):
            skey = f"signal:{sig['id']}"
            if skey in w.done:
                return
            async with sem:
                await _judge(client, w, skey,
                             {"kind": "signal", "id": sig["id"], "document": mid, "doc_kind": kind,
                              "stated_type": sig.get("type"), "owner": (sig.get("owner") or {}).get("name"),
                              "review_status": sig.get("review_status"),
                              "snippet": (sig.get("content") or "")[:200]},
                             signal_state(sig, doc, kind), SIGNAL_QUESTIONS,
                             "memory_signal_check",
                             lambda r: {"type_verdict": _choice(r, "type"),
                                        "standalone": round(r.noul("standalone"), 3)})

        await asyncio.gather(*(one_signal(s) for s in sf["signals"]))

    # Document kinds already judged in a previous run, for resumed signal calls
    w_doc_kind: dict[str, str] = {}
    if (out / "verdicts.jsonl").exists():
        for line in (out / "verdicts.jsonl").open():
            r = json.loads(line)
            if r.get("kind") == "document" and r.get("kind_verdict"):
                w_doc_kind[r["id"]] = r["kind_verdict"]["choice"]

    await asyncio.gather(*(one_capture(c) for c in captures), *(one_document(sf) for sf in sig_files))
    await client.aclose()
    print(f"done: {w.n} written, {w.errors} errors, ${w.cost:.4f}", file=sys.stderr)


# ---- report -----------------------------------------------------------------

DOC_KIND_TO_LANE = {"conversation": "memory", "own_note": "memory", "third_party": "library", "junk": "junk"}


def latest_verdicts(out: Path) -> dict[str, dict]:
    v: dict[str, dict] = {}
    for line in (out / "verdicts.jsonl").open():
        r = json.loads(line)
        if r["key"] not in v or r.get("status") == "ok":
            v[r["key"]] = r
    return v


def capture_lane(r: dict) -> tuple[str, bool]:
    """(lane, low_confidence)"""
    if r.get("lane"):
        return r["lane"], False
    lv = r["lane_verdict"]
    return lv["choice"], lv["p"] < LOW_CONFIDENCE


DOC_KINDS: dict[str, str] = {}  # document id -> judged kind, filled by report()


def signal_lane(r: dict) -> str:
    lane = DOC_KIND_TO_LANE.get(DOC_KINDS.get(r.get("document")) or r.get("doc_kind") or "", "unknown")
    if lane != "junk" and r.get("standalone", 1) < 0.2:
        return "junk"
    return lane


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "–"


def report(corpus: str, out: Path) -> None:
    v = latest_verdicts(out)
    ok = [r for r in v.values() if r.get("status") == "ok"]
    errs = [r for r in v.values() if r.get("status") == "error"]
    caps = [r for r in ok if r["kind"] == "capture"]
    docs = [r for r in ok if r["kind"] == "document"]
    sigs = [r for r in ok if r["kind"] == "signal"]
    agents = [r for r in ok if r["kind"] == "agent"]
    DOC_KINDS.update({r["id"]: r["kind_verdict"]["choice"] for r in docs if r.get("kind_verdict")})
    C = collections.Counter
    L: list[str] = []
    p = L.append

    p("# Memory lane classification — report\n")
    p(f"Corpus: `{corpus}` · verdicts: `{out / 'verdicts.jsonl'}` · decision model: Jev "
      f"(`memory_lane_capture`, `memory_lane_document`, `memory_signal_check`). Read-only: nothing in the corpus was changed.\n")
    p(f"Judged: {len(caps)} captures, {len(docs)} source documents, {len(sigs)} signals, "
      f"{len(agents)} agent memories. Unresolved errors: {len(errs)}.\n")

    # ---- captures
    lanes = C(capture_lane(r)[0] for r in caps)
    low = [r for r in caps if capture_lane(r)[1]]
    p("## Captures\n")
    p("| lane | count | share |\n|---|---:|---:|")
    for lane in ("memory", "library", "junk"):
        p(f"| {lane} | {lanes[lane]} | {pct(lanes[lane], len(caps))} |")
    p(f"\nLow-confidence lane calls (top-choice p < {LOW_CONFIDENCE}): {len(low)}. "
      f"Decided by rule: {sum(1 for r in caps if r.get('rule'))}. "
      f"Cross-source URL duplicates (a longer copy exists): {sum(1 for r in caps if r.get('duplicate_of'))}.\n")

    p("### Lane by source\n")
    srcs = sorted(C(r["source"] for r in caps).items(), key=lambda kv: -kv[1])
    p("| source | n | memory | library | junk | durable≥0.5 | clean body≥0.5 |\n|---|---:|---:|---:|---:|---:|---:|")
    for s, n in srcs:
        rs = [r for r in caps if r["source"] == s]
        lc = C(capture_lane(r)[0] for r in rs)
        judged = [r for r in rs if "durable" in r]
        dur = sum(r["durable"] >= 0.5 for r in judged)
        cb = sum(r["clean_body"] >= 0.5 for r in judged)
        p(f"| {s} | {n} | {lc['memory']} | {lc['library']} | {lc['junk']} | "
          f"{pct(dur, len(judged))} | {pct(cb, len(judged))} |")

    rules = C(re.sub(r"\(.*", "(…)", r["rule"]) for r in caps if r.get("rule"))
    p("\n### Rule hits\n")
    for k, n in rules.most_common():
        p(f"- {k}: {n}")

    judged = [r for r in caps if "durable" in r]
    lib = [r for r in judged if capture_lane(r)[0] == "library"]
    p(f"\n### Library quality\n\nOf {len(lib)} library captures judged by Jev: "
      f"{sum(r['durable'] >= 0.5 for r in lib)} look durable, "
      f"{sum(r['clean_body'] < 0.5 for r in lib)} have an unclean body (navigation, chrome, or cut off), "
      f"{sum(r['len'] == 4000 for r in lib)} are exactly 4,000 chars (truncated at intake).\n")

    rng = random.Random(1)

    def samples(rows, n, fmt):
        for r in rng.sample(rows, min(n, len(rows))):
            p(fmt(r))

    def cap_line(r):
        lane, _ = capture_lane(r)
        conf = r.get("rule") or f"p={r['lane_verdict']['p']:.2f} durable={r['durable']:.2f}"
        return f"- `{r['source']}` · **{lane}** ({conf}) — {r['snippet'][:150]}"

    p("### Samples\n")
    for lane in ("memory", "library", "junk"):
        rows = [r for r in caps if capture_lane(r)[0] == lane and not r.get("rule")]
        p(f"**{lane}** (Jev):\n")
        samples(rows, 8, cap_line)
        p("")
    p("**Low confidence** (review these by hand):\n")
    samples(low, 12, lambda r: cap_line(r) + f"  · probs {r['lane_verdict']['probs']}")
    p("")
    p("**Memory from openbrain-import** (first-person notes buried in the import):\n")
    samples([r for r in caps if r["source"] == "openbrain-import" and capture_lane(r)[0] == "memory"], 8, cap_line)

    # ---- documents and signals
    p("\n## Signal source documents\n")
    kinds = C((r.get("kind_verdict") or {}).get("choice", "missing file") for r in docs)
    p("| kind | documents | signals |\n|---|---:|---:|")
    for k, n in kinds.most_common():
        ns = sum(r["n_signals"] for r in docs if (r.get("kind_verdict") or {}).get("choice", "missing file") == k)
        p(f"| {k} | {n} | {ns} |")
    p("\nSamples:\n")
    for k in ("conversation", "own_note", "third_party", "junk"):
        rows = [r for r in docs if (r.get("kind_verdict") or {}).get("choice") == k]
        for r in rng.sample(rows, min(4, len(rows))):
            p(f"- **{k}** (p={r['kind_verdict']['p']:.2f}, {r['n_signals']} signals) — {r['title'][:110]}")

    p("\n## Signals\n")
    sl = C(signal_lane(r) for r in sigs)
    p("| lane | count | share |\n|---|---:|---:|")
    for lane in ("memory", "library", "junk", "unknown"):
        if sl[lane]:
            p(f"| {lane} | {sl[lane]} | {pct(sl[lane], len(sigs))} |")
    p("\nLane comes from the source document's kind; a signal Jev scores as not standalone (p < 0.2) is junk.\n")

    p("### Stored type vs. Jev's blind type, by source lane\n")
    for lane in ("memory", "library"):
        rows = [r for r in sigs if signal_lane(r) == lane]
        if not rows:
            continue
        p(f"**{lane}** ({len(rows)} signals)\n")
        types = ["decision", "action_item", "key_point", "insight"]
        p("| stored \\ Jev | " + " | ".join(types) + " | Jev disagrees |\n|---|" + "---:|" * 5)
        for st in types:
            rs = [r for r in rows if r["stated_type"] == st]
            if not rs:
                continue
            c = C(r["type_verdict"]["choice"] for r in rs)
            wrong = sum(r["type_verdict"]["choice"] != st for r in rs)
            p(f"| {st} ({len(rs)}) | " + " | ".join(str(c[t]) for t in types) + f" | {wrong} ({pct(wrong, len(rs))}) |")
        p("")

    def sig_line(r):
        return (f"- [{r['doc_kind']}] stored **{r['stated_type']}** → Jev {r['type_verdict']['choice']} "
                f"(p={r['type_verdict']['p']:.2f}, standalone={r['standalone']:.2f}"
                f"{', owner=' + r['owner'] if r.get('owner') else ''}) — {r['snippet'][:150]}")

    p("### Samples\n")
    p("**Decisions from library documents** (news mistyped as decisions):\n")
    samples([r for r in sigs if signal_lane(r) == "library" and r["stated_type"] == "decision"], 6, sig_line)
    p("\n**Action items Jev retypes**:\n")
    samples([r for r in sigs if r["stated_type"] == "action_item" and r["type_verdict"]["choice"] != "action_item"], 8, sig_line)
    p("\n**Action items Jev accepts, from conversations**:\n")
    samples([r for r in sigs if r["stated_type"] == "action_item" and r["type_verdict"]["choice"] == "action_item"
             and signal_lane(r) == "memory"], 6, sig_line)
    p("\n**Not standalone** (junk):\n")
    samples([r for r in sigs if r.get("standalone", 1) < 0.2], 8, sig_line)
    p("\n**Memory-lane signals**:\n")
    samples([r for r in sigs if signal_lane(r) == "memory" and r.get("standalone", 0) >= 0.5], 8, sig_line)

    p("\n## Agent memories\n")
    for r in agents:
        p(f"- **{r['lane']}** ({r.get('rule') or 'kept'}) — {r['snippet'][:120]}")

    p("\n## Totals\n")
    mem = lanes["memory"] + sl["memory"] + sum(r["lane"] == "memory" for r in agents)
    libn = lanes["library"] + sl["library"]
    junk = lanes["junk"] + sl["junk"] + sum(r["lane"] == "junk" for r in agents)
    tot = len(caps) + len(sigs) + len(agents)
    p(f"Of {tot} memory records: **memory {mem} ({pct(mem, tot)})**, library {libn} ({pct(libn, tot)}), "
      f"junk {junk} ({pct(junk, tot)}).\n")
    if errs:
        by_kind = ", ".join(f"{k}={n}" for k, n in C(r["kind"] for r in errs).items())
        p(f"\n{len(errs)} records failed to classify ({by_kind}); re-run `classify` to retry.\n")

    (out / "report.md").write_text("\n".join(L) + "\n")
    print(f"wrote {out / 'report.md'}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["classify", "report"])
    ap.add_argument("--corpus", required=True, help="corpus repo dir (contains memory/, signals/, meetings/)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, help="classify a random sample of N captures (+N/5 documents)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--rpm", type=int, default=DEFAULT_RPM, help="max Jev requests per minute")
    args = ap.parse_args()
    if args.command == "classify":
        asyncio.run(classify(args.corpus, args.out, args.limit, args.seed, args.rpm))
    else:
        report(args.corpus, args.out)


if __name__ == "__main__":
    main()
