"""Entity Profile & Activity API routes - Issue #60"""

import asyncio
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..domain.entities.services import get_entity_repository
from ..models import (
    EntityActivity,
    EntityActivityResponse,
    EntityDocumentsResponse,
    EntityInsight,
    EntityProfileResponse,
    EntityStatistics,
)
from ..models import (
    ProfileEntityRelationship as EntityRelationship,
)
from ..services.agent_tools import AgentToolRegistry
from ..services.entity_activity_tracker import EntityActivityTracker
from ..services.entity_file_service import EntityFileService, clear_entity_cache
from ..services.graph import clear_knowledge_graph_cache, get_knowledge_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities", tags=["entity-profile"])

# Initialize services
activity_tracker = None


def get_activity_tracker():
    """Get or create activity tracker instance."""
    global activity_tracker
    if activity_tracker is None:
        knowledge_graph = get_knowledge_graph()
        activity_tracker = EntityActivityTracker(knowledge_graph)
    return activity_tracker


async def _get_entity_statistics(entity_id: str) -> EntityStatistics:
    """Calculate statistics for an entity.

    Note: Caller must ensure knowledge graph is already built.
    """
    tracker = get_activity_tracker()
    return await tracker.get_entity_statistics(entity_id)


async def _get_entity_activities(
    entity_id: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    activity_types: list[str] | None = None,
    limit: int = 100,
) -> list[EntityActivity]:
    """Get activities for an entity.

    Note: Caller must ensure knowledge graph is already built.
    """
    tracker = get_activity_tracker()
    return await tracker.get_entity_activities(
        entity_id=entity_id,
        start_date=start_date,
        end_date=end_date,
        activity_types=activity_types,
        limit=limit,
    )


async def _empty_list() -> list:
    """No-op coroutine for asyncio.gather slots that should be empty.

    Used when an `include_*` flag is False so gather still sees a
    coroutine in every position without us building a special branch
    for every flag combination.
    """
    return []


async def _get_entity_relationships(
    entity_id: str, limit: int = 10
) -> list[EntityRelationship]:
    """Get top relationships for an entity.

    Note: Caller must ensure knowledge graph is already built.
    """
    tracker = get_activity_tracker()
    knowledge_graph = tracker.knowledge_graph

    # Get entity node
    node = knowledge_graph.nodes.get(entity_id)
    if not node:
        return []

    relationships = []

    # Get all edges involving this entity
    entity_edges = []
    for edge_key, edge in knowledge_graph.edges.items():
        if entity_id in edge_key:
            entity_edges.append(edge)

    # Sort by strength and recency
    entity_edges.sort(key=lambda e: (e.strength, e.created), reverse=True)

    # Fetch activities ONCE (not per-edge) to find last interaction dates
    all_activities = await tracker.get_entity_activities(entity_id, limit=50)

    # Convert to EntityRelationship objects
    for edge in entity_edges[:limit]:
        # Determine the other entity
        other_entity_id = edge.target if edge.source == entity_id else edge.source
        other_node = knowledge_graph.nodes.get(other_entity_id)

        if not other_node:
            continue

        # Calculate interaction count from shared documents
        interaction_count = len(edge.context)

        # Find last interaction date from pre-fetched activities
        last_interaction = None
        other_name_lower = other_node.name.lower()

        for activity in all_activities:
            # Check if the other entity is mentioned in the activity
            if other_name_lower in activity.description.lower():
                last_interaction = activity.activity_date
                break

        if not last_interaction:
            # Use edge creation date as fallback
            last_interaction = edge.created

        relationships.append(
            EntityRelationship(
                entity_id=other_entity_id,
                relationship_type=edge.relationship_type,
                strength=edge.strength,
                last_interaction=last_interaction,
                interaction_count=interaction_count,
            )
        )

    return relationships


