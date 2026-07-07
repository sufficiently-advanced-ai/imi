"""
Ingest Orchestrator — Pipeline coordination for content ingestion (Issue #863).

Translates external transcripts into Observation objects, then feeds them
through the signal promotion pipeline (SignalPromoter → SignalStore →
SignalGraphWriter) so they appear as first-class observations in the signal
feed, meeting history, and domain graph.

Pipeline:
  CLASSIFY → BUILD_MEETING → PROMOTE_SIGNALS → DETECT_SUPERSESSION → DETECT_CONFLICTS → ENRICH_GRAPH → PERSIST → DELTA_REPORT → COMPLETE

Note: The phase name BUILD_MEETING is kept in the job-tracking API for
backwards compatibility; internally the phase now builds an Observation.
"""

import email.utils
import inspect
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.services.conflict_detector import find_conflict_candidates

from ..entity_utils import ensure_entity_id_format, is_valid_entity_name
from ..graph.factory import get_semantica_knowledge
from ..ingest_classifier import compute_content_hash
from .base import BaseOrchestrator

logger = logging.getLogger(__name__)

# First "Date:" header line in ingested content. For email_thread content the
# sender's message carries a Date: header (echoed into the transcript body);
# the first match is the top/most-recent message — i.e. the real send date.
_CONTENT_DATE_RE = re.compile(r"^Date:\s*(.+?)\s*$", re.MULTILINE)


def _parse_content_timestamp(content: str) -> datetime | None:
    """Recover the source timestamp from a ``Date:`` header echoed into content.

    Without this, the ingest pipeline stamps ingest time into the observation
    (and thus every signal's source_timestamp/valid_from + the meeting
    start_time), clustering all migrated content in the import window. The true
    date survives in the body's first ``Date:`` line.

    Tries ISO-8601 first (the form bulk-migrated transcripts use), then RFC 2822
    (real email headers, e.g. "Wed, 31 Mar 2026 16:21:13 -0400"). Returns a
    tz-aware UTC datetime, or None when no parseable Date: line is present.
    """
    if not content:
        return None
    for m in _CONTENT_DATE_RE.finditer(content):
        raw = m.group(1).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
        try:
            dt = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            dt = None
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


PHASES = [
    "CLASSIFY",
    "BUILD_MEETING",
    "EXTRACT_ENTITIES",
    "PROMOTE_SIGNALS",
    "DETECT_SUPERSESSION",
    "DETECT_CONFLICTS",
    "ENRICH_GRAPH",
    "PERSIST",
    "ENRICH_PROFILES",
    "DELTA_REPORT",
    "COMPLETE",
]


