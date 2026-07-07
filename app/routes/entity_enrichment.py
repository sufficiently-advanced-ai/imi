"""API routes for entity enrichment and relationships - Issue #59"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.domain.entities.services import get_entity_repository
from app.models import (
    CanonicalPerson,
    CanonicalTeam,
    RelationshipType,
)
from app.models import (
    EnrichedEntityRelationship as EntityRelationship,
)
from app.services.entity_enrichment import EntityEnrichmentEngine

router = APIRouter(prefix="/api", tags=["entity-enrichment"])
logger = logging.getLogger(__name__)

# Constants
MAX_RELATED_ENTITIES = 10  # Limit to prevent too many requests

def get_enrichment_engine() -> EntityEnrichmentEngine:
    """Get enrichment engine instance"""
    # Lazy init via service factory to avoid circular imports and import-time side effects
    from app.services.entity_enrichment import get_entity_enrichment_service
    return get_entity_enrichment_service()


class EntityRelationshipsResponse(BaseModel):
    """Response model for entity relationships"""

    entity_id: str
    relationships: list[EntityRelationship]
    related_entities: list[dict[str, Any]] | None = None


class OrganizationStructureResponse(BaseModel):
    """Response model for organizational structure"""

    levels: list[dict[str, Any]]
    total_entities: int
    max_depth: int


class OrganizationChartResponse(BaseModel):
    """Response model for organization chart visualization"""

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class TeamWithMembersResponse(BaseModel):
    """Response model for team with members"""

    team: dict[str, Any]
    members: list[dict[str, Any]]
    lead: dict[str, Any] | None


class BulkEnrichRequest(BaseModel):
    """Request model for bulk entity enrichment"""

    entity_ids: list[str]


class EnrichmentStatusResponse(BaseModel):
    """Response model for enrichment status"""

    entity_id: str
    last_enriched: str | None
    enrichment_version: str
    status: str
    next_enrichment: str | None


@router.get(
    "/entities/{entity_id}/relationships", response_model=EntityRelationshipsResponse
)
async def get_entity_relationships(
    entity_id: str,
    relationship_type: RelationshipType | None = Query(None),
    depth: int | None = Query(1, ge=1, le=3),
    engine: EntityEnrichmentEngine = Depends(get_enrichment_engine),
) -> EntityRelationshipsResponse:
    """
    Get relationships for a specific entity

    Args:
        entity_id: ID of the entity
        relationship_type: Optional filter by relationship type
        depth: Depth of relationship traversal (1-3)
    """
    try:
        # Enrich the entity
        enriched = await engine.enrich_entity(entity_id)

        # Filter relationships if type specified
        relationships = enriched.relationships
        if relationship_type:
            relationships = enriched.get_relationships_by_type(relationship_type)

        # Get related entities if depth > 1
        related_entities = []
        if depth > 1:
            # Get unique target entity IDs
            target_ids = list(set(r.target_entity_id for r in relationships))

            # Enrich target entities
            for target_id in target_ids[:MAX_RELATED_ENTITIES]:
                try:
                    target_enriched = await engine.enrich_entity(target_id)
                    related_entities.append(
                        {
                            "id": target_id,
                            "name": target_enriched.base_entity.canonical_name,
                            "type": type(target_enriched.base_entity).__name__,
                            "relationship_count": len(target_enriched.relationships),
                        }
                    )
                except Exception as e:
                    # Non-fatal: continue building related_entities, but log for visibility
                    logger.warning(
                        "Failed enriching related entity '%s': %s", target_id, e
                    )

        return EntityRelationshipsResponse(
            entity_id=entity_id,
            relationships=relationships,
            related_entities=related_entities if depth > 1 else None,
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get relationships: {str(e)}"
        )


@router.get("/organization/structure", response_model=OrganizationStructureResponse)
async def get_organization_structure(
    department: str | None = Query(None),
    format: str | None = Query("hierarchy", pattern="^(hierarchy|chart)$"),
    engine: EntityEnrichmentEngine = Depends(get_enrichment_engine),
):
    """
    Get the organizational structure

    Args:
        department: Optional filter by department
        format: Response format (hierarchy or chart)
    """
    try:
        # Get full hierarchy
        hierarchy = await engine.get_organizational_hierarchy()

        # Filter by department if specified
        if department:
            for level in hierarchy["levels"]:
                level["entities"] = [
                    e
                    for e in level["entities"]
                    if e.get("department", "").lower() == department.lower()
                ]

        # Convert to chart format if requested
        if format == "chart":
            nodes = []
            edges = []

            for level in hierarchy["levels"]:
                for entity in level["entities"]:
                    nodes.append(
                        {
                            "id": entity["id"],
                            "label": entity["name"],
                            "title": entity.get("title", ""),
                            "level": level["level"],
                            "department": entity.get("department", ""),
                        }
                    )

            # Create edges from relationships
            for level in hierarchy["levels"]:
                for entity in level["entities"]:
                    # Get entity relationships
                    try:
                        enriched = await engine.enrich_entity(entity["id"])
                        for rel in enriched.relationships:
                            if rel.relationship_type in [
                                RelationshipType.REPORTS_TO,
                                RelationshipType.MANAGES,
                            ]:
                                edges.append(
                                    {
                                        "source": entity["id"],
                                        "target": rel.target_entity_id,
                                        "relationship_type": rel.relationship_type.value,
                                    }
                                )
                    except Exception as e:
                        logger.warning(
                            "Failed to get relationships for entity '%s': %s", entity["id"], e
                        )

            return OrganizationChartResponse(nodes=nodes, edges=edges)

        return OrganizationStructureResponse(
            levels=hierarchy["levels"],
            total_entities=hierarchy["total_entities"],
            max_depth=hierarchy["max_depth"],
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get organization structure: {str(e)}"
        )


@router.get("/organization/teams")
async def list_teams_with_members(
    engine: EntityEnrichmentEngine = Depends(get_enrichment_engine),
) -> dict[str, list[TeamWithMembersResponse]]:
    """Get all teams with their members"""
    try:
        # Get all teams from registry
        all_teams = []
        for entity_id, entity in get_entity_repository()._entities.items():
            if isinstance(entity, CanonicalTeam):
                all_teams.append((entity_id, entity))

        # Build team responses
        team_responses = []
        for _team_id, team in all_teams:
            # Get member details
            members = []
            for member_id in team.members:
                member = get_entity_repository().get_canonical_entity(member_id)
                if member and isinstance(member, CanonicalPerson):
                    members.append(
                        {
                            "id": member_id,
                            "name": member.canonical_name,
                            "role": member.titles[0] if member.titles else "",
                        }
                    )

            # Get lead details
            lead = None
            if team.lead:
                lead_entity = get_entity_repository().get_canonical_entity(team.lead)
                if lead_entity and isinstance(lead_entity, CanonicalPerson):
                    lead = {
                        "id": team.lead,
                        "name": lead_entity.canonical_name,
                        "role": lead_entity.titles[0] if lead_entity.titles else "",
                    }

            team_responses.append(
                {"team": team.model_dump(), "members": members, "lead": lead}
            )

        return {"teams": team_responses}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list teams: {str(e)}")


@router.get("/organization/teams/hierarchy")
async def get_team_hierarchy(
    engine: EntityEnrichmentEngine = Depends(get_enrichment_engine),
) -> dict[str, Any]:
    """Get team hierarchy structure"""
    try:
        # Get all teams
        all_teams = []
        for entity_id, entity in get_entity_repository()._entities.items():
            if isinstance(entity, CanonicalTeam):
                all_teams.append((entity_id, entity))

        # Build hierarchy
        root_teams = []
        team_map = {team_id: team for team_id, team in all_teams}

        for team_id, team in all_teams:
            if not team.parent_team or team.parent_team not in team_map:
                # This is a root team
                root_teams.append(
                    {
                        "id": team_id,
                        "name": team.canonical_name,
                        "sub_teams": _get_sub_teams(team_id, team_map),
                    }
                )

        return {"root_teams": root_teams}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get team hierarchy: {str(e)}"
        )


def _get_sub_teams(
    parent_id: str, team_map: dict[str, CanonicalTeam]
) -> list[dict[str, Any]]:
    """Recursively get sub-teams"""
    sub_teams = []
    for team_id, team in team_map.items():
        if team.parent_team == parent_id:
            sub_teams.append(
                {
                    "id": team_id,
                    "name": team.canonical_name,
                    "sub_teams": _get_sub_teams(team_id, team_map),
                }
            )
    return sub_teams


@router.post("/entities/{entity_id}/enrich")
async def trigger_entity_enrichment(
    entity_id: str, engine: EntityEnrichmentEngine = Depends(get_enrichment_engine)
) -> dict[str, str]:
    """Manually trigger entity enrichment"""
    try:
        # Clear cache for this entity
        if entity_id in engine._cache:
            del engine._cache[entity_id]

        # Enrich entity
        await engine.enrich_entity(entity_id)

        return {"message": "Entity enrichment triggered", "entity_id": entity_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to enrich entity: {str(e)}"
        )


@router.post("/entities/enrich/bulk")
async def bulk_entity_enrichment(
    request: BulkEnrichRequest,
    engine: EntityEnrichmentEngine = Depends(get_enrichment_engine),
) -> dict[str, Any]:
    """Bulk entity enrichment"""
    try:
        results = await engine.batch_enrich_entities(request.entity_ids)

        # Convert results to response format
        response_results = {}
        for entity_id in request.entity_ids:
            if entity_id in results:
                response_results[entity_id] = {"status": "success"}
            else:
                response_results[entity_id] = {"status": "failed"}

        return {"results": response_results}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to enrich entities: {str(e)}"
        )


@router.get(
    "/entities/{entity_id}/enrichment-status", response_model=EnrichmentStatusResponse
)
async def get_enrichment_status(
    entity_id: str, engine: EntityEnrichmentEngine = Depends(get_enrichment_engine)
) -> EnrichmentStatusResponse:
    """Get enrichment status for an entity"""
    try:
        # Check if entity exists
        entity = get_entity_repository().get_canonical_entity(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Check cache
        if entity_id in engine._cache:
            enriched = engine._cache[entity_id]
            last_enriched = enriched.last_enriched.isoformat() + "Z"
            next_enrichment = (
                enriched.last_enriched + engine._cache_ttl
            ).isoformat() + "Z"
            status = "current"
        else:
            last_enriched = None
            next_enrichment = None
            status = "pending"

        return EnrichmentStatusResponse(
            entity_id=entity_id,
            last_enriched=last_enriched,
            enrichment_version="1.0",
            status=status,
            next_enrichment=next_enrichment,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get enrichment status: {str(e)}"
        )