async def _get_entity_insights(entity_id: str) -> list[EntityInsight]:
    """Generate insights about an entity"""
    try:
        # Get entity data for context
        registry = get_entity_repository()
        entity = registry.get_canonical_entity(entity_id)
        if not entity:
            return []

        # Get activity data
        tracker = get_activity_tracker()
        await tracker.knowledge_graph.build_graph()

        activities = await tracker.get_entity_activities(entity_id, limit=50)
        statistics = await tracker.get_entity_statistics(entity_id)

        # Prepare context for insights generation
        # Handle both dict and Pydantic model formats
        if isinstance(entity, dict):
            entity_name = entity.get("canonical_name") or entity.get("attributes", {}).get("canonical_name", entity_id)
            entity_type = entity.get("type") or entity.get("entity_type", "unknown")
            entity_confidence = entity.get("confidence_score") or entity.get("confidence", 0.0)
        else:
            entity_name = getattr(entity, "canonical_name", entity_id)
            entity_type = getattr(entity.entity_type, "value", str(entity.entity_type)) if hasattr(entity, "entity_type") else "unknown"
            entity_confidence = getattr(entity, "confidence_score", getattr(entity, "confidence", 0.0))

        context_data = {
            "entity": {
                "id": entity_id,
                "name": entity_name,
                "type": entity_type,
                "confidence": entity_confidence,
            },
            "statistics": {
                "total_mentions": statistics.total_mentions,
                "recent_mentions": statistics.recent_mentions,
                "document_count": statistics.document_count,
                "activity_count": statistics.activity_count,
                "relationship_count": statistics.relationship_count,
            },
            "recent_activities": [
                {
                    "type": a.activity_type,
                    "date": a.activity_date.isoformat(),
                    "description": a.description,
                }
                for a in activities[:10]
            ],
        }

        # Use generate_insights tool
        tool_registry = AgentToolRegistry()
        insights_tool = tool_registry.get_tool("generate_insights")

        if not insights_tool:
            return []

        # Execute insights generation
        result = await insights_tool.execute({"data": json.dumps(context_data)})

        if not result.success or not result.output:
            return []

        # Convert tool insights to EntityInsight objects
        entity_insights = []

        # Process general insights
        for insight in result.output.get("insights", [])[:3]:  # Limit to top 3
            entity_insights.append(
                EntityInsight(
                    insight_type=insight.get("category", "general"),
                    content=insight.get("description", ""),
                    confidence=insight.get("confidence_level", 0.5),
                    supporting_evidence=insight.get("supporting_evidence", []),
                )
            )

        # Process predictions as insights
        for prediction in result.output.get("predictions", [])[:2]:  # Limit to top 2
            entity_insights.append(
                EntityInsight(
                    insight_type="prediction",
                    content=prediction.get("prediction", ""),
                    confidence=prediction.get("probability", 0.5),
                    supporting_evidence=prediction.get("early_indicators", []),
                )
            )

        return entity_insights

    except Exception as e:
        logger.error(f"Error generating insights for entity {entity_id}: {e}")
        # Return fallback insights
        return [
            EntityInsight(
                insight_type="error",
                content="Unable to generate insights at this time",
                confidence=0.0,
                supporting_evidence=[str(e)],
            )
        ]


