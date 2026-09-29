#!/usr/bin/env python3
"""Run signal dedup over the signals already in the store.

Replays every meeting in time order as if it were being ingested: each
meeting's signals are compared with the signals of earlier meetings (and
earlier ones in the same meeting) exactly as DETECT_DUPLICATES does, then
judged by the decision model. Embeddings are computed here with the live
embedder, so the result does not depend on what the vector index holds.

Dry run by default: prints every verdict and the resulting hides/links and
writes nothing. ``--apply`` saves each changed meeting file through the
SignalStore (which re-indexes it). Run inside the app container:

    docker exec -w /app imi-app python scripts/backfill_signal_dedup.py
    docker exec -w /app imi-app python scripts/backfill_signal_dedup.py --apply

Signals already hidden stay hidden and are not re-judged as originals.
Outside the app process ``--apply`` cannot re-index, so afterwards refresh the
vector metadata (search reads ``duplicate_of`` from it) in-process:

    docker exec imi-app curl -s -X POST localhost:8000/api/admin/backfill-signal-index
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.services import signal_dedup  # noqa: E402
from app.services.inference.decisions import get_decision_client  # noqa: E402
from app.services.signal_indexing import _get_semantica  # noqa: E402
from app.services.signal_store import SignalStore  # noqa: E402


class _ForceOn:
    """Judge as in ``on`` mode whatever the configured mode; writes are the script's call."""

    def __init__(self, client):
        self._client = client

    def mode(self, _operation: str) -> str:
        return "on"

    async def decide(self, state, questions, *, operation):
        return await self._client.decide(state, questions, operation=operation)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill signal dedup over stored signals.")
    ap.add_argument("--apply", action="store_true", help="write hides/links (default: dry run)")
    args = ap.parse_args()

    store = SignalStore()
    containers = store.load_all()
    signals = [s for c in containers for s in c.signals]
    container_of = {s.id: c for c in containers for s in c.signals}
    if not signals:
        print("no signals")
        return 0

    # Outside the app process the live stack is not registered; build the same
    # embedder the app uses (FastEmbed, semantica_init.EMBEDDING_MODEL).
    sk = _get_semantica()
    if sk is not None:
        embedder = sk.embedder
    else:
        from app.services.semantica_init import create_embedding_generator

        embedder = create_embedding_generator()

    def embed(text: str) -> np.ndarray:
        vec = np.asarray(embedder.generate_embeddings(text, data_type="text"), dtype=float)
        vec = vec[0] if vec.ndim > 1 else vec
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    vectors = {s.id: embed(s.content) for s in signals if s.content and s.content.strip()}

    signals.sort(key=lambda s: (s.source_timestamp or "", s.source_meeting_id, s.position))
    meetings: list[list] = []
    for s in signals:
        if not meetings or meetings[-1][0].source_meeting_id != s.source_meeting_id:
            meetings.append([])
        meetings[-1].append(s)

    client = _ForceOn(get_decision_client())
    standing: dict = {}
    pairs = 0
    changed: set[str] = set()
    for batch in meetings:
        def similar(text: str, stype: str, batch=batch) -> list[tuple[str, float]]:
            me = next(s for s in batch if s.content == text)
            if me.id not in vectors:
                return []
            scored = [(sid, float(vectors[me.id] @ vectors[sid]))
                      for sid, s in standing.items() if s.type == stype and sid in vectors]
            return sorted(scored, key=lambda x: -x[1])[:10]

        def batch_similarity(a, b) -> float:
            va, vb = vectors.get(a.id), vectors.get(b.id)
            return float(va @ vb) if va is not None and vb is not None else 0.0

        cands = signal_dedup.find_duplicate_candidates(
            batch, similar, standing, batch_similarity=batch_similarity)
        pairs += len(cands)
        outcome = await signal_dedup.judge_duplicates(cands, client)
        for s in batch:
            if s.metadata.get("duplicate_of") or s.metadata.get("related_signals"):
                changed.add(s.id)
        changed.update(s.id for s in outcome.hidden_old)
        for s in batch:
            standing[s.id] = s

    by_id = {s.id: s for s in signals}
    hidden = signal_dedup.resolve_hidden(signals)
    links = sum(len(s.metadata.get("related_signals", [])) for s in signals)
    print(f"{len(signals)} signals, {len(meetings)} meetings, {pairs} candidate pairs -> "
          f"{len(hidden)} hidden, {links} links")
    for sid, kept in sorted(hidden.items(), key=lambda kv: by_id[kv[0]].source_timestamp or ""):
        h, k = by_id[sid], by_id[kept]
        print(f"\nHIDE {h.type} {sid[:8]} ({h.source_meeting_title}) under {kept[:8]} ({k.source_meeting_title})"
              f"\n  hidden: {h.content[:160]}\n  kept:   {k.content[:160]}")

    if not args.apply:
        print("\ndry run: nothing written (re-run with --apply)")
        return 0
    touched = {container_of[sid].bot_id: container_of[sid] for sid in changed}
    for container in touched.values():
        store.save(container)
    print(f"\napplied: saved {len(touched)} meeting file(s); now POST /api/admin/backfill-signal-index")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
