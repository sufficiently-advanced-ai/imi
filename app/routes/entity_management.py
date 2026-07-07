"""Entity Management API routes - Issue #60"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from ..domain.entities.services import get_entity_repository
from ..models import (
    CanonicalPerson,
    CanonicalProject,
    CanonicalTeam,
    EntityAliasRequest,
    EntityAliasResponse,
    EntityArchiveResponse,
    EntityMergeRequest,
    EntityMergeResponse,
    EntityType,
    EntityUpdateRequest,
    EntityUpdateResponse,
)
from ..services.entity_archive_manager import EntityArchiveManager
from ..services.entity_utils import extract_entity_type_from_id
from ..services.entity_webhook_service import EntityEventType, get_webhook_service
from ..services.graph import clear_knowledge_graph_cache
from ..services.graph.factory import get_knowledge_graph
from ..services.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities", tags=["entity-management"])


def _get_entity_type(entity_id: str) -> EntityType | None:
    """Determine entity type from ID"""
    entity_type_str = extract_entity_type_from_id(entity_id)
    if entity_type_str == "person":
        return EntityType.PERSON
    elif entity_type_str == "project":
        return EntityType.PROJECT
    elif entity_type_str == "team":
        return EntityType.TEAM
    return None


async def _calculate_merge_impact_graph(
    graph, primary: dict[str, Any], duplicate: dict[str, Any],
    primary_id: str, duplicate_id: str,
) -> dict[str, Any]:
    """Non-mutating preview of what merging ``duplicate`` into ``primary`` does.

    Reads from the live graph (Neo4j) rather than the legacy registry so it
    works for any entity the graph knows about, including auto-extracted nodes
    with no canonical registry record.
    """
    dup_meta = duplicate.get("metadata", {}) or {}
    primary_meta = primary.get("metadata", {}) or {}

    # Surface forms the primary will absorb as aliases.
    aliases: list[str] = []
    if duplicate.get("name"):
        aliases.append(duplicate["name"])
    dup_aliases = dup_meta.get("aliases") or []
    if isinstance(dup_aliases, str):
        dup_aliases = [dup_aliases]
    aliases.extend(dup_aliases)
    aliases.append(duplicate_id)

    # Relationships that will be transferred = the duplicate's current edges.
    relationships_affected = 0
    try:
        rows = await graph.neo4j.execute_read(
            "MATCH (n:Entity {id: $id})-[r]-() RETURN count(r) AS c",
            {"id": duplicate_id},
        )
        relationships_affected = rows[0]["c"] if rows else 0
    except Exception as e:
        logger.warning("Merge preview: relationship count failed for %s: %s", duplicate_id, e)

    # Scalar fields present on both with differing values → human review.
    data_conflicts = []
    for key in ("email", "title", "company", "department"):
        sv = dup_meta.get(key)
        tv = primary_meta.get(key)
        if sv and tv and sv != tv:
            data_conflicts.append(
                {"field": key, "duplicate_value": sv, "primary_value": tv}
            )

    return {
        # Order-preserving dedup, dropping falsy entries.
        "aliases_to_merge": [a for a in dict.fromkeys(aliases) if a],
        "relationships_affected": relationships_affected,
        "data_conflicts": data_conflicts,
    }


@router.post("/{entity_id}/merge", response_model=EntityMergeResponse)
async def merge_entities(entity_id: str, merge_request: EntityMergeRequest):
    """Merge a duplicate entity into a survivor via the graph mutation layer.

    Uses ``graph.merge_nodes`` (Neo4j + durable frontmatter write-through) so
    transferred edges and aliases survive a rebuild — unlike the legacy
    registry merge. The path id is the survivor by default; ``primary_id`` in
    the body can state the survivor explicitly.
    """
    try:
        # Durable merge lives on the Neo4j-backed graph: get_entity_by_id,
        # merge_nodes and the .neo4j Cypher accessor used by the impact preview
        # are all Neo4jKnowledgeGraph methods. get_knowledge_graph() returns
        # that graph when Neo4j is active (it is NOT get_semantica_knowledge(),
        # whose SemanticaKnowledge has none of these methods — calling them
        # there 500s with an AttributeError).
        graph = get_knowledge_graph()
        if graph is None:
            raise HTTPException(
                status_code=503, detail="Knowledge graph service unavailable"
            )
        # The in-memory builder fallback (used when Neo4j is unavailable) cannot
        # do a durable merge. Fail with a clear 503 rather than an opaque 500.
        if not hasattr(graph, "merge_nodes") or not hasattr(graph, "get_entity_by_id"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Durable entity merge requires the Neo4j graph backend, "
                    "which is not currently active."
                ),
            )
        target_id = merge_request.target_entity_id

        # Resolve survivor (primary) and the entity merged away (duplicate).
        # Guard the self-merge case first: if the two ids are equal there is no
        # distinct duplicate, and the generator below would raise StopIteration.
        if entity_id == target_id:
            raise HTTPException(
                status_code=400, detail="Cannot merge an entity into itself"
            )
        primary_id = merge_request.primary_id or entity_id
        candidates = {entity_id, target_id}
        if primary_id not in candidates:
            raise HTTPException(
                status_code=400,
                detail="primary_id must be one of the two entities being merged",
            )
        duplicate_id = next(c for c in candidates if c != primary_id)

        # Type compatibility — string-based so all domain types are supported
        # (the EntityType enum only covers person/project/team).
        src_type = extract_entity_type_from_id(entity_id)
        tgt_type = extract_entity_type_from_id(target_id)
        if not src_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid source entity ID format: {entity_id}"
            )
        if not tgt_type:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid target entity ID format: {target_id}",
            )
        if src_type != tgt_type:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot merge different entity types: {src_type} and {tgt_type}",
            )

        # Existence checks against the live graph.
        primary = await graph.get_entity_by_id(primary_id)
        if not primary:
            raise HTTPException(status_code=404, detail=f"Entity {primary_id} not found")
        duplicate = await graph.get_entity_by_id(duplicate_id)
        if not duplicate:
            raise HTTPException(
                status_code=404, detail=f"Entity {duplicate_id} not found"
            )

        # Preview: non-mutating impact summary for the confirm dialog.
        if merge_request.preview:
            merge_impact = await _calculate_merge_impact_graph(
                graph, primary, duplicate, primary_id, duplicate_id
            )
            return EntityMergeResponse(
                success=True, preview=True, merge_impact=merge_impact
            )

        # The user explicitly chose the survivor, so primary's properties win
        # on conflict. (The REST merge_strategy values — confidence/manual/
        # oldest — predate the graph strategies; primary_wins matches the
        # manual select-two UX.)
        result = await graph.merge_nodes(
            primary_id=primary_id,
            duplicate_id=duplicate_id,
            strategy="primary_wins",
        )

        # Invalidate the Neo4j + visualization response caches so the next read
        # reflects the merge (duplicate gone, edges consolidated) rather than
        # serving the pre-merge state. Non-fatal — the merge already succeeded.
        try:
            clear_knowledge_graph_cache()
        except Exception as e:
            logger.warning("Merge cache invalidation failed (non-fatal): %s", e)

        # Publish merge event (best-effort; never fails the merge).
        try:
            webhook_service = get_webhook_service()
            await webhook_service.publish_event(
                EntityEventType.ENTITY_MERGED,
                primary_id,
                src_type,
                {
                    "duplicate_entity_id": duplicate_id,
                    "merge_strategy": "primary_wins",
                    "relationships_transferred": result.get(
                        "relationships_transferred", 0
                    ),
                },
            )
        except Exception as e:
            logger.warning("Merge webhook publish failed (non-fatal): %s", e)

        return EntityMergeResponse(
            success=True,
            merged_entity_id=primary_id,
            merge_summary={
                "primary_entity": primary_id,
                "duplicate_entity": duplicate_id,
                "merged_at": datetime.utcnow().isoformat(),
                "strategy": "primary_wins",
                "relationships_transferred": result.get("relationships_transferred", 0),
                "aliases": result.get("aliases", []),
            },
            # Rollback is not supported on the graph merge path. The duplicate's
            # markdown file is soft-archived (recoverable), and merges run
            # behind a preview-confirm step.
            rollback_token=None,
            rollback_enabled=False,
        )

    except HTTPException:
        raise
    except ValueError as e:
        # merge_nodes raises ValueError for bad strategy / missing nodes.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to process merge request: {str(e)}"
        )


@router.post("/{entity_id}/aliases", response_model=EntityAliasResponse)
async def add_entity_alias(entity_id: str, alias_request: EntityAliasRequest):
    """Add a new alias to an entity"""
    try:
        registry = get_entity_repository()

        # Get the entity
        entity_type = _get_entity_type(entity_id)
        if not entity_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {entity_id}"
            )

        entity = registry.get_canonical_entity(entity_type.value, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Check if alias already exists for this entity
        if entity.has_alias(alias_request.alias):
            raise HTTPException(
                status_code=400,
                detail=f"Alias '{alias_request.alias}' already exists for this entity",
            )

        # Check if alias conflicts with other entities
        # Try to determine type from alias format
        alias_type = _get_entity_type(alias_request.alias)
        if alias_type:
            conflicting_entity = registry.get_canonical_entity(
                alias_type.value, alias_request.alias
            )
        else:
            # If no type in alias, check same type as the entity
            conflicting_entity = registry.get_canonical_entity(
                entity_type.value, alias_request.alias
            )
        if conflicting_entity and conflicting_entity.id != entity_id:
            raise HTTPException(
                status_code=409,
                detail=f"Alias '{alias_request.alias}' conflicts with entity {conflicting_entity.id}",
                headers={"conflicting_entity_id": conflicting_entity.id},
            )

        # Add the alias
        entity.add_alias(alias_request.alias)
        registry.save()

        return EntityAliasResponse(
            success=True,
            current_aliases=entity.aliases,
            message=f"Alias '{alias_request.alias}' added successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add alias: {str(e)}")


@router.delete("/{entity_id}/aliases/{alias}")
async def remove_entity_alias(entity_id: str, alias: str):
    """Remove an alias from an entity"""
    try:
        registry = get_entity_repository()

        # Get the entity
        entity_type = extract_entity_type_from_id(entity_id)
        if not entity_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {entity_id}"
            )
        entity = registry.get_canonical_entity(entity_type, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Remove the alias
        entity.remove_alias(alias)
        registry.save()

        return EntityAliasResponse(
            success=True,
            current_aliases=entity.aliases,
            message=f"Alias '{alias}' removed successfully",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove alias: {str(e)}")


@router.put("/{entity_id}", response_model=EntityUpdateResponse)
async def update_entity(entity_id: str, update_request: EntityUpdateRequest):
    """Update entity fields"""
    try:
        registry = get_entity_repository()

        # Get the entity
        entity_type = extract_entity_type_from_id(entity_id)
        if not entity_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {entity_id}"
            )
        entity = registry.get_canonical_entity(entity_type, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # TODO: Implement version-based concurrency control
        # if update_request.version and entity.version != update_request.version:
        #     raise HTTPException(
        #         status_code=409,
        #         detail="Version conflict - entity has been modified"
        #     )

        # Update fields based on entity type
        if isinstance(entity, CanonicalPerson):
            if update_request.canonical_name:
                entity.canonical_name = update_request.canonical_name
            if update_request.email:
                entity.email = update_request.email
            if update_request.phone:
                entity.phone = update_request.phone
            if update_request.titles:
                entity.titles = update_request.titles
            if update_request.departments:
                entity.departments = update_request.departments
            if update_request.confidence is not None:
                entity.confidence = update_request.confidence

        elif isinstance(entity, CanonicalProject):
            if update_request.canonical_name:
                entity.canonical_name = update_request.canonical_name
            if update_request.status:
                entity.status = update_request.status
            if update_request.teams:
                entity.teams = update_request.teams
            if update_request.objectives:
                entity.objectives = update_request.objectives
            if update_request.confidence is not None:
                entity.confidence = update_request.confidence

        elif isinstance(entity, CanonicalTeam):
            if update_request.canonical_name:
                entity.canonical_name = update_request.canonical_name
            if update_request.department:
                entity.department = update_request.department
            if update_request.division:
                entity.division = update_request.division
            if update_request.members:
                entity.members = update_request.members
            if update_request.lead:
                entity.lead = update_request.lead
            if update_request.confidence is not None:
                entity.confidence = update_request.confidence

        # Update last seen
        entity.update_last_seen()
        registry.save()

        return EntityUpdateResponse(
            success=True,
            entity=entity.model_dump(),
            version=1,  # TODO: Implement actual versioning
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update entity: {str(e)}"
        )


@router.delete("/{entity_id}", response_model=EntityArchiveResponse)
async def archive_entity(
    entity_id: str,
    archive_reason: str = Query(..., description="Reason for archiving"),
    handle_relationships: str = Query(
        default="preserve", description="How to handle relationships"
    ),
):
    """Archive (soft delete) an entity"""
    try:
        registry = get_entity_repository()

        # Get the entity
        entity_type = extract_entity_type_from_id(entity_id)
        if not entity_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {entity_id}"
            )
        entity = registry.get_canonical_entity(entity_type, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Count relationships that will be preserved
        knowledge_graph = KnowledgeGraph()
        await knowledge_graph.build_graph()

        relationships_preserved = 0
        if handle_relationships == "preserve":
            # Count actual relationships
            node = knowledge_graph.nodes.get(entity_id)
            relationships_preserved = len(node.connections) if node else 0

        # Archive the entity
        archive_manager = EntityArchiveManager(registry)
        archive_result = await archive_manager.archive_entity(
            entity_id=entity_id, entity=entity, archive_reason=archive_reason
        )

        archived_at = datetime.fromisoformat(archive_result["archived_at"])

        # Publish archive event
        webhook_service = get_webhook_service()
        entity_type = _get_entity_type(entity_id)
        if not entity_type:
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {entity_id}"
            )
        await webhook_service.publish_event(
            EntityEventType.ENTITY_ARCHIVED,
            entity_id,
            entity_type.value,
            {
                "archive_reason": archive_reason,
                "relationships_preserved": relationships_preserved,
            },
        )

        return EntityArchiveResponse(
            success=True,
            entity_id=entity_id,
            archived_at=archived_at,
            archive_reason=archive_reason,
            relationships_preserved=relationships_preserved,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to archive entity: {str(e)}"
        )


@router.post("/{entity_id}/restore")
async def restore_entity(entity_id: str):
    """Restore an archived entity"""
    try:
        registry = get_entity_repository()
        archive_manager = EntityArchiveManager(registry)

        # Restore the entity
        restored_entity = await archive_manager.restore_entity(entity_id)

        if not restored_entity:
            raise HTTPException(
                status_code=404, detail=f"Archived entity {entity_id} not found"
            )

        # Publish restore event
        webhook_service = get_webhook_service()
        await webhook_service.publish_event(
            EntityEventType.ENTITY_RESTORED,
            entity_id,
            restored_entity.entity_type.value,
            {"restored_at": datetime.utcnow().isoformat()},
        )

        return {
            "success": True,
            "entity": restored_entity.model_dump(),
            "restored_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to restore entity: {str(e)}"
        )


@router.post("/merge/rollback")
async def rollback_merge(rollback_token: str = Body(..., embed=True)):
    """Rollback a previously merged entity using the rollback token"""
    try:
        registry = get_entity_repository()
        archive_manager = EntityArchiveManager(registry)

        # Perform rollback
        result = await archive_manager.rollback_merge(rollback_token)

        if not result["success"]:
            raise HTTPException(
                status_code=400, detail=result.get("error", "Rollback failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to rollback merge: {str(e)}"
        )