async def _get_narrative_profile(entity_id: str, entity_data: dict | None = None) -> str | None:
    """
    Fetch the narrative profile content from the entity markdown file.

    The narrative profile is the markdown content after the YAML frontmatter,
    which contains the full profile description including Overview, Role,
    Projects, Key Topics, and Relationships sections.

    First tries using source_file from entity metadata (most reliable for
    domain-configured entities), then falls back to EntityService.load_entity_file().

    Args:
        entity_id: The entity ID (e.g., "person-jordan-reyes")
        entity_data: Optional entity dict with metadata containing source_file

    Returns:
        The narrative markdown content if found, None otherwise
    """
    def _extract_narrative(content: str) -> str | None:
        """Parse narrative content after YAML frontmatter."""
        frontmatter_pattern = r'^---\s*\n.*?\n---\s*\n'
        match = re.match(frontmatter_pattern, content, re.DOTALL)
        if match:
            narrative = content[match.end():].strip()
            return narrative if narrative else None
        else:
            return content.strip() if content.strip() else None

    try:
        # Try source_file from entity metadata first (most reliable for graph entities)
        source_file = None
        if entity_data:
            meta = entity_data.get("metadata")
            attrs = entity_data.get("attributes")
            if isinstance(meta, dict):
                source_file = meta.get("source_file")
            if not source_file and isinstance(attrs, dict):
                source_file = attrs.get("source_file")
            if source_file and not isinstance(source_file, str):
                source_file = None

        if source_file:
            # Validate source_file to prevent path traversal
            from pathlib import PurePosixPath
            sf_path = PurePosixPath(source_file)
            if sf_path.is_absolute() or '..' in sf_path.parts or sf_path.suffix.lower() not in ('.md', '.markdown'):
                logger.debug(f"Rejected unsafe source_file '{source_file}' for {entity_id}")
                source_file = None

        if source_file:
            from ..git_ops import GitOperationError, git_ops
            try:
                content = await git_ops.read_file(source_file)
                if content:
                    narrative = _extract_narrative(content)
                    if narrative:
                        return narrative
            except GitOperationError as e:
                logger.debug(f"Could not read source_file {source_file} for {entity_id}: {e}")

        # Fall back to EntityService which checks domain-aware and legacy paths
        from ..domain.entities.services import EntityService

        entity_service = EntityService()
        content = await entity_service.load_entity_file(entity_id)

        if not content:
            logger.debug(f"No content found in profile file for {entity_id}")
            return None

        return _extract_narrative(content)

    except Exception as e:
        logger.debug(f"Could not read narrative profile for {entity_id}: {e}")
        return None


async def _get_entity_documents(
    entity_id: str,
    document_types: list[str] | None = None,
    sort_by: str = "relevance",
    limit: int = 50,
) -> list[dict]:
    """Get documents associated with an entity"""
    tracker = get_activity_tracker()

    # Ensure knowledge graph is built
    await tracker.knowledge_graph.build_graph()

    # Get real documents
    return await tracker.get_entity_documents(
        entity_id=entity_id, document_types=document_types, sort_by=sort_by, limit=limit
    )


@router.get("/{entity_id}/profile", response_model=EntityProfileResponse)
async def get_entity_profile(
    request: Request,
    entity_id: str,
    include_activity: bool = Query(default=True, description="Include recent activity"),
    include_relationships: bool = Query(
        default=True, description="Include relationships"
    ),
    include_insights: bool = Query(default=False, description="Generate insights"),
):
    """Get comprehensive profile for an entity"""
    try:
        # Check demo entities first (populated via demo injection routes)
        demo_entities = getattr(request.app.state, "demo_entities", {})
        if entity_id in demo_entities:
            logger.info(f"Returning demo entity profile for {entity_id}")
            return EntityProfileResponse(**demo_entities[entity_id])

        # Build the knowledge graph ONCE upfront — the helper functions
        # (_get_entity_statistics, _get_entity_activities, _get_entity_relationships)
        # each called build_graph() independently, causing redundant rebuilds.
        tracker = get_activity_tracker()
        await tracker.knowledge_graph.build_graph()

        registry = get_entity_repository()

        # Get the entity from registry first
        entity = registry.get_canonical_entity(entity_id)

        if not entity:
            # Fall back to knowledge graph node data — graph nodes may not
            # be formally registered yet but still contain useful data.
            graph_node = tracker.knowledge_graph.nodes.get(entity_id)
            if not graph_node:
                raise HTTPException(
                    status_code=404, detail=f"Entity {entity_id} not found"
                )
            # Build a basic entity dict from the graph node
            entity_dict = {
                "id": graph_node.id,
                "canonical_name": graph_node.name,
                "entity_type": graph_node.type,
                "type": graph_node.type,
                "aliases": [],
                "confidence": 1.0,
                "source": "knowledge_graph",
                "metadata": graph_node.metadata,
            }
        else:
            # Serialize entity - handle both dict and Pydantic model formats
            if isinstance(entity, dict):
                entity_dict = entity
            else:
                entity_dict = entity.model_dump()

        # The five helpers below are pairwise independent — none reads
        # another's output — so we run them concurrently. Wall-clock time
        # drops from sum(times) to max(times). Disabled flags get a no-op
        # coroutine so gather always sees five awaitables.
        statistics, recent_activity, top_relationships, insights, narrative_profile = (
            await asyncio.gather(
                _get_entity_statistics(entity_id),
                _get_entity_activities(entity_id, limit=10) if include_activity else _empty_list(),
                _get_entity_relationships(entity_id) if include_relationships else _empty_list(),
                _get_entity_insights(entity_id) if include_insights else _empty_list(),
                _get_narrative_profile(entity_id, entity_dict),
            )
        )

        return EntityProfileResponse(
            entity=entity_dict,
            statistics=statistics,
            recent_activity=recent_activity,
            top_relationships=top_relationships,
            insights=insights,
            narrative_profile=narrative_profile,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get entity profile: {str(e)}"
        )


