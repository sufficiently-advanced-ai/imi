"""
Entity CRUD API Routes - Issue #244.

RESTful API endpoints for domain-aware entity management including
CRUD operations, relationship management, and schema information.
"""

import logging
from enum import Enum
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from ..domain.entities.services import get_entity_repository
from ..services.entity_crud import EntityCrudService
from ..services.entity_relationships import EntityRelationshipService
from ..services.entity_search import EntitySearchService
from ..services.entity_validation import EntityValidationService
from ..services.graph import clear_knowledge_graph_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities", tags=["entity-crud"])


# Pydantic models for request/response
class EntityCreateRequest(BaseModel):
    entity_type: str = Field(..., description="Type of entity to create")
    attributes: dict[str, Any] = Field(..., description="Entity attributes")
    relationships: dict[str, Any] | None = Field(
        default=None, description="Entity relationships"
    )


class EntityUpdateRequest(BaseModel):
    attributes: dict[str, Any] | None = Field(
        default=None, description="Attributes to update"
    )
    relationships: dict[str, Any] | None = Field(
        default=None, description="Relationships to update"
    )


class RelationshipHandling(str, Enum):
    preserve = "preserve"
    cascade = "cascade"
    nullify = "nullify"


class EntityDeleteRequest(BaseModel):
    reason: str | None = Field(default=None, description="Reason for deletion")
    handle_relationships: RelationshipHandling = Field(
        default=RelationshipHandling.preserve, description="How to handle relationships"
    )


class RelationshipRequest(BaseModel):
    relationship_type: str = Field(..., description="Type of relationship")
    target_entity_id: str = Field(..., description="ID of target entity")


class EntityValidationRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    attributes: dict[str, Any] = Field(..., description="Attributes to validate")
    relationships: dict[str, Any] | None = Field(
        default=None, description="Relationships to validate"
    )


# Dependency to get services
def get_entity_crud_service() -> EntityCrudService:
    """Get EntityCrudService instance."""
    entity_registry = get_entity_repository()

    # Get the current domain config from registry
    domain_config = None
    if hasattr(entity_registry, "domain_config"):
        domain_config = entity_registry.domain_config

    return EntityCrudService(
        entity_registry=entity_registry, domain_config=domain_config
    )


def get_entity_validation_service() -> EntityValidationService:
    """Get EntityValidationService instance."""
    entity_registry = get_entity_repository()

    # Get the current domain config from registry for consistency
    domain_config = None
    if hasattr(entity_registry, "domain_config"):
        domain_config = entity_registry.domain_config

    return EntityValidationService(
        entity_registry=entity_registry, domain_config=domain_config
    )


def get_entity_relationship_service() -> EntityRelationshipService:
    """Get EntityRelationshipService instance."""
    entity_registry = get_entity_repository()

    # Get the current domain config from registry for consistency
    domain_config = None
    if hasattr(entity_registry, "domain_config"):
        domain_config = entity_registry.domain_config

    return EntityRelationshipService(
        entity_registry=entity_registry, domain_config=domain_config
    )


def get_entity_search_service() -> EntitySearchService:
    """Get EntitySearchService instance."""
    entity_registry = get_entity_repository()

    # Get the current domain config from registry for consistency
    domain_config = None
    if hasattr(entity_registry, "domain_config"):
        domain_config = entity_registry.domain_config

    return EntitySearchService(
        entity_registry=entity_registry, domain_config=domain_config
    )


