"""Rebuild Orchestrator — regenerate a tenant's derived KB state (graph + vectors).

Derived state (Neo4j graph, vector embeddings) can drift from the authoritative
signal store as the extraction pipeline evolves. This orchestrator wipes the
tenant's derived state and regenerates it, in one of two tiers:

* ``signals`` — cheap replay: existing signals (store.load_all()) are written
  back to Neo4j, SUPERSEDES/CONFLICTS_WITH edges are reconstructed from
  persisted fields, and vectors re-embedded. ZERO LLM calls. Confirmed
  reviews and pending candidates survive byte-for-byte.
* ``source`` — full re-extraction: every meeting markdown in the corpus is
  replayed through the ingest pipeline (PROMOTE_SIGNALS → … → PERSIST) via
  ``IngestOrchestrator.process_observation``, preserving each meeting's
  bot_id so signal files overwrite in place. Produces NEW signal IDs —
  human-confirmed supersessions/conflicts reset to candidates.

Phases:
  WIPE_GRAPH → WIPE_VECTORS → REBUILD_ENTITIES → REPLAY → BACKFILL_EDGES
  → REINDEX_SIGNALS → EVALUATE_STATES → COMPLETE

Order constraints baked in:
  * entity nodes must exist before signal replay (MENTIONS/ASSIGNED_TO edges
    silently no-op on missing :Entity nodes)
  * replay is serial, chronological ascending (supersession/conflict
    detection compares each meeting against previously persisted ones)

Service-layer only (no HTTP): invoked by scripts/rebuild_kb.py today; a
hosted control-plane endpoint can wrap it later. All store/graph access is
resolved for the tenant that is current when ``process`` runs.
"""

from __future__ import annotations

import glob
import logging
import os
import time
import uuid
from typing import Any

from .base import BaseOrchestrator

logger = logging.getLogger(__name__)

PHASES = [
    "WIPE_GRAPH",
    "WIPE_VECTORS",
    "REBUILD_ENTITIES",
    "REPLAY",
    "BACKFILL_EDGES",
    "REINDEX_SIGNALS",
    "EVALUATE_STATES",
    "COMPLETE",
]

TIERS = ("signals", "source")