@router.get("/{entity_id}/activity", response_model=EntityActivityResponse)
async def get_entity_activity(
    entity_id: str,
    start_date: datetime | None = Query(
        default=None, description="Start date filter"
    ),
    end_date: datetime | None = Query(default=None, description="End date filter"),
    activity_types: list[str] | None = Query(
        default=None, description="Filter by activity types"
    ),
    limit: int = Query(default=100, le=500, description="Maximum activities to return"),
):
    """Get activity timeline for an entity"""
    try:
        registry = get_entity_repository()
        tracker = get_activity_tracker()
        await tracker.knowledge_graph.build_graph()

        # Verify entity exists — fall back to knowledge graph
        entity = registry.get_canonical_entity(entity_id)
        if not entity:
            graph_node = tracker.knowledge_graph.nodes.get(entity_id)
            if not graph_node:
                raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Get activities
        activities = await _get_entity_activities(
            entity_id,
            start_date=start_date,
            end_date=end_date,
            activity_types=activity_types,
            limit=limit + 1,  # Get one extra to check if there are more
        )

        # Check if there are more results
        has_more = len(activities) > limit
        if has_more:
            activities = activities[:limit]

        return EntityActivityResponse(
            activities=activities,
            total_count=len(activities),
            has_more=has_more,
            entity_id=entity_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get entity activity: {str(e)}"
        )


@router.get("/{entity_id}/documents", response_model=EntityDocumentsResponse)
async def get_entity_documents(
    entity_id: str,
    document_types: list[str] | None = Query(
        default=None, description="Filter by document types"
    ),
    sort_by: str = Query(default="relevance", description="Sort by: relevance, date"),
    limit: int = Query(default=50, le=100, description="Maximum documents to return"),
):
    """Get documents associated with an entity"""
    try:
        registry = get_entity_repository()
        tracker = get_activity_tracker()
        await tracker.knowledge_graph.build_graph()

        # Verify entity exists — fall back to knowledge graph
        entity = registry.get_canonical_entity(entity_id)
        if not entity:
            graph_node = tracker.knowledge_graph.nodes.get(entity_id)
            if not graph_node:
                raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Get documents
        documents = await _get_entity_documents(
            entity_id, document_types=document_types, sort_by=sort_by, limit=limit
        )

        return EntityDocumentsResponse(
            documents=documents, total_count=len(documents), entity_id=entity_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get entity documents: {str(e)}"
        )


# ──────────────────────────────────────────────────────────────────────────
# Profile editing — body quick-edit + durable corrections overlay
# ──────────────────────────────────────────────────────────────────────────


class ProfileBodyUpdate(BaseModel):
    """Replace an entity's narrative profile body (markdown below frontmatter)."""

    # Bounded to keep a single profile body from ballooning the repo / prompt.
    body: str = Field(max_length=100_000)