class IngestOrchestrator(BaseOrchestrator):
    """Orchestrates the content ingestion pipeline.

    Converts external content into Observation objects and processes them
    through the signal promotion pipeline.
    """

    def __init__(
        self,
        classifier,
        claude_client,
        graph,
        signal_writer,
        git_ops,
        tools: dict[str, Any] | None = None,
        event_emitter: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        super().__init__()
        self._classifier = classifier
        self._claude = claude_client
        self._graph = graph
        self._signal_writer = signal_writer
        self._git_ops = git_ops
        self._tools = tools or {}
        self._event_emitter = event_emitter

    async def _emit(self, event_type: str, data: dict) -> None:
        """Emit an event to the injected emitter; swallows all exceptions."""
        if self._event_emitter is None:
            return
        try:
            await self._event_emitter(event_type, data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[INGEST] _emit swallowed emitter exception: %s", exc)

    async def process(
        self,
        request,
        job_id: str,
        job_store: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the full ingestion pipeline.

        Args:
            request: IngestRequest with content and optional hints
            job_id: Unique job identifier
            job_store: Shared dict for status tracking

        Returns:
            Result dict with signal counts, content_hash, processing_time_ms
        """
        start_time = time.time()
        job_key = f"job:{job_id}"
        content_hash = compute_content_hash(request.content)
        bot_id = f"ingest-{content_hash[:12]}"

        # Initialize job tracking
        job_store[job_key] = {
            "job_id": job_id,
            "status": "running",
            "content_type": None,
            "phases_completed": [],
            "current_phase": None,
            "result": None,
            "error": None,
        }

        result = {
            "status": "completed",
            "entities_extracted": 0,
            "relationships_created": 0,
            "decisions_found": 0,
            "insights_generated": 0,
            "graph_nodes_created": [],
            "content_hash": content_hash,
            "processing_time_ms": 0,
        }

        try:
            # Phase 1: CLASSIFY
            content_type = await self._run_phase(
                job_store, job_key, "CLASSIFY", self._phase_classify, request
            )
            job_store[job_key]["content_type"] = content_type

            # Phase 2: BUILD_MEETING — build an Observation from the ingested content
            observation = await self._run_phase(
                job_store,
                job_key,
                "BUILD_MEETING",
                self._phase_build_observation,
                request,
                bot_id,
                content_type,
            )

            # Phases 3-9 — shared with process_observation() (rebuild replay)
            await self._run_observation_phases(
                observation,
                bot_id,
                job_id,
                job_store,
                content=request.content,
                result=result,
            )

        except Exception as e:
            result["status"] = "failed"
            job_store[job_key]["status"] = "failed"
            job_store[job_key]["error"] = str(e)
            await self._handle_orchestrator_error(
                "ingest_pipeline", e, {"job_id": job_id}
            )
            logger.exception(f"[INGEST] Pipeline failed for job {job_id}: {e}")
            await self._emit("ingest_failed", {"error": str(e)})

        # Finalize
        elapsed_ms = int((time.time() - start_time) * 1000)
        result["processing_time_ms"] = elapsed_ms

        if result["status"] != "failed":
            job_store[job_key]["status"] = result["status"]
            job_store[job_key]["result"] = result
            # Emit terminal success event with counts-level summary
            _counts = {
                k: v
                for k, v in result.items()
                if k
                not in (
                    "status",
                    "content_hash",
                    "processing_time_ms",
                    "graph_nodes_created",
                    "delta_report",
                )
            }
            await self._emit("ingest_complete", {"result": _counts})

        self._log_operation(
            "ingest_complete",
            {
                "job_id": job_id,
                "bot_id": bot_id,
                "content_type": job_store[job_key].get("content_type"),
                "status": result["status"],
                "processing_time_ms": elapsed_ms,
                "decisions": result["decisions_found"],
                "entities": result["entities_extracted"],
            },
        )

        return result

    async def _run_observation_phases(
        self,
        observation,
        bot_id: str,
        job_id: str,
        job_store: dict[str, Any],
        *,
        content: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Run phases 3-9 (PROMOTE_SIGNALS → COMPLETE) for a built Observation.

        Mutates and returns ``result``. Shared by ``process()`` (normal ingest)
        and ``process_observation()`` (rebuild replay of an existing meeting).
        """
        job_key = f"job:{job_id}"

        # Phase 3: EXTRACT_ENTITIES — salience-aware extraction re-derives
        # entities_mentioned from raw content. Lives here (not BUILD_MEETING)
        # so the rebuild replay path (process_observation) re-extracts with
        # the current prompt instead of trusting stale frontmatter.
        await self._run_phase(
            job_store,
            job_key,
            "EXTRACT_ENTITIES",
            self._phase_extract_entities,
            observation,
        )

        # Phase: PROMOTE_SIGNALS — extract signals via SignalPromoter
        meeting_signals = await self._run_phase(
            job_store,
            job_key,
            "PROMOTE_SIGNALS",
            self._phase_promote_signals,
            observation,
        )

        # Phase 4: DETECT_SUPERSESSION — annotate new decision signals with candidates
        candidate_count = await self._run_phase(
            job_store,
            job_key,
            "DETECT_SUPERSESSION",
            self._phase_detect_supersession,
            meeting_signals,
        )
        if candidate_count:
            result["supersession_candidates"] = candidate_count

        # Phase 5: DETECT_CONFLICTS — annotate new decision signals with conflict candidates
        conflict_count = await self._run_phase(
            job_store,
            job_key,
            "DETECT_CONFLICTS",
            self._phase_detect_conflicts,
            meeting_signals,
        )
        if conflict_count:
            result["conflict_candidates"] = conflict_count

        # Phase 6: ENRICH_GRAPH — write signals to Neo4j + infer entity relationships
        graph_result = await self._run_phase(
            job_store,
            job_key,
            "ENRICH_GRAPH",
            self._phase_enrich_graph,
            meeting_signals,
            content,
            observation,
        )

        # Phase 7: PERSIST — save observation markdown + signals JSON to git
        await self._run_phase(
            job_store,
            job_key,
            "PERSIST",
            self._phase_persist,
            observation,
            meeting_signals,
            bot_id,
        )

        # Phase: ENRICH_PROFILES — grounded entity narratives + signal-derived
        # stats. After PERSIST so the meeting markdown (used as the rich-profile
        # trigger file) already exists on disk.
        profile_result = await self._run_phase(
            job_store,
            job_key,
            "ENRICH_PROFILES",
            self._phase_enrich_profiles,
            observation,
            meeting_signals,
            bot_id,
        )
        if profile_result and profile_result.get("rich_profiles_generated"):
            result["profiles_generated"] = profile_result["rich_profiles_generated"]

        # Phase 8: DELTA_REPORT — build "what your brain learned" artifact.
        # Must run AFTER PERSIST so it reports what was actually persisted.
        _meeting_title = observation.title if observation else None
        await self._run_phase(
            job_store,
            job_key,
            "DELTA_REPORT",
            self._phase_delta_report,
            job_id,
            bot_id,
            _meeting_title,
            meeting_signals,
            result,
        )

        # Emit delta_report_ready — the report object is on self after the phase
        _delta = getattr(self, "_last_delta_report", None)
        if _delta is not None:
            await self._emit("delta_report_ready", {"summary": _delta.counts})

        # Phase 9: COMPLETE
        self._advance_phase(job_store, job_key, "COMPLETE")

        # Aggregate results
        if meeting_signals:
            for sig in meeting_signals.signals:
                if sig.type == "decision":
                    result["decisions_found"] += 1
                elif sig.type == "key_point":
                    result["insights_generated"] += 1
            result["entities_extracted"] = len(
                {e.id for sig in meeting_signals.signals for e in sig.entities}
            )
            result["graph_nodes_created"] = [
                f"signal-{s.id[:8]}" for s in meeting_signals.signals
            ]
        # relationships_created counts edge-writes only; signal_count is
        # tracked separately as graph_nodes_created above.
        result["relationships_created"] = graph_result.get("edge_count", 0)
        result["signals_written"] = graph_result.get("signal_count", 0)
        return result

    @staticmethod
    def _validate_bot_id(bot_id: str) -> str:
        """Reject bot_ids that could escape the persistence paths.

        bot_id lands in file paths (meetings/meeting-{bot_id}.md,
        signals/meeting-{bot_id}.json); ``../`` or separators would allow
        path traversal in git writes. Normal ids are ``ingest-<hex>``.
        """
        if not isinstance(bot_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", bot_id
        ):
            raise ValueError(
                f"Invalid bot_id {bot_id!r} — expected 1-128 chars of " "[A-Za-z0-9_-]"
            )
        return bot_id

    async def process_observation(
        self,
        observation,
        bot_id: str,
        job_id: str,
        job_store: dict[str, Any],
    ) -> dict[str, Any]:
        """Run phases 3-9 for a pre-built Observation, preserving its bot_id.

        Rebuild-replay entry point: skips CLASSIFY/BUILD_MEETING and the
        content-hash bot_id derivation, so re-extracting an existing meeting
        overwrites its signals JSON / markdown in place (linear git history)
        instead of minting a new meeting identity.
        """
        bot_id = self._validate_bot_id(bot_id)
        start_time = time.time()
        job_key = f"job:{job_id}"
        content = getattr(observation, "raw_content", None) or getattr(
            observation, "content", ""
        )

        job_store[job_key] = {
            "job_id": job_id,
            "status": "running",
            "content_type": getattr(observation, "content_type", None),
            "phases_completed": [],
            "current_phase": None,
            "result": None,
            "error": None,
        }

        result: dict[str, Any] = {
            "status": "completed",
            "entities_extracted": 0,
            "relationships_created": 0,
            "decisions_found": 0,
            "insights_generated": 0,
            "graph_nodes_created": [],
            "content_hash": compute_content_hash(content),
            "processing_time_ms": 0,
        }

        try:
            await self._run_observation_phases(
                observation, bot_id, job_id, job_store, content=content, result=result
            )
        except Exception as e:
            result["status"] = "failed"
            job_store[job_key]["status"] = "failed"
            job_store[job_key]["error"] = str(e)
            await self._handle_orchestrator_error(
                "ingest_replay", e, {"job_id": job_id, "bot_id": bot_id}
            )
            logger.exception(
                f"[INGEST] Replay pipeline failed for job {job_id} (bot {bot_id}): {e}"
            )

        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        if result["status"] != "failed":
            job_store[job_key]["status"] = result["status"]
            job_store[job_key]["result"] = result
        return result

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _phase_classify(self, request) -> str:
        """Phase 1: Classify content type."""
        source_hint = request.source.value if request.source else None
        return await self._classifier.classify(request.content, source_hint=source_hint)

    async def _phase_build_observation(self, request, bot_id: str, content_type: str):
        """Phase 2: Build an Observation from the ingested content.

        This translates the external transcript into an Observation so the
        downstream signal promotion pipeline is source-agnostic.
        """
        from app.models.observation import Observation

        now = datetime.now(UTC)
        # Resolve the observation timestamp from the real content date, not
        # ingest time: explicit request.timestamp wins, else the email Date:
        # header in the content, else fall back to now. This timestamp flows to
        # observed_at -> signal source_timestamp/valid_from and occurred_at ->
        # meeting start_time, so getting it right here keeps temporal queries,
        # supersession ordering, and retention honest.
        content_ts = None if request.timestamp else _parse_content_timestamp(request.content)
        observed_at = request.timestamp or content_ts or now
        if content_ts is not None:
            logger.info(
                "[INGEST] Recovered content date %s from Date: header (not ingest time)",
                content_ts.isoformat(),
            )
        elif request.timestamp is None:
            logger.info("[INGEST] No content Date: header; using ingest time %s", now.isoformat())
        title = request.title or f"Ingested {content_type}"
        participants = request.participants or []

        # Build entities_mentioned: start with participants, then enrich with
        # domain-aware NER so domain types (client, engagement, ...) are present
        # BEFORE signal promotion links entities onto signals.
        entities_mentioned: dict[str, list[str]] = {}
        if participants:
            entities_mentioned["person"] = list(participants)

        try:
            sk = get_semantica_knowledge()
            if sk:
                grouped = await sk.extract_entities_grouped(request.content)
                for entity_type, items in (grouped or {}).items():
                    names = [it.get("name") for it in items if it.get("name")]
                    if names:
                        existing = set(entities_mentioned.get(entity_type, []))
                        entities_mentioned[entity_type] = sorted(existing | set(names))
        except Exception as e:
            logger.warning(f"[INGEST] Domain entity extraction failed (non-fatal): {e}")

        # Build a markdown body with structure the SignalPromoter expects
        body = self._build_observation_body(title, request.content, participants)

        observation = Observation(
            observation_id=f"ingest-{uuid.uuid4().hex[:8]}",
            external_id=bot_id,
            source="ingest",
            observed_at=observed_at,
            entities_mentioned=entities_mentioned,
            content=body,
            raw_content=request.content,
            is_finalized=True,
            status="completed",
            title=title,
            occurred_at=observed_at,
            participants=participants,
            update_count=1,
        )

        logger.info(f"[INGEST] Built Observation: external_id={bot_id} title={title}")
        return observation

    @staticmethod
    def _build_observation_body(
        title: str, content: str, participants: list[str]
    ) -> str:
        """Build a markdown body that SignalPromoter can extract from."""
        parts = [f"# {title}", ""]

        if participants:
            parts.append("## Participants")
            parts.append("")
            for p in participants:
                parts.append(f"- {p}")
            parts.append("")

        parts.extend(
            [
                "## Discussion",
                "",
                content,
            ]
        )

        return "\n".join(parts)

    async def _phase_extract_entities(self, observation) -> int:
        """Phase: salience-aware entity extraction (extraction v2).

        Re-derives observation.entities_mentioned from the raw transcript via
        the transcript_entity_extract.xml prompt: the prompt labels salience
        (participant/subject/mention), code promotes participant+subject;
        passing mentions only link when they resolve to an existing entity.
        The full labeled list is preserved on observation.metadata for
        evals/debugging. On any failure the existing entities_mentioned is
        kept (regex/NER seed or legacy frontmatter).
        """
        if not self._claude:
            logger.info("[INGEST] No claude client; skipping salient entity extraction")
            return 0

        transcript = observation.raw_content or observation.content or ""
        if not transcript.strip():
            return 0

        try:
            from app.services.entity_resolver import EntityResolver
            from app.services.entity_utils import SYSTEM_ENTITY_TYPES, get_active_entity_types
            from app.services.salient_entity_extractor import (
                extract_salient_entities,
                filter_salient_entities,
                to_entities_mentioned,
            )

            entity_types = sorted(get_active_entity_types() - SYSTEM_ENTITY_TYPES)
            existing = self._existing_entity_context()
            extraction = await extract_salient_entities(
                self._claude, transcript, entity_types, existing
            )
            labeled = extraction["entities"]

            # Content-derived title for generic ingest titles ("Ingested
            # transcript", "Untitled Meeting") — the ingest-path counterpart
            # of the finalization title fix. Applied BEFORE the empty-entity
            # early return so entity-free meetings still get the better title.
            derived_title = extraction.get("meeting_title")
            current_title = (observation.title or "").strip()
            if derived_title and (
                not current_title
                or current_title.lower().startswith("ingested ")
                or current_title.lower() == "untitled meeting"
            ):
                observation.title = derived_title
                logger.info("[INGEST] Content-derived title: %r", derived_title)

            if not labeled:
                logger.warning(
                    "[INGEST] Salient extraction returned nothing; keeping "
                    "existing entities_mentioned"
                )
                return 0

            resolver = EntityResolver(knowledge_graph=self._graph)
            promoted = filter_salient_entities(labeled, resolver)
            mentioned = to_entities_mentioned(promoted)

            # Participants always count as person entities
            for name in observation.participants or []:
                people = mentioned.setdefault("person", [])
                if name not in people:
                    people.append(name)

            observation.entities_mentioned = mentioned
            observation.metadata["salient_entities"] = labeled
            logger.info(
                "[INGEST] Salient extraction: %d labeled, %d promoted (%s)",
                len(labeled),
                len(promoted),
                ", ".join(f"{t}:{len(n)}" for t, n in mentioned.items()),
            )
            return len(promoted)
        except Exception as e:
            logger.warning(
                "[INGEST] Salient entity extraction failed (non-fatal, keeping "
                "existing entities_mentioned): %s",
                e,
                exc_info=True,
            )
            return 0

    def _existing_entity_context(self) -> dict[str, list[str]] | None:
        """Names of existing graph entities, as prompt context for canonical
        name consistency. Capped to keep the prompt small."""
        if not self._graph or not getattr(self._graph, "nodes", None):
            return None
        context: dict[str, list[str]] = {}
        for node in self._graph.nodes.values():
            ntype = getattr(node, "type", None)
            name = getattr(node, "name", "")
            if not ntype or not name:
                continue
            names = context.setdefault(ntype, [])
            if len(names) < 40:
                names.append(name)
        return context or None

    async def _phase_promote_signals(self, observation):
        """Phase 3: Extract signals using the existing SignalPromoter."""
        try:
            from ..signal_promoter import SignalPromoter

            promoter = SignalPromoter(
                claude_client=self._claude,
                knowledge_graph=self._graph,
            )
            meeting_signals = await promoter.promote(observation)

            if meeting_signals:
                logger.info(
                    f"[INGEST] Promoted {meeting_signals.signal_count} signals "
                    f"for {observation.external_id}"
                )
            else:
                logger.info(
                    f"[INGEST] No signals extracted for {observation.external_id}"
                )

            return meeting_signals

        except Exception as e:
            logger.warning(f"[INGEST] Signal promotion failed (non-fatal): {e}")
            return None

    async def _phase_enrich_graph(
        self, meeting_signals, content: str, observation
    ) -> dict[str, int]:
        """Phase 4: Write signals to Neo4j AND infer entity-to-entity relationships.

        Three steps (order matters — entity nodes must exist before signal edges
        reference them, otherwise MENTIONS/ASSIGNED_TO/FOR_CLIENT silently no-op):
        1. Create entity nodes so the graph has all referenced entities.
        2. Write Signal nodes via SignalGraphWriter — edges resolve because nodes exist.
        3. Infer entity relationships (WORKS_ON, LEADS, MEMBER_OF, etc.) via
           infer_relationships tool, then write edges to the knowledge graph.
        """
        result = {"signal_count": 0, "edge_count": 0}

        # Step 1 (A): Extract entities + create graph nodes first so that signal
        # edges written in Step 2 can resolve to existing nodes.
        entities = []
        if self._graph:
            # Full entity list: people from signals + the salience-filtered
            # entities_mentioned produced by the EXTRACT_ENTITIES phase.
            # (The old extract_entities supplement re-added unfiltered
            # passing mentions; salient extraction replaces it.)
            entities = self._collect_entities(meeting_signals, observation)

            # Drop entities whose type isn't in the active domain (org-centric
            # extraction / NER can emit person/project/team/concept/date types).
            entities = self._filter_to_domain_entities(entities)

            # Resolve surface forms against existing graph entities so name
            # variants ("Nation Swell" vs "Nationswell") attach to the
            # existing node instead of minting a duplicate slug. The id map
            # is pushed back into the signal EntityRefs so MENTIONS edges
            # land on the resolved nodes.
            entities, id_map = self._resolve_collected_entities(entities)
            if id_map and meeting_signals:
                self._remap_signal_entity_ids(meeting_signals, id_map)

            logger.info(
                f"[INGEST] Collected {len(entities)} domain entities for relationship inference"
            )

            # Ensure all entities exist as graph nodes (projects/teams may not
            # exist yet). add_node is MERGE-based and will roll back the Neo4j
            # write if the file persistence fails — that's deliberate, it
            # prevents the orphan-entity class of bugs. Log every failure so
            # we can spot bad LLM extraction (e.g. slug-IDs being passed as
            # display names, which produces doubled-prefix orphans). Continue
            # on individual failures so a single bad entity doesn't drop the
            # whole batch's relationship inference.
            add_failures = 0
            for entity in entities:
                eid = entity.get("id", "")
                etype = entity.get("type", "unknown")
                ename = entity.get("name", "")
                if eid and ename:
                    try:
                        await self._graph.add_node(
                            entity_type=etype,
                            name=ename,
                            entity_id=eid,
                            properties={"source": "ingest"},
                        )
                    except Exception as e:
                        add_failures += 1
                        logger.warning(
                            "[INGEST] add_node failed for %s (type=%s, name=%r): %s",
                            eid,
                            etype,
                            ename,
                            e,
                        )
            if add_failures:
                logger.warning(
                    "[INGEST] %d/%d add_node calls failed in this batch",
                    add_failures,
                    len(entities),
                )

        # Step 2 (B): Write signal nodes to Neo4j — entity nodes now exist so
        # MENTIONS/ASSIGNED_TO/FOR_CLIENT edges will resolve correctly.
        if meeting_signals and self._signal_writer:
            try:
                count = await self._signal_writer.write_meeting_signals(meeting_signals)
                result["signal_count"] = count
                logger.info(f"[INGEST] Wrote {count} signal nodes to Neo4j")
            except Exception as e:
                logger.warning(
                    "[INGEST] Neo4j signal write failed (non-fatal): %s",
                    e,
                    exc_info=True,
                )

        # Step 3 (C): Infer entity-to-entity relationships and write edges.
        if self._graph and len(entities) >= 2:
            relationships = await self._infer_relationships(content, entities)
            logger.info(f"[INGEST] Inferred {len(relationships)} relationships")
            result["edge_count"] = await self._write_relationship_edges(relationships)

            if result["edge_count"] > 0:
                logger.info(
                    f"[INGEST] Wrote {result['edge_count']} entity relationship edges"
                )

        return result

    def _resolve_collected_entities(
        self, entities: list[dict]
    ) -> tuple[list[dict], dict[str, str]]:
        """Resolve each (type, name) against existing graph entities.

        Returns the entities with canonical ids/names plus an old_id->new_id
        map for every entity whose id changed. Resolution never crosses
        types; unresolvable entries keep their original id.
        """
        try:
            from app.services.entity_resolver import EntityResolver
        except Exception:  # pragma: no cover - packaging error
            return entities, {}

        resolver = EntityResolver(knowledge_graph=self._graph)
        id_map: dict[str, str] = {}
        resolved_entities: list[dict] = []
        seen_ids: set[str] = set()
        for entity in entities:
            etype, ename, eid = (
                entity.get("type", ""),
                entity.get("name", ""),
                entity.get("id", ""),
            )
            if not etype or not ename:
                resolved_entities.append(entity)
                continue
            try:
                resolved = resolver.resolve(etype, ename)
            except Exception as e:
                logger.warning(
                    "[INGEST] Entity resolution failed for %s/%r: %s", etype, ename, e
                )
                resolved_entities.append(entity)
                continue
            new_id = resolved.id or eid
            if eid and new_id != eid:
                id_map[eid] = new_id
                logger.info(
                    "[INGEST] Resolved entity %r (%s): %s -> %s (%s)",
                    ename,
                    etype,
                    eid,
                    new_id,
                    resolved.matched_via,
                )
            if new_id in seen_ids:
                continue  # two surface forms resolved to the same entity
            seen_ids.add(new_id)
            updated = dict(entity)
            updated["id"] = new_id
            if resolved.matched_via != "new" and resolved.canonical_name:
                updated["name"] = resolved.canonical_name
            resolved_entities.append(updated)
        return resolved_entities, id_map

    @staticmethod
    def _remap_signal_entity_ids(meeting_signals, id_map: dict[str, str]) -> None:
        """Rewrite signal EntityRef ids that were remapped by resolution."""
        for sig in meeting_signals.signals:
            for ref in sig.entities:
                if ref.id in id_map:
                    ref.id = id_map[ref.id]
            if sig.owner and sig.owner.id in id_map:
                sig.owner.id = id_map[sig.owner.id]
            if sig.client_id and sig.client_id in id_map:
                sig.client_id = id_map[sig.client_id]

    @staticmethod
    def _collect_entities(meeting_signals, observation) -> list[dict]:
        """Collect unique entities from signals and observation."""
        entities = {}

        # From signals (have structured EntityRef objects)
        if meeting_signals:
            for sig in meeting_signals.signals:
                for e in sig.entities:
                    if e.id not in entities:
                        entities[e.id] = {"id": e.id, "name": e.name, "type": e.type}
                if sig.owner and sig.owner.id not in entities:
                    entities[sig.owner.id] = {
                        "id": sig.owner.id,
                        "name": sig.owner.name,
                        "type": sig.owner.type,
                    }

        # From observation participants
        if observation and observation.participants:
            for name in observation.participants:
                pid = ensure_entity_id_format("person", name)
                if pid not in entities:
                    entities[pid] = {"id": pid, "name": name, "type": "person"}

        # From observation entities_mentioned (type -> list of names)
        if observation and observation.entities_mentioned:
            for entity_type, names in observation.entities_mentioned.items():
                for name in names or []:
                    eid = ensure_entity_id_format(entity_type, name)
                    if eid not in entities:
                        entities[eid] = {"id": eid, "name": name, "type": entity_type}

        return list(entities.values())

    @staticmethod
    def _entity_type_from_id(entity_id: str) -> str:
        """Entity IDs are '{type}-{slug}'; return the type prefix."""
        return entity_id.split("-", 1)[0] if "-" in entity_id else entity_id

    @staticmethod
    def _filter_to_domain_entities(
        entities: list[dict], valid_types: set[str] | None = None
    ) -> list[dict]:
        """Keep only entities whose type is a valid entity type in the active domain.

        The org-centric extract_entities tool and NER emit person/project/team/
        concept/date/event entities that aren't part of every domain. They fail
        add_node and cannot map to any domain relationship, so they pollute
        relationship inference. Filtering to domain types fixes both.
        """
        if valid_types is None:
            try:
                from ...core.domain_config.domain_config_service import (
                    get_domain_config_service,
                )

                domain = get_domain_config_service().get_active_domain()
                valid_types = (
                    set(domain.entities.keys())
                    if (domain and domain.entities)
                    else None
                )
            except Exception as e:
                logger.warning(
                    "[INGEST] Failed to load active domain for entity filtering; "
                    "allowing all entity types: %s",
                    e,
                    exc_info=True,
                )
                valid_types = None
        # Type filter (org-centric extraction / NER emit out-of-domain types).
        if valid_types:
            candidates = [e for e in entities if e.get("type") in valid_types]
        else:
            candidates = list(entities)

        # Name filter (defense-in-depth): drop extraction-junk names — phone
        # numbers, newline-contaminated fragments — before they become graph
        # nodes + stub files. Shares is_valid_entity_name with the extractor so
        # entities arriving via signals (not just salient extraction) are gated.
        kept = [e for e in candidates if is_valid_entity_name(e.get("name", ""))]
        dropped = len(candidates) - len(kept)
        if dropped:
            logger.info(
                "[INGEST] Dropped %d junk-named entit%s before add_node",
                dropped,
                "y" if dropped == 1 else "ies",
            )
        return kept

    @staticmethod
    def _is_schema_valid_relationship(
        rel_type: str, source_id: str, target_id: str, domain
    ) -> bool:
        """Whether (type, source-entity-type, target-entity-type) is defined
        in the active domain schema (forward or inverse direction)."""
        if not domain or not domain.entities:
            return False
        rt = rel_type.lower()
        st = IngestOrchestrator._entity_type_from_id(source_id)
        tt = IngestOrchestrator._entity_type_from_id(target_id)
        ent = domain.entities.get(st)
        if ent:
            for rel in ent.relationships:
                if rel.type == rt and rel.target == tt:
                    return True
        ent = domain.entities.get(tt)
        if ent:
            for rel in ent.relationships:
                if rel.inverse_name == rt and rel.target == st:
                    return True
        return False

    @staticmethod
    def _resolve_domain_relationship(source_id: str, target_id: str, domain):
        """Pick a valid relationship from the active domain schema based on the
        source/target entity types. Returns (from_id, to_id, rel_type) or None.

        Tries the forward direction (source_type -> target_type); if the domain
        only defines the reverse, flips the edge so it's still schema-valid.
        """
        if not domain or not domain.entities:
            return None
        st = IngestOrchestrator._entity_type_from_id(source_id)
        tt = IngestOrchestrator._entity_type_from_id(target_id)
        ent = domain.entities.get(st)
        if ent:
            for rel in ent.relationships:
                if rel.target == tt:
                    return (source_id, target_id, rel.type)
        ent = domain.entities.get(tt)
        if ent:
            for rel in ent.relationships:
                if rel.target == st:
                    return (target_id, source_id, rel.type)
        return None

    async def _write_relationship_edges(self, relationships: list[dict]) -> int:
        """Write entity-to-entity edges via create_semantic_relationship.

        Resolves relationship types from the active domain schema by entity type
        so they pass validation and appear in the domain graph visualization.
        """
        domain = None
        try:
            from ...core.domain_config.domain_config_service import (
                get_domain_config_service,
            )

            domain = get_domain_config_service().get_active_domain()
        except Exception as e:
            logger.warning(
                "[INGEST] Failed to load active domain config; relationship edge "
                "resolution disabled: %s",
                e,
                exc_info=True,
            )

        count = 0
        for rel in relationships:
            source = rel.get("source", "")
            target = rel.get("target", "")
            if not source or not target:
                continue
            # Trust the LLM's typed triple when it is schema-valid — the tool
            # (InferRelationshipsTool) already validated type signatures
            # against the domain config. Falling back to
            # _resolve_domain_relationship (which substitutes the FIRST
            # type-pair match and silently rewrote reports_to into
            # collaborates_with) only for legacy untyped output.
            llm_type = (rel.get("type") or "").strip()
            if llm_type and domain and self._is_schema_valid_relationship(
                llm_type, source, target, domain
            ):
                from_id, to_id, domain_type = source, target, llm_type.lower()
            else:
                resolved = self._resolve_domain_relationship(source, target, domain)
                if not resolved:
                    logger.debug(
                        "[INGEST] No domain relationship for %s -> %s; skipping",
                        source,
                        target,
                    )
                    continue
                from_id, to_id, domain_type = resolved
            desc = rel.get("description", "")
            evidence = rel.get("evidence", "")
            raw_type = rel.get("type", "")
            try:
                await self._graph.create_semantic_relationship(
                    from_entity_id=from_id,
                    to_entity_id=to_id,
                    relationship_type=domain_type,
                    strength=0.8,
                    evidence=evidence or desc or "Inferred from ingested content",
                    reasoning=f"Inferred ({raw_type}): {desc}"
                    if desc
                    else f"Domain relationship: {domain_type}",
                    source="ingest",
                )
                count += 1
            except Exception as e:
                logger.warning(
                    "[INGEST] Edge write failed %s→%s→%s: %s",
                    from_id,
                    domain_type,
                    to_id,
                    e,
                    exc_info=True,
                )

        if count > 0:
            logger.info(f"[INGEST] Wrote {count} entity relationship edges")
            # Invalidate domain graph cache so new edges appear immediately
            try:
                from ...services.graph.factory import invalidate_graph_response_cache

                invalidate_graph_response_cache()
            except Exception:
                pass
        return count

    async def _extract_entities_from_content(self, content: str) -> list[dict]:
        """Use extract_entities tool to get people, projects, and teams."""
        tool = self._tools.get("extract_entities")
        if not tool:
            return []

        try:
            result = tool.execute({"content": content})
            if inspect.isawaitable(result):
                result = await result
            if not result.success:
                return []

            entities = []
            _singular = {"people": "person", "projects": "project", "teams": "team"}
            data = result.data.get("entities", result.data)
            for category in ("people", "projects", "teams"):
                for item in data.get(category, []):
                    if isinstance(item, dict) and item.get("id"):
                        if "type" not in item:
                            item["type"] = _singular[category]
                        entities.append(item)
            return entities
        except Exception as e:
            logger.warning(f"[INGEST] extract_entities exception: {e}")
            return []

    async def _infer_relationships(
        self, content: str, entities: list[dict]
    ) -> list[dict]:
        """Use the infer_relationships tool to discover entity-to-entity edges."""
        tool = self._tools.get("infer_relationships")
        if not tool:
            return []

        try:
            result = tool.execute({"content": content, "entities": entities})
            if inspect.isawaitable(result):
                result = await result
            if result.success:
                return result.data.get("relationships", [])
            logger.warning(f"[INGEST] infer_relationships failed: {result.error}")
        except Exception as e:
            logger.warning(f"[INGEST] infer_relationships exception: {e}")

        return []

    async def _phase_detect_supersession(self, meeting_signals, store=None) -> int:
        """Phase DETECT_SUPERSESSION: annotate new decision signals with candidates.

        Loads all standing signals from the SignalStore and finds signals that
        each new decision signal may supersede.  Candidates are attached as
        ``signal.metadata["supersession_candidates"]`` (only when non-empty so
        downstream readers can use ``"supersession_candidates" in metadata`` as
        a presence check).

        Failure is intentionally non-fatal: any exception is logged and the
        pipeline continues without annotations.

        Args:
            meeting_signals: MeetingSignals from PROMOTE_SIGNALS (may be None).
            store: Optional SignalStore override (for testing).  When None the
                module-level ``signal_store`` proxy is used.

        Returns:
            Number of candidate relationships found across all new signals.
        """
        if not meeting_signals:
            return 0

        from app.services.signal_store import signal_store as _signal_store
        from app.services.supersession_candidates import find_supersession_candidates

        active_store = store if store is not None else _signal_store

        try:
            all_batches = active_store.load_all()
        except Exception as e:
            logger.warning(
                "[INGEST] DETECT_SUPERSESSION: load_all failed (non-fatal): %s", e
            )
            return 0

        # Flatten all standing signals
        standing: list = [sig for batch in all_batches for sig in batch.signals]

        total_candidates = 0
        for sig in meeting_signals.signals:
            if sig.type != "decision":
                continue
            try:
                candidates = find_supersession_candidates(sig, standing)
                if candidates:
                    sig.metadata["supersession_candidates"] = candidates
                    total_candidates += len(candidates)
            except Exception as e:
                logger.warning(
                    "[INGEST] DETECT_SUPERSESSION: candidate matching failed for "
                    "signal %s (non-fatal): %s",
                    sig.id,
                    e,
                )

        if total_candidates > 0:
            logger.info(
                "[INGEST] DETECT_SUPERSESSION: annotated %d candidate relationships",
                total_candidates,
            )
        return total_candidates

    async def _phase_detect_conflicts(self, meeting_signals, store=None) -> int:
        """Phase DETECT_CONFLICTS: annotate new decision signals with conflict candidates.

        Loads all standing signals from the SignalStore and uses the LLM-based
        conflict detector to find signals that semantically contradict each new
        decision.  Candidates are attached as
        ``signal.metadata["conflict_candidates"]`` (only when non-empty, so
        downstream readers can use ``"conflict_candidates" in metadata`` as a
        presence check).

        Skips gracefully (log info) when ``settings.ANTHROPIC_API_KEY`` is
        falsy (no LLM available).

        Failure is intentionally non-fatal: any exception is logged and the
        pipeline continues without annotations.

        Args:
            meeting_signals: MeetingSignals from PROMOTE_SIGNALS (may be None).
            store: Optional SignalStore override (for testing).  When None the
                module-level ``signal_store`` proxy is used.

        Returns:
            Number of conflict candidate relationships found across all new signals.
        """
        if not meeting_signals:
            return 0

        if not settings.ANTHROPIC_API_KEY:
            logger.info(
                "[INGEST] DETECT_CONFLICTS: skipping — ANTHROPIC_API_KEY not set"
            )
            return 0

        from app.services.signal_store import signal_store as _signal_store

        active_store = store if store is not None else _signal_store

        try:
            all_batches = active_store.load_all()
        except Exception as e:
            logger.warning(
                "[INGEST] DETECT_CONFLICTS: load_all failed (non-fatal): %s", e
            )
            return 0

        # Flatten all standing signals
        standing: list = [sig for batch in all_batches for sig in batch.signals]

        total_candidates = 0
        for sig in meeting_signals.signals:
            if sig.type != "decision":
                continue
            try:
                candidates = await find_conflict_candidates(sig, standing)
                if candidates:
                    sig.metadata["conflict_candidates"] = candidates
                    total_candidates += len(candidates)
            except Exception as e:
                logger.warning(
                    "[INGEST] DETECT_CONFLICTS: candidate matching failed for "
                    "signal %s (non-fatal): %s",
                    sig.id,
                    e,
                )

        if total_candidates > 0:
            logger.info(
                "[INGEST] DETECT_CONFLICTS: annotated %d conflict candidates",
                total_candidates,
            )
        return total_candidates

    async def _phase_delta_report(
        self,
        job_id: str,
        bot_id: str,
        meeting_title: str | None,
        meeting_signals,
        result: dict,
    ) -> None:
        """Phase DELTA_REPORT: build and persist the 'what your brain learned' artifact.

        Builds a DeltaReport from the promoted signals, renders it as markdown,
        commits it to ``deltas/delta-{bot_id}.md`` (non-fatal if git unavailable),
        and stores ``report.model_dump()`` under ``result["delta_report"]``.

        The report object is also stored on ``self._last_delta_report`` as a
        clean seam for Task 20's SSE emitter — no need to deserialise from the
        result dict.
        """
        from datetime import UTC, datetime

        from ..delta_report import build_delta_report, render_delta_markdown

        generated_at = datetime.now(UTC).isoformat()

        report = build_delta_report(
            job_id,
            bot_id,
            meeting_title,
            meeting_signals,
            generated_at=generated_at,
        )

        # Expose for Task 20 SSE emitter
        self._last_delta_report = report

        # Store in result dict so GET /api/ingest/{id}/delta can surface it
        result["delta_report"] = report.model_dump()

        # Commit markdown artifact to git (non-fatal)
        if self._git_ops and hasattr(self._git_ops, "commit_file"):
            markdown = render_delta_markdown(report)
            delta_path = f"deltas/delta-{bot_id}.md"
            commit_msg = f"[delta] What your brain learned: {meeting_title or bot_id}"
            try:
                await self._git_ops.commit_file(delta_path, markdown, commit_msg)
                logger.info("[INGEST] Delta report committed to %s", delta_path)
            except Exception as exc:
                logger.warning(
                    "[INGEST] Delta report git commit failed (non-fatal): %s", exc
                )

    async def _phase_persist(self, observation, meeting_signals, bot_id: str) -> None:
        """Phase 5: Persist observation markdown + signals JSON to the git repo.

        Writes two files:
        1. meetings/meeting-{bot_id}.md — the observation as markdown (same
           filename convention as other ingested meetings, so list_meetings and
           get_meeting_transcript discover ingested observations too)
        2. signals/meeting-{bot_id}.json — signal data for the signal feed

        Both use git_ops.commit_file() for write + git add + commit + push.
        """
        try:
            if not self._git_ops or not hasattr(self._git_ops, "commit_file"):
                logger.info("[INGEST] Git ops not available, skipping persist phase")
                return

            # 1. Persist observation as markdown. Use the `meeting-` prefix so the
            # same readers that serve live meetings (list_meetings globs
            # `meeting-*.md`; get_meeting_transcript reads `meeting-{bot_id}.md`)
            # also surface ingested observations — otherwise they're invisible.
            meeting_path = f"meetings/meeting-{bot_id}.md"
            meeting_content = observation.to_markdown()
            await self._git_ops.commit_file(
                meeting_path,
                meeting_content,
                f"[ingest] Add meeting: {observation.title or bot_id}",
            )
            logger.info(f"[INGEST] Persisted observation to {meeting_path}")

            # 2. Persist signals JSON (for signal feed)
            if meeting_signals and meeting_signals.signal_count > 0:
                signals_path = f"signals/meeting-{bot_id}.json"
                signals_content = meeting_signals.model_dump_json(indent=2)
                await self._git_ops.commit_file(
                    signals_path,
                    signals_content,
                    f"[ingest] Extract {meeting_signals.signal_count} signals from {bot_id}",
                )
                logger.info(
                    f"[INGEST] Persisted {meeting_signals.signal_count} signals to {signals_path}"
                )

        except Exception as e:
            logger.warning(f"[INGEST] Persist phase failed (non-fatal): {e}")

    async def _phase_enrich_profiles(
        self, observation, meeting_signals, bot_id: str
    ) -> dict[str, int]:
        """Phase ENRICH_PROFILES: generate grounded entity narratives + stats.

        The ingest pipeline otherwise leaves every entity with a stub body
        ("# Name\\n\\nEntity ID: ...") and no signal grounding. This phase reuses
        the SAME chain the live-meeting path uses:

          1. Save MeetingSignals to the canonical signal_store (ingest only
             wrote git JSON before, so the enricher's signal lookup found
             nothing). Keyed by bot_id.
          2. EntityMeetingEnricher.update_entity_files_with_meeting writes each
             entity's "## Recent Signals" section, persists typed relationships,
             generates grounded rich profiles, and rebuilds the graph (which
             also refreshes the in-memory edges the profile endpoint reads).

        Independent of GIT_REPO_URL. Best-effort: any failure is logged and the
        ingest job still completes.
        """
        result = {"rich_profiles_generated": 0}
        entities_mentioned = getattr(observation, "entities_mentioned", None) or {}
        if not entities_mentioned:
            logger.info("[INGEST] ENRICH_PROFILES: no entities_mentioned; skipping")
            return result

        try:
            # 1. Persist signals to the canonical store so the enricher's
            #    signal_store.load(bot_id) resolves.
            if meeting_signals and getattr(meeting_signals, "signal_count", 0) > 0:
                from app.services.signal_store import signal_store

                signal_store.save(meeting_signals)
                logger.info(
                    "[INGEST] ENRICH_PROFILES: saved %d signals to signal_store (%s)",
                    meeting_signals.signal_count,
                    bot_id,
                )

            # 2. Run the shared enrichment chain.
            from app.domain.entities.services import (
                EntityService,
                get_entity_repository,
            )
            from app.services.entity_meeting_enricher import EntityMeetingEnricher

            enricher = EntityMeetingEnricher(EntityService(), get_entity_repository())
            transcript = (
                getattr(observation, "raw_content", None)
                or getattr(observation, "content", None)
                or ""
            )
            # relationships intentionally empty: the ENRICH_GRAPH phase already
            # inferred and persisted typed entity-to-entity edges to Neo4j (via
            # _write_relationship_edges), so relationship_count / top_relationships
            # are populated from the graph. The enricher's _persist_meeting_
            # relationships expects a different shape ({entity1, entity2} names vs
            # our {source, target} ids), so re-passing them here would need an
            # id->name translation layer. Deferred: surfacing typed relationships
            # into entity-file frontmatter for the profile's "Key Relationships"
            # narrative section (follow-up).
            meeting_data = {
                "meeting_id": bot_id,
                "bot_id": bot_id,
                "entities": entities_mentioned,
                "transcript_excerpt": transcript[:5000],
                "relationships": [],
            }
            enrich_result = await enricher.update_entity_files_with_meeting(meeting_data)
            if isinstance(enrich_result, dict):
                result["rich_profiles_generated"] = enrich_result.get(
                    "rich_profiles_generated", 0
                )
            logger.info(
                "[INGEST] ENRICH_PROFILES: generated %d rich profiles",
                result["rich_profiles_generated"],
            )
        except Exception as e:
            logger.warning(
                "[INGEST] ENRICH_PROFILES failed (non-fatal): %s", e, exc_info=True
            )
        return result

    # ------------------------------------------------------------------
    # Phase lifecycle helpers
    # ------------------------------------------------------------------

    async def _run_phase(self, job_store, job_key, phase_name, phase_fn, *args):
        """Execute a phase, updating job store tracking."""
        self._advance_phase(job_store, job_key, phase_name)
        phases_so_far = list(job_store[job_key].get("phases_completed", []))
        await self._emit(
            "ingest_phase",
            {
                "phase": phase_name,
                "status": "started",
                "phases_completed": phases_so_far,
            },
        )
        result = await phase_fn(*args)
        job_store[job_key]["phases_completed"].append(phase_name)
        phases_now = list(job_store[job_key]["phases_completed"])
        await self._emit(
            "ingest_phase",
            {
                "phase": phase_name,
                "status": "completed",
                "phases_completed": phases_now,
            },
        )
        return result

    def _advance_phase(self, job_store, job_key, phase_name):
        """Mark a phase as current in the job store."""
        job_store[job_key]["current_phase"] = phase_name
        if phase_name == "COMPLETE":
            job_store[job_key]["phases_completed"].append("COMPLETE")
            job_store[job_key]["current_phase"] = None