@router.get("/list")
async def list_entities(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Page size"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    q: str | None = Query(None, description="Search query"),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_order: str = Query(
        "desc", regex="^(asc|desc)$", description="Sort order (asc or desc)"
    ),
    status: str | None = Query(None, description="Filter by status"),
    include_archived: bool = Query(False, description="Include archived entities"),
    # Date range filters
    start_date_from: str | None = Query(None, description="Start date range (from)"),
    start_date_to: str | None = Query(None, description="Start date range (to)"),
    # Boolean filters
    is_active: bool | None = Query(None, description="Filter by active status"),
    # Enum filters
    industry: str | None = Query(None, description="Filter by industry"),
    # Numeric range filters
    revenue_min: float | None = Query(None, description="Minimum revenue"),
    revenue_max: float | None = Query(None, description="Maximum revenue"),
    crud_service: EntityCrudService = Depends(get_entity_crud_service),
):
    """
    List entities with pagination, filtering, and sorting.
    """
    try:
        # Build filters dictionary
        filters = {}
        if entity_type:
            filters["entity_type"] = entity_type
        if status:
            filters["status"] = status
        if include_archived:
            filters["include_archived"] = include_archived
        if is_active is not None:
            filters["is_active"] = is_active
        if industry:
            filters["industry"] = industry
        if start_date_from:
            filters["start_date_from"] = start_date_from
        if start_date_to:
            filters["start_date_to"] = start_date_to
        if revenue_min is not None:
            filters["revenue_min"] = revenue_min
        if revenue_max is not None:
            filters["revenue_max"] = revenue_max

        result = await crud_service.list_entities(
            page=page,
            size=size,
            filters=filters if filters else None,
            search_query=q,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        return result

    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Schema and validation endpoints - MUST come before /{entity_id} catch-all route
@router.get("/schema")
async def get_domain_schema():
    """
    Get the current domain schema.
    """
    try:
        entity_registry = get_entity_repository()

        if not entity_registry.domain_config:
            raise HTTPException(
                status_code=404, detail="No domain configuration loaded"
            )

        # Return in format expected by frontend DomainSchema interface
        domain_dict = entity_registry.domain_config.dict()
        return {
            "domain_id": domain_dict.get("id"),
            "entities": domain_dict.get("entities", {}),
            "entity_types": entity_registry.get_entity_types(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting domain schema: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema/{entity_type}")
async def get_entity_type_schema(entity_type: str):
    """
    Get schema for a specific entity type.
    """
    try:
        entity_registry = get_entity_repository()
        entity_schema = entity_registry.get_entity_schema(entity_type)

        if not entity_schema:
            raise HTTPException(
                status_code=404, detail=f"Entity type '{entity_type}' not found"
            )

        return {"entity_type": entity_type, "schema": entity_schema.dict()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting schema for {entity_type}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/types")
async def get_entity_types():
    """
    Get list of available entity types.
    """
    try:
        entity_registry = get_entity_repository()
        entity_types = entity_registry.get_entity_types()

        return {"entity_types": entity_types, "count": len(entity_types)}

    except Exception as e:
        logger.error(f"Error getting entity types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_entity_data(
    validation_request: EntityValidationRequest,
    validation_service: EntityValidationService = Depends(
        get_entity_validation_service
    ),
):
    """
    Validate entity data without creating the entity.
    """
    try:
        result = await validation_service.validate_entity_data(
            validation_request.dict()
        )

        return result

    except Exception as e:
        logger.error(f"Error validating entity data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_entity(
    entity_data: EntityCreateRequest,
    crud_service: EntityCrudService = Depends(get_entity_crud_service),
):
    """
    Create a new entity with validation.
    """
    try:
        result = await crud_service.create_entity(entity_data.dict())

        if not result.get("success"):
            if "validation_errors" in result:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Validation failed",
                        "validation_errors": result["validation_errors"],
                    },
                )
            else:
                raise HTTPException(
                    status_code=400, detail=result.get("error", "Creation failed")
                )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Entity-specific endpoints - MUST come after all general routes
@router.get("/{entity_id}")
async def get_entity(
    entity_id: str = Path(..., min_length=1, description="Entity ID"),
    crud_service: EntityCrudService = Depends(get_entity_crud_service),
):
    """
    Get an entity by ID.
    """
    try:
        entity = await crud_service.get_entity(entity_id)

        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        return entity

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{entity_id}")
async def update_entity(
    entity_id: str = Path(..., min_length=1, description="Entity ID"),
    update_data: EntityUpdateRequest = Body(...),
    crud_service: EntityCrudService = Depends(get_entity_crud_service),
):
    """
    Update an entity.
    """
    try:
        result = await crud_service.update_entity(
            entity_id, update_data.dict(exclude_none=True)
        )

        if not result.get("success"):
            if "validation_errors" in result:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Validation failed",
                        "validation_errors": result["validation_errors"],
                    },
                )
            elif result.get("error") == "Entity not found":
                raise HTTPException(status_code=404, detail="Entity not found")
            else:
                raise HTTPException(
                    status_code=400, detail=result.get("error", "Update failed")
                )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating entity {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{entity_id}")
async def delete_entity(
    entity_id: str = Path(..., min_length=1, description="Entity ID"),
    delete_data: EntityDeleteRequest | None = None,
    crud_service: EntityCrudService = Depends(get_entity_crud_service),
):
    """
    Soft delete an entity.
    """
    try:
        delete_params = delete_data.dict() if delete_data else {}
        result = await crud_service.delete_entity(entity_id, **delete_params)

        if not result.get("success"):
            if result.get("error") == "Entity not found":
                raise HTTPException(status_code=404, detail="Entity not found")
            else:
                raise HTTPException(
                    status_code=400, detail=result.get("error", "Deletion failed")
                )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting entity {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Relationship management endpoints
@router.post("/{entity_id}/relationships")
async def add_relationship(
    entity_id: str = Path(..., min_length=1, description="Source entity ID"),
    relationship_data: RelationshipRequest = Body(...),
    relationship_service: EntityRelationshipService = Depends(
        get_entity_relationship_service
    ),
):
    """
    Add a relationship between entities.
    """
    try:
        result = await relationship_service.add_relationship(
            source_entity_id=entity_id,
            relationship_type=relationship_data.relationship_type,
            target_entity_id=relationship_data.target_entity_id,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to add relationship"),
            )

        # Invalidate the Neo4j graph + visualization response caches so the new
        # edge shows up on the next read (the service only clears the domain
        # graph build cache).
        clear_knowledge_graph_cache()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding relationship: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{entity_id}/relationships/{relationship_type}/{target_entity_id}")
async def remove_relationship(
    entity_id: str = Path(..., min_length=1, description="Source entity ID"),
    relationship_type: str = Path(..., min_length=1, description="Relationship type"),
    target_entity_id: str = Path(..., min_length=1, description="Target entity ID"),
    relationship_service: EntityRelationshipService = Depends(
        get_entity_relationship_service
    ),
):
    """
    Remove a relationship between entities.
    """
    try:
        result = await relationship_service.remove_relationship(
            source_entity_id=entity_id,
            relationship_type=relationship_type,
            target_entity_id=target_entity_id,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to remove relationship"),
            )

        # Invalidate caches so the removed edge disappears on the next read.
        clear_knowledge_graph_cache()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing relationship: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{entity_id}/relationships")
async def get_entity_relationships(
    entity_id: str = Path(..., min_length=1, description="Entity ID"),
    relationship_service: EntityRelationshipService = Depends(
        get_entity_relationship_service
    ),
):
    """
    Get all relationships for an entity.
    """
    try:
        result = await relationship_service.get_entity_relationships(entity_id)
        return result

    except Exception as e:
        logger.error(f"Error getting relationships for {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