class CorrectionsUpdate(BaseModel):
    """Replace an entity's durable manual corrections list."""

    # Bounded count; per-item length is enforced in the validator. Corrections
    # are injected verbatim into the grounding prompt, so keep them tight.
    manual_corrections: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("manual_corrections")
    @classmethod
    def _clean_corrections(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        for item in cleaned:
            if len(item) > 500:
                raise ValueError("Each correction must be 500 characters or fewer")
        return cleaned


def _entity_file_service() -> EntityFileService:
    """EntityFileService wired with the current domain config (for file
    resolution: plural directory selection, etc.)."""
    registry = get_entity_repository()
    domain_config = getattr(registry, "domain_config", None)
    return EntityFileService(domain_config)


def _normalize_corrections(raw) -> list[str]:
    """Coerce stored corrections (list, scalar, or missing) into a clean list."""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


@router.get("/{entity_id}/profile/body")
async def get_profile_editable(entity_id: str):
    """Return the editable narrative body + durable corrections for an entity.

    Backs the Edit Profile dialog. The body is the markdown below the
    frontmatter; corrections live in the ``manual_corrections`` frontmatter
    field.
    """
    try:
        efs = _entity_file_service()
        entity = await efs.get_entity(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        return {
            "entity_id": entity_id,
            "body": entity.get("content", "") or "",
            "manual_corrections": _normalize_corrections(
                (entity.get("attributes") or {}).get("manual_corrections")
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error loading editable profile for %s", entity_id)
        raise HTTPException(
            status_code=500, detail="Failed to load editable profile"
        ) from e


@router.put("/{entity_id}/profile")
async def update_profile_body(entity_id: str, payload: ProfileBodyUpdate):
    """Replace the narrative profile body, preserving frontmatter.

    Immediate but NOT regeneration-safe: the AI enricher may re-derive the
    body on the next meeting. For permanent fixes, use corrections (below).
    """
    try:
        efs = _entity_file_service()
        entity = await efs.get_entity(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        entity["content"] = payload.body
        saved = await efs.save_entity(
            entity, commit_message=f"Edit profile body: {entity_id}"
        )
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save profile body")

        entity_type = entity_id.split("-")[0] if "-" in entity_id else None
        clear_entity_cache(entity_type, entity_id)
        clear_knowledge_graph_cache()

        return {"success": True, "entity_id": entity_id, "body": payload.body}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating profile body for %s", entity_id)
        raise HTTPException(
            status_code=500, detail="Failed to update profile body"
        ) from e


@router.put("/{entity_id}/corrections")
async def update_profile_corrections(entity_id: str, payload: CorrectionsUpdate):
    """Replace the durable corrections list for an entity.

    Stored in frontmatter (survives a rebuild) and injected into the
    authoritative grounded-facts block at regeneration time (survives AI
    regeneration). This is the durable way to fix a recurring bad fact.
    """
    try:
        corrections = _normalize_corrections(payload.manual_corrections)

        efs = _entity_file_service()
        entity = await efs.get_entity(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        attributes = entity.get("attributes") or {}
        if corrections:
            attributes["manual_corrections"] = corrections
        else:
            # Empty list → drop the key entirely so no stale "Corrections"
            # header is emitted during grounding.
            attributes.pop("manual_corrections", None)
        entity["attributes"] = attributes

        saved = await efs.save_entity(
            entity, commit_message=f"Update profile corrections: {entity_id}"
        )
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save corrections")

        entity_type = entity_id.split("-")[0] if "-" in entity_id else None
        clear_entity_cache(entity_type, entity_id)
        clear_knowledge_graph_cache()

        return {
            "success": True,
            "entity_id": entity_id,
            "manual_corrections": corrections,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating corrections for %s", entity_id)
        raise HTTPException(
            status_code=500, detail="Failed to update corrections"
        ) from e