class RebuildOrchestrator(BaseOrchestrator):
    """Wipe and regenerate one tenant's derived graph + vector state."""

    def __init__(
        self,
        *,
        neo4j_client,
        knowledge_graph,
        semantica,
        signal_writer,
        signal_store,
        tenant_id: str,
        ingest_orchestrator=None,
    ):
        """
        Args:
            neo4j_client: client exposing execute_read/execute_write.
            knowledge_graph: Neo4jKnowledgeGraph (entity rebuild via build_graph).
            semantica: SemanticaKnowledge facade or None.
            signal_writer: SignalGraphWriter.
            signal_store: store with load_all() (file-based or Postgres-backed).
            tenant_id: tenant whose derived state is rebuilt. The caller must
                have already set the ambient tenant context to match.
            ingest_orchestrator: IngestOrchestrator — required for tier="source".
        """
        super().__init__()
        self._client = neo4j_client
        self._kg = knowledge_graph
        self._semantica = semantica
        self._writer = signal_writer
        self._store = signal_store
        self._tenant_id = tenant_id
        self._ingest = ingest_orchestrator

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def process(
        self,
        tier: str,
        job_id: str,
        job_store: dict[str, Any],
        *,
        dry_run: bool = False,
        signals_only_wipe: bool = False,
    ) -> dict[str, Any]:
        if tier not in TIERS:
            raise ValueError(f"Unknown rebuild tier {tier!r} — expected one of {TIERS}")
        if tier == "source":
            if self._ingest is None:
                raise ValueError("tier='source' requires an ingest_orchestrator")
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ValueError(
                    "tier='source' re-extracts signals via Claude — "
                    "ANTHROPIC_API_KEY must be set (the regex fallback would "
                    "silently degrade extraction quality)"
                )

        start_time = time.time()
        job_key = f"job:{job_id}"
        job_store[job_key] = {
            "job_id": job_id,
            "status": "running",
            "tier": tier,
            "tenant_id": self._tenant_id,
            "dry_run": dry_run,
            "phases_completed": [],
            "current_phase": None,
            "result": None,
            "error": None,
        }

        result: dict[str, Any] = {
            "status": "completed",
            "tier": tier,
            "tenant_id": self._tenant_id,
            "dry_run": dry_run,
        }

        try:
            result["wipe_graph"] = await self._run_phase(
                job_store,
                job_key,
                "WIPE_GRAPH",
                self._phase_wipe_graph,
                dry_run,
                signals_only_wipe,
            )
            result["wipe_vectors"] = await self._run_phase(
                job_store, job_key, "WIPE_VECTORS", self._phase_wipe_vectors, dry_run
            )
            result["rebuild_entities"] = await self._run_phase(
                job_store,
                job_key,
                "REBUILD_ENTITIES",
                self._phase_rebuild_entities,
                dry_run,
                tier,
            )
            result["replay"] = await self._run_phase(
                job_store, job_key, "REPLAY", self._phase_replay, tier, dry_run
            )
            result["backfill_edges"] = await self._run_phase(
                job_store,
                job_key,
                "BACKFILL_EDGES",
                self._phase_backfill_edges,
                tier,
                dry_run,
            )
            result["reindex_signals"] = await self._run_phase(
                job_store,
                job_key,
                "REINDEX_SIGNALS",
                self._phase_reindex_signals,
                tier,
                dry_run,
            )
            result["evaluate_states"] = await self._run_phase(
                job_store,
                job_key,
                "EVALUATE_STATES",
                self._phase_evaluate_states,
                dry_run,
            )
            self._advance_phase(job_store, job_key, "COMPLETE")

            # Source replay is best-effort per meeting (one bad meeting must
            # not abort a long rebuild after destructive wipes), but partial
            # failure must not read as a clean rebuild either.
            replay_failed = (result.get("replay") or {}).get("failed") or []
            if replay_failed:
                result["status"] = "degraded"
                result["error"] = (
                    f"replay failed for {len(replay_failed)} meeting(s): "
                    f"{replay_failed[:10]} — re-run 'rebuild --from source' "
                    "after fixing, or replay those meetings individually"
                )
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            job_store[job_key]["status"] = "failed"
            job_store[job_key]["error"] = str(e)
            await self._handle_orchestrator_error(
                "rebuild_pipeline", e, {"job_id": job_id, "tier": tier}
            )
            logger.exception("[REBUILD] Pipeline failed for job %s: %s", job_id, e)

        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        if result["status"] != "failed":
            job_store[job_key]["status"] = result["status"]
            job_store[job_key]["result"] = result
            if result["status"] == "degraded":
                job_store[job_key]["error"] = result.get("error")

        self._log_operation(
            "rebuild_complete",
            {
                "job_id": job_id,
                "tier": tier,
                "tenant_id": self._tenant_id,
                "status": result["status"],
                "dry_run": dry_run,
                "processing_time_ms": result["processing_time_ms"],
            },
        )
        return result

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    async def _phase_wipe_graph(
        self, dry_run: bool, signals_only: bool
    ) -> dict[str, Any]:
        from app.services.graph.factory import is_multi_tenant_graph_backend
        from app.services.graph.tenant_graph_wipe import (
            tenant_match_clause,
            wipe_tenant_graph,
        )

        if dry_run:
            label = ":Signal" if signals_only else ""
            match = tenant_match_clause(label, not is_multi_tenant_graph_backend())
            rows = await self._client.execute_read(
                match + "RETURN count(n) AS nodes",
                {"tenant_id": self._tenant_id},
            )
            return {
                "dry_run": True,
                "nodes_that_would_be_deleted": rows[0]["nodes"] if rows else 0,
            }
        return await wipe_tenant_graph(
            self._client, self._tenant_id, signals_only=signals_only
        )

    async def _phase_wipe_vectors(self, dry_run: bool) -> dict[str, Any]:
        from app.config import settings

        if dry_run:
            return {
                "dry_run": True,
                "backend": getattr(settings, "VECTOR_BACKEND", "faiss"),
            }
        from app.services.signal_indexing import reset_vector_index

        outcome = reset_vector_index()
        if outcome.get("error"):
            # Proceeding past a failed wipe would replay onto stale vectors
            # (and duplicate on FAISS) — fail the job before any replay.
            raise RuntimeError(f"vector index wipe failed: {outcome['error']}")
        return outcome

    async def _phase_rebuild_entities(
        self, dry_run: bool, tier: str = "signals"
    ) -> dict[str, Any]:
        """Rebuild entity nodes from corpus markdown BEFORE signal replay.

        Signal→entity edges (MENTIONS/ASSIGNED_TO/FOR_CLIENT) silently no-op
        when the target :Entity node is missing, so this must precede REPLAY.

        Source tier skips the stale-signal re-ingest inside build_graph: the
        replay re-extracts fresh signals whose ids won't match the persisted
        JSONs, so re-ingesting them first would leave orphan Signal nodes.
        """
        if dry_run:
            return {"dry_run": True}

        stats: dict[str, Any] = {}
        if self._kg is not None and hasattr(self._kg, "build_graph"):
            kg_stats = await self._kg.build_graph(
                force_rebuild=True, reingest_signals=(tier != "source")
            )
            stats["knowledge_graph"] = {
                k: v for k, v in (kg_stats or {}).items() if isinstance(v, (int, str))
            }
        if self._semantica is not None and hasattr(self._semantica, "build_graph"):
            try:
                await self._semantica.build_graph(force_rebuild=True)
                stats["semantica"] = "rebuilt"
            except Exception as e:
                # Entity *vectors* are best-effort; signal replay only needs
                # the Neo4j entity nodes restored above.
                logger.warning("[REBUILD] semantica build_graph failed: %s", e)
                stats["semantica"] = f"failed: {e}"
        return stats

    async def _phase_replay(self, tier: str, dry_run: bool) -> dict[str, Any]:
        if tier == "signals":
            return await self._replay_signals(dry_run)
        return await self._replay_source(dry_run)

    async def _replay_signals(self, dry_run: bool) -> dict[str, Any]:
        """Write persisted signals back to Neo4j, chronological ascending."""
        meetings = sorted(self._store.load_all(), key=lambda ms: ms.extracted_at or "")
        if dry_run:
            return {
                "dry_run": True,
                "meetings": len(meetings),
                "signals": sum(len(ms.signals) for ms in meetings),
            }
        written = 0
        for ms in meetings:
            written += await self._writer.write_meeting_signals(ms)
        return {
            "meetings": len(meetings),
            "signals_written": written,
        }

    async def _replay_source(self, dry_run: bool) -> dict[str, Any]:
        """Re-extract every corpus meeting through the ingest pipeline, serially."""
        from app.models.observation import Observation

        meeting_files = sorted(glob.glob(self._meetings_glob()))
        observations: list[tuple[str, Any]] = []
        skipped: list[str] = []
        for path in meeting_files:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    obs = Observation.from_markdown(fh.read())
                observations.append((path, obs))
            except Exception as e:
                logger.warning("[REBUILD] Skipping unparseable meeting %s: %s", path, e)
                skipped.append(os.path.basename(path))

        # Chronological ascending — supersession/conflict detection compares
        # each meeting against previously persisted ones.
        observations.sort(key=lambda item: item[1].observed_at.isoformat())

        if dry_run:
            return {
                "dry_run": True,
                "meetings": len(observations),
                "skipped_unparseable": skipped,
            }

        processed = 0
        failed: list[str] = []
        for path, obs in observations:
            bot_id = obs.external_id or os.path.basename(path).removeprefix(
                "meeting-"
            ).removesuffix(".md")
            sub_job_id = f"rebuild-{uuid.uuid4().hex[:8]}"
            sub_result = await self._ingest.process_observation(
                obs, bot_id, sub_job_id, {}
            )
            if sub_result.get("status") == "completed":
                processed += 1
            else:
                failed.append(bot_id)
            logger.info(
                "[REBUILD] Re-extracted %s (%d/%d): %s",
                bot_id,
                processed + len(failed),
                len(observations),
                sub_result.get("status"),
            )
        return {
            "meetings": len(observations),
            "reextracted": processed,
            "failed": failed,
            "skipped_unparseable": skipped,
        }

    async def _phase_backfill_edges(self, tier: str, dry_run: bool) -> dict[str, Any]:
        """Reconstruct SUPERSEDES/CONFLICTS_WITH from persisted fields.

        Signals tier only — the source tier just re-ran live detection, which
        annotates fresh candidates instead.
        """
        if tier != "signals":
            return {"skipped": "source tier re-runs detection during replay"}
        from app.services.graph.signal_edge_backfill import (
            backfill_all_edges,
            collect_conflict_pairs,
            collect_supersedes_pairs,
        )

        if dry_run:
            return {
                "dry_run": True,
                "supersedes_pairs": len(collect_supersedes_pairs(self._store)),
                "conflict_pairs": len(collect_conflict_pairs(self._store)),
            }
        return await backfill_all_edges(self._store, self._writer)

    async def _phase_reindex_signals(self, tier: str, dry_run: bool) -> dict[str, Any]:
        """Re-embed all signals. Signals tier only — the source tier
        indexes-on-write during PERSIST (re-indexing would double-append on
        FAISS)."""
        if tier != "signals":
            return {"skipped": "source tier indexes on write"}
        if dry_run:
            return {"dry_run": True}
        from app.services.signal_indexing import backfill_signals

        total, indexed, skipped = backfill_signals(self._store)
        return {"total": total, "indexed": indexed, "skipped": skipped}

    async def _phase_evaluate_states(self, dry_run: bool) -> dict[str, Any]:
        """Regenerate decision-lifecycle artifacts against the rebuilt state.

        Best-effort: a failure here is reported but does not fail the job —
        the graph and vectors are already consistent at this point.
        """
        if dry_run:
            return {"dry_run": True}
        out: dict[str, Any] = {}
        try:
            from app.services.staleness_evaluator import run_staleness_evaluation

            out["staleness"] = await run_staleness_evaluation(
                store=self._store, commit=True
            )
        except Exception as e:
            logger.warning("[REBUILD] staleness evaluation failed: %s", e)
            out["staleness_error"] = str(e)
        try:
            from app.services.constitution import export_constitution

            out["constitution"] = await export_constitution(commit=True)
        except Exception as e:
            logger.warning("[REBUILD] constitution export failed: %s", e)
            out["constitution_error"] = str(e)
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _meetings_glob(self) -> str:
        from app.git_ops import git_ops

        return os.path.join(str(git_ops.repo_path), "meetings", "meeting-*.md")

    async def _run_phase(self, job_store, job_key, phase_name, phase_fn, *args):
        self._advance_phase(job_store, job_key, phase_name)
        result = await phase_fn(*args)
        job_store[job_key]["phases_completed"].append(phase_name)
        return result

    def _advance_phase(self, job_store, job_key, phase_name):
        job_store[job_key]["current_phase"] = phase_name
        if phase_name == "COMPLETE":
            job_store[job_key]["phases_completed"].append("COMPLETE")
            job_store[job_key]["current_phase"] = None
