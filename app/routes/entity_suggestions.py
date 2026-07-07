"""Entity suggestion API routes - Issue #58"""

import time

from fastapi import APIRouter, HTTPException, Path

from ..domain.entities.services import get_entity_repository
from ..models import (
    EntitySuggestionRequest,
    EntitySuggestionResponse,
    EntityType,
    SuggestedEntity,
)

router = APIRouter(prefix="/api/entities", tags=["entities"])


def _calculate_relevance_score(
    text: str, entity_name: str, aliases: list[str]
) -> float:
    """Calculate relevance score based on mentions in text"""
    text_lower = text.lower()
    score = 0.0

    # Count exact matches
    name_count = text_lower.count(entity_name.lower())
    score += name_count * 1.0

    # Count alias matches (slightly lower weight)
    for alias in aliases:
        alias_count = text_lower.count(alias.lower())
        score += alias_count * 0.8

    # Normalize by text length (per 1000 chars)
    if len(text) > 0:
        score = (score * 1000) / len(text)

    # Cap at 1.0
    return min(score, 1.0)


def _convert_to_suggested_entity(
    entity, entity_type: EntityType, text: str, include_context: bool = False
) -> SuggestedEntity:
    """Convert canonical entity to suggestion format"""
    relevance = _calculate_relevance_score(text, entity.canonical_name, entity.aliases)

    suggestion = SuggestedEntity(
        id=entity.id,
        canonical_name=entity.canonical_name,
        entity_type=entity_type,
        confidence=entity.confidence,
        aliases=entity.aliases,
        relevance_score=relevance,
    )

    if include_context:
        context = {}

        # Add type-specific context
        if hasattr(entity, "titles") and entity.titles:
            context["titles"] = entity.titles
            # Add title to match reason if found in text
            text_lower = text.lower()
            for title in entity.titles:
                if title.lower() in text_lower:
                    suggestion.match_reason = f"Title '{title}' found in text"
                    break

        if hasattr(entity, "email") and entity.email:
            context["email"] = entity.email

        if hasattr(entity, "departments") and entity.departments:
            context["departments"] = entity.departments

        if hasattr(entity, "status") and entity.status:
            context["status"] = entity.status

        if hasattr(entity, "teams") and entity.teams:
            context["teams"] = entity.teams

        if context:
            suggestion.context = context

    return suggestion


@router.post("/suggest", response_model=EntitySuggestionResponse)
async def suggest_entities(request: EntitySuggestionRequest):
    """Get entity suggestions based on text content"""
    start_time = time.time()

    try:
        # Get entity registry
        registry = get_entity_repository()

        # Get raw suggestions from registry
        raw_suggestions = registry.suggest_entities(request.text)

        # Convert to response format
        suggestions = {"people": [], "projects": [], "teams": []}

        # Filter by requested entity types
        entity_types = request.entity_types or [
            EntityType.PERSON,
            EntityType.PROJECT,
            EntityType.TEAM,
        ]

        # Process people
        if EntityType.PERSON in entity_types:
            for person in raw_suggestions.get("people", []):
                if person.confidence >= request.min_confidence:
                    suggestion = _convert_to_suggested_entity(
                        person, EntityType.PERSON, request.text, request.include_context
                    )
                    suggestions["people"].append(suggestion)

        # Process projects
        if EntityType.PROJECT in entity_types:
            for project in raw_suggestions.get("projects", []):
                if project.confidence >= request.min_confidence:
                    suggestion = _convert_to_suggested_entity(
                        project,
                        EntityType.PROJECT,
                        request.text,
                        request.include_context,
                    )
                    suggestions["projects"].append(suggestion)

        # Process teams
        if EntityType.TEAM in entity_types:
            for team in raw_suggestions.get("teams", []):
                if team.confidence >= request.min_confidence:
                    suggestion = _convert_to_suggested_entity(
                        team, EntityType.TEAM, request.text, request.include_context
                    )
                    suggestions["teams"].append(suggestion)

        # Sort by relevance and apply limit
        for entity_type in ["people", "projects", "teams"]:
            suggestions[entity_type].sort(key=lambda x: x.relevance_score, reverse=True)
            if request.limit:
                suggestions[entity_type] = suggestions[entity_type][: request.limit]

        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        return EntitySuggestionResponse(
            suggestions=suggestions, response_time_ms=response_time_ms
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get entity suggestions: {str(e)}"
        )


@router.get("/validate/{entity_type}/{entity_name}")
async def validate_entity(
    entity_type: str = Path(..., description="Type of entity to validate"),
    entity_name: str = Path(..., description="Name of entity to validate"),
):
    """Validate if an entity exists in the registry"""
    try:
        registry = get_entity_repository()

        # Get entity with confidence
        entity, confidence = registry.get_canonical_entity_with_confidence(entity_name)

        if not entity:
            return {
                "valid": False,
                "confidence": 0.0,
                "canonical_id": None,
                "canonical_name": None,
            }

        # Verify type matches
        actual_type = None
        if entity.id.startswith("person-"):
            actual_type = "person"
        elif entity.id.startswith("project-"):
            actual_type = "project"
        elif entity.id.startswith("team-"):
            actual_type = "team"

        if actual_type and actual_type.value != entity_type:
            return {
                "valid": False,
                "confidence": 0.0,
                "canonical_id": None,
                "canonical_name": None,
                "error": f"Entity exists but is type {actual_type.value}, not {entity_type}",
            }

        return {
            "valid": True,
            "confidence": confidence,
            "canonical_id": entity.id,
            "canonical_name": entity.canonical_name,
            "aliases": entity.aliases,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to validate entity: {str(e)}"
        )
