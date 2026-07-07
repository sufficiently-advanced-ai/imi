"""Entity Registry API endpoints - Issue #57"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_entity_repository
from app.domain.entities.services import EntityRepository
from app.services.entity_utils import extract_entity_type_from_id

router = APIRouter()
logger = logging.getLogger(__name__)


def get_registry() -> EntityRepository:
    """Get or create the registry instance"""
    return get_entity_repository()


class RegisterEntityRequest(BaseModel):
    """Request model for registering an entity"""

    entity_type: str = Field(
        ..., description="Type of entity: person, project, or team"
    )
    canonical_name: str = Field(..., description="Canonical name for the entity")
    aliases: list[str] | None = Field(
        default_factory=list, description="Alternative names"
    )
    # Person-specific fields
    titles: list[str] | None = Field(default=None)
    email: str | None = Field(default=None)
    phone: str | None = Field(default=None)
    departments: list[str] | None = Field(default=None)
    # Project-specific fields
    status: str | None = Field(default="active")
    teams: list[str] | None = Field(default=None)
    # Team-specific fields
    department: str | None = Field(default=None)
    division: str | None = Field(default=None)
    parent_team: str | None = Field(default=None)
    members: list[str] | None = Field(default=None)
    lead: str | None = Field(default=None)


class BatchRegisterRequest(BaseModel):
    """Request model for batch entity registration"""

    entities: list[RegisterEntityRequest]


class EntitySuggestionRequest(BaseModel):
    """Request model for entity suggestions"""

    text: str = Field(..., description="Text to analyze for entity suggestions")


class MergeEntitiesRequest(BaseModel):
    """Request model for merging entities"""

    entity_id_1: str = Field(..., description="First entity ID")
    entity_id_2: str = Field(..., description="Second entity ID")
    canonical_name: str | None = Field(
        default=None, description="Preferred canonical name"
    )


class ValidateEntityRequest(BaseModel):
    """Request model for entity validation"""

    entity_name: str = Field(..., description="Entity name to validate")
    entity_type: str = Field(..., description="Entity type: person, project, or team")


@router.get("/api/entities/registry")
async def get_registry_overview(registry: EntityRepository = Depends(get_registry)):
    """Get overview of all entities in the registry"""
    all_entities = registry.get_all_entities()
    stats = registry.get_stats()

    return {
        "people": [entity.model_dump() for entity in all_entities["people"].values()],
        "projects": [
            entity.model_dump() for entity in all_entities["projects"].values()
        ],
        "teams": [entity.model_dump() for entity in all_entities["teams"].values()],
        "stats": stats,
    }


@router.post("/api/entities/register", status_code=201)
async def register_entity(
    request: RegisterEntityRequest, registry: EntityRepository = Depends(get_registry)
):
    """Register a new entity or update existing"""
    if request.entity_type not in ["person", "project", "team"]:
        raise HTTPException(status_code=400, detail="Invalid entity type")

    entity_id = None

    if request.entity_type == "person":
        entity_id = registry.register_person(
            canonical_name=request.canonical_name,
            aliases=request.aliases,
            titles=request.titles,
            email=request.email,
            phone=request.phone,
            departments=request.departments,
        )
    elif request.entity_type == "project":
        entity_id = registry.register_project(
            canonical_name=request.canonical_name,
            aliases=request.aliases,
            status=request.status,
            teams=request.teams,
        )
    elif request.entity_type == "team":
        entity_id = registry.register_team(
            canonical_name=request.canonical_name,
            aliases=request.aliases,
            department=request.department,
            division=request.division,
            parent_team=request.parent_team,
            members=request.members,
            lead=request.lead,
        )

    return {
        "entity_id": entity_id,
        "entity_type": request.entity_type,
        "canonical_name": request.canonical_name,
    }


@router.post("/api/entities/register/batch", status_code=201)
async def batch_register_entities(
    request: BatchRegisterRequest, registry: EntityRepository = Depends(get_registry)
):
    """Register multiple entities at once"""
    entity_ids = []

    for entity_req in request.entities:
        if entity_req.entity_type == "person":
            entity_id = registry.register_person(
                canonical_name=entity_req.canonical_name,
                aliases=entity_req.aliases,
                titles=entity_req.titles,
                email=entity_req.email,
            )
        elif entity_req.entity_type == "project":
            entity_id = registry.register_project(
                canonical_name=entity_req.canonical_name,
                aliases=entity_req.aliases,
                status=entity_req.status,
                teams=entity_req.teams,
            )
        elif entity_req.entity_type == "team":
            entity_id = registry.register_team(
                canonical_name=entity_req.canonical_name,
                aliases=entity_req.aliases,
                department=entity_req.department,
                members=entity_req.members,
            )
        else:
            continue

        entity_ids.append(entity_id)

    return {"registered": len(entity_ids), "entity_ids": entity_ids}


@router.get("/api/entities/canonical/{entity_id}")
async def get_canonical_entity(
    entity_id: str, registry: EntityRepository = Depends(get_registry)
):
    """Get canonical entity by ID or alias"""
    # Extract entity type from ID
    entity_type = extract_entity_type_from_id(entity_id)
    if not entity_type:
        raise HTTPException(
            status_code=400, detail=f"Invalid entity ID format: {entity_id}"
        )

    entity = registry.get_canonical_entity(entity_type, entity_id)

    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    return entity.model_dump()


@router.post("/api/entities/registry/suggest")
async def suggest_entities_registry(
    request: EntitySuggestionRequest, registry: EntityRepository = Depends(get_registry)
):
    """Suggest entities based on text content (legacy endpoint)"""
    suggestions = registry.suggest_entities(request.text)

    return {
        "people": [entity.model_dump() for entity in suggestions["people"]],
        "projects": [entity.model_dump() for entity in suggestions["projects"]],
        "teams": [entity.model_dump() for entity in suggestions["teams"]],
    }


@router.put("/api/entities/registry/merge")
async def merge_entities_registry(
    request: MergeEntitiesRequest, registry: EntityRepository = Depends(get_registry)
):
    """Merge two entities into one (legacy endpoint)"""
    try:
        merged_id = registry.merge_entities(
            request.entity_id_1,
            request.entity_id_2,
            canonical_name=request.canonical_name,
        )

        return {
            "merged_entity_id": merged_id,
            "canonical_name": request.canonical_name
            or registry.get_canonical_entity(merged_id).canonical_name,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/entities/registry/validate")
async def validate_entity_registry(
    request: ValidateEntityRequest, registry: EntityRepository = Depends(get_registry)
):
    """Validate if an entity exists in the registry (legacy endpoint)"""
    is_valid = registry.validate_entity(request.entity_name, request.entity_type)

    return {
        "is_valid": is_valid,
        "entity_name": request.entity_name,
        "entity_type": request.entity_type,
    }


@router.get("/api/entities/registry/search")
async def search_entities_registry(
    q: str,
    type: str | None = None,
    limit: int = 10,
    registry: EntityRepository = Depends(get_registry),
):
    """Search for entities with fuzzy matching (legacy endpoint)"""
    similar = registry.find_similar_entities(q, entity_type=type, limit=limit)

    results = []
    for entity, score in similar:
        entity_data = entity.model_dump()
        entity_data["similarity_score"] = score
        results.append(entity_data)

    return results


@router.get("/api/entities/stats")
async def get_registry_stats(registry: EntityRepository = Depends(get_registry)):
    """Get statistics about the entity registry"""
    return registry.get_stats()


# Helper function for integration tests
def get_entity_suggestions(text: str) -> dict[str, list[dict[str, Any]]]:
    """Get entity suggestions for text (used in tests)"""
    registry = get_registry()
    suggestions = registry.suggest_entities(text)

    return {
        "people": [
            {
                "name": entity.canonical_name,
                "type": "person",
                "confidence": entity.confidence,
            }
            for entity in suggestions["people"]
        ],
        "projects": [
            {
                "name": entity.canonical_name,
                "type": "project",
                "confidence": entity.confidence,
            }
            for entity in suggestions["projects"]
        ],
        "teams": [
            {
                "name": entity.canonical_name,
                "type": "team",
                "confidence": entity.confidence,
            }
            for entity in suggestions["teams"]
        ],
    }


@router.post("/api/entities/populate-from-metadata")
async def populate_registry_from_metadata(
    registry: EntityRepository = Depends(get_registry),
):
    """Populate the registry from existing document metadata"""
    from ..git_ops import git_ops
    from ..services.frontmatter import frontmatter

    stats = {
        "files_processed": 0,
        "entities_registered": {"people": 0, "projects": 0, "teams": 0},
        "errors": [],
    }

    try:
        # Get all markdown files
        files = await git_ops.read_markdown_files()

        for file_obj in files:
            try:
                # Extract metadata
                metadata = frontmatter.extract_all(file_obj.content)
                stats["files_processed"] += 1

                # Register people
                people_list = metadata.get("people", [])
                if isinstance(people_list, list):
                    for person_name in people_list:
                        if isinstance(person_name, str) and person_name.strip():
                            try:
                                # Check if already exists
                                existing = registry.get_canonical_entity(person_name)
                                if not existing:
                                    registry.register_person(
                                        canonical_name=person_name.strip()
                                    )
                                    stats["entities_registered"]["people"] += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed to register person {person_name}: {e}"
                                )

                # Register projects
                projects_list = metadata.get("projects", [])
                if isinstance(projects_list, list):
                    for project_name in projects_list:
                        if isinstance(project_name, str) and project_name.strip():
                            try:
                                # Check if already exists
                                existing = registry.get_canonical_entity(project_name)
                                if not existing:
                                    registry.register_project(
                                        canonical_name=project_name.strip()
                                    )
                                    stats["entities_registered"]["projects"] += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed to register project {project_name}: {e}"
                                )

                # Register teams
                teams_list = metadata.get("teams", [])
                if isinstance(teams_list, list):
                    for team_name in teams_list:
                        if isinstance(team_name, str) and team_name.strip():
                            try:
                                # Check if already exists
                                existing = registry.get_canonical_entity(team_name)
                                if not existing:
                                    registry.register_team(
                                        canonical_name=team_name.strip()
                                    )
                                    stats["entities_registered"]["teams"] += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed to register team {team_name}: {e}"
                                )

            except Exception as e:
                error_msg = f"Error processing {file_obj.path}: {str(e)}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)

        # Save registry
        registry.save()

        return {
            "success": True,
            "stats": stats,
            "message": f"Registry populated with {sum(stats['entities_registered'].values())} entities",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to populate registry: {str(e)}"
        )
