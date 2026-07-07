"""Entity Search & Discovery API routes - Issue #60"""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..domain.entities.services import get_entity_repository
from ..models import (
    EntityAutocompleteResponse,
    EntitySearchResponse,
    EntitySearchResult,
    EntityType,
    SuggestedEntity,
)

router = APIRouter(prefix="/api/entities", tags=["entity-search"])


def _calculate_match_type(query: str, entity_name: str, aliases: list[str]) -> str:
    """Determine the type of match between query and entity"""
    query_lower = query.lower()
    name_lower = entity_name.lower()

    # Exact match
    if query_lower == name_lower:
        return "exact"

    # Alias match
    for alias in aliases:
        if query_lower == alias.lower():
            return "alias"

    # Partial match
    if query_lower in name_lower or name_lower in query_lower:
        return "partial"

    # Default to fuzzy
    return "fuzzy"


def _calculate_relevance_score(entity, query: str) -> float:
    """Calculate relevance score for search result"""
    query_lower = query.lower()
    name_lower = entity.canonical_name.lower()

    # Exact match gets highest score
    if query_lower == name_lower:
        return 1.0

    # Check aliases
    for alias in entity.aliases:
        if query_lower == alias.lower():
            return 0.95

    # Partial matches
    if query_lower in name_lower:
        return 0.8 + (len(query_lower) / len(name_lower)) * 0.15

    if name_lower in query_lower:
        return 0.7 + (len(name_lower) / len(query_lower)) * 0.1

    # Word-based matching
    query_words = set(query_lower.split())
    name_words = set(name_lower.split())
    common_words = query_words.intersection(name_words)

    if common_words:
        return 0.5 + (len(common_words) / max(len(query_words), len(name_words))) * 0.3

    # Default low score for fuzzy matches
    return 0.3


@router.get("/search", response_model=EntitySearchResponse)
async def search_entities(
    query: str = Query(..., min_length=1, description="Search query"),
    entity_types: list[str] | None = Query(
        default=None, description="Filter by entity types"
    ),
    limit: int = Query(default=50, le=100, description="Maximum results to return"),
    include_aliases: bool = Query(default=True, description="Include alias matches"),
    confidence_threshold: float = Query(
        default=0.0, ge=0.0, le=1.0, description="Minimum confidence score"
    ),
):
    """Search for entities across all types with advanced filtering"""
    start_time = time.time()

    try:
        registry = get_entity_repository()
        results = []

        # Search across all entity types or filtered types
        if entity_types:
            # Convert string types to EntityType enum
            search_types = []
            for type_str in entity_types:
                try:
                    search_types.append(EntityType(type_str))
                except ValueError:
                    # Skip invalid entity types
                    continue
        else:
            search_types = [EntityType.PERSON, EntityType.PROJECT, EntityType.TEAM]

        for entity_type in search_types:
            # Get similar entities for this type
            type_str = entity_type.value
            similar_entities = registry.find_similar_entities(
                query,
                entity_type=type_str,
                limit=limit * 2,  # Get more to filter later
            )

            for entity, _similarity_score in similar_entities:
                # Skip if below confidence threshold
                if entity.confidence < confidence_threshold:
                    continue

                # Skip if not including aliases and match is via alias
                match_type = _calculate_match_type(
                    query, entity.canonical_name, entity.aliases
                )
                if not include_aliases and match_type == "alias":
                    continue

                # Calculate relevance score
                relevance = _calculate_relevance_score(entity, query)

                # Create search result
                result = EntitySearchResult(
                    entity_id=entity.id,
                    entity_type=entity_type,
                    canonical_name=entity.canonical_name,
                    aliases=entity.aliases,
                    confidence_score=entity.confidence,
                    match_type=match_type,
                    last_activity=entity.last_seen,
                    document_count=0,  # TODO: Get from activity tracking
                    relevance_score=relevance,
                )
                results.append(result)

        # Sort by relevance and limit
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        results = results[:limit]

        # Generate suggestions for typos or alternatives
        suggestions = []
        if len(results) < 5:
            # Try to find alternative queries
            all_entities = registry.get_all_entities()
            all_names = []
            for entity_dict in all_entities.values():
                for entity in entity_dict.values():
                    all_names.append(entity.canonical_name)
                    all_names.extend(entity.aliases)

            # Simple suggestion: find names that start with query
            query_lower = query.lower()
            for name in all_names:
                if name.lower().startswith(query_lower) and name not in suggestions:
                    suggestions.append(name)
                    if len(suggestions) >= 3:
                        break

        search_time_ms = (time.time() - start_time) * 1000

        return EntitySearchResponse(
            results=results,
            total_results=len(results),
            query=query,
            suggestions=suggestions,
            search_time_ms=search_time_ms,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


class EntitySuggestRequest(BaseModel):
    """Request for entity suggestions from text"""

    text: str
    context: str | None = None
    max_suggestions: int = 10


@router.post("/suggest", response_model=dict)
async def suggest_entities_from_text(request: EntitySuggestRequest):
    """Extract and suggest entities from natural text"""
    try:
        registry = get_entity_repository()

        # Get suggestions from registry
        raw_suggestions = registry.suggest_entities(request.text)

        # Add context consideration if provided
        if request.context:
            # Enhance suggestions based on context
            context_suggestions = registry.suggest_entities(request.context)

            # Merge and prioritize suggestions
            for entity_type, entities in context_suggestions.items():
                for entity in entities:
                    # Boost confidence if entity appears in both text and context
                    if any(
                        e.id == entity.id for e in raw_suggestions.get(entity_type, [])
                    ):
                        entity.confidence = min(entity.confidence * 1.1, 1.0)

        # Convert to response format
        suggestions = {"people": [], "projects": [], "teams": []}

        # Limit suggestions per type
        max_per_type = max(request.max_suggestions // 3, 1)

        for entity_type, entities in raw_suggestions.items():
            limited_entities = entities[:max_per_type]

            for entity in limited_entities:
                suggested = SuggestedEntity(
                    id=entity.id,
                    canonical_name=entity.canonical_name,
                    entity_type=EntityType(entity_type[:-1]),  # Remove 's' from plural
                    confidence=entity.confidence,
                    aliases=entity.aliases,
                )
                suggestions[entity_type].append(suggested)

        return {"suggestions": suggestions}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to suggest entities: {str(e)}"
        )


@router.get("/autocomplete", response_model=EntityAutocompleteResponse)
async def autocomplete_entities(
    partial: str = Query(..., min_length=1, description="Partial text to complete"),
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    limit: int = Query(default=10, le=20, description="Maximum suggestions"),
):
    """Autocomplete entity names based on partial input"""
    try:
        registry = get_entity_repository()
        suggestions = []

        partial_lower = partial.lower()

        # Determine which entity types to search
        if entity_type:
            try:
                entity_types = [EntityType(entity_type)]
            except ValueError:
                # Invalid entity type, use all types as fallback
                entity_types = [EntityType.PERSON, EntityType.PROJECT, EntityType.TEAM]
        else:
            entity_types = [EntityType.PERSON, EntityType.PROJECT, EntityType.TEAM]

        # Search each entity type
        for e_type in entity_types:
            collection_name = (
                f"{e_type.value}s"  # e.g., "persons" -> but we need "people"
            )
            if e_type == EntityType.PERSON:
                collection_name = "people"
            elif e_type == EntityType.PROJECT:
                collection_name = "projects"
            elif e_type == EntityType.TEAM:
                collection_name = "teams"

            # Get all entities of this type
            all_entities = registry.get_all_entities()
            entities = all_entities.get(collection_name, {})

            for entity in entities.values():
                # Check canonical name
                if entity.canonical_name.lower().startswith(partial_lower):
                    suggestion = SuggestedEntity(
                        id=entity.id,
                        canonical_name=entity.canonical_name,
                        entity_type=e_type,
                        confidence=entity.confidence,
                        aliases=entity.aliases,
                        relevance_score=1.0,  # Exact prefix match
                    )
                    suggestions.append(suggestion)
                    continue

                # Check aliases
                for alias in entity.aliases:
                    if alias.lower().startswith(partial_lower):
                        suggestion = SuggestedEntity(
                            id=entity.id,
                            canonical_name=entity.canonical_name,
                            entity_type=e_type,
                            confidence=entity.confidence,
                            aliases=entity.aliases,
                            relevance_score=0.9,  # Alias match
                            match_context=f"Matched alias: {alias}",
                        )
                        suggestions.append(suggestion)
                        break

                # Check word boundaries
                words = entity.canonical_name.lower().split()
                for word in words:
                    if word.startswith(partial_lower):
                        suggestion = SuggestedEntity(
                            id=entity.id,
                            canonical_name=entity.canonical_name,
                            entity_type=e_type,
                            confidence=entity.confidence,
                            aliases=entity.aliases,
                            relevance_score=0.7,  # Word match
                        )
                        if suggestion not in suggestions:
                            suggestions.append(suggestion)
                        break

        # Sort by relevance and limit
        suggestions.sort(key=lambda s: s.relevance_score, reverse=True)
        suggestions = suggestions[:limit]

        return EntityAutocompleteResponse(
            suggestions=suggestions, query=partial, total_matches=len(suggestions)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Autocomplete failed: {str(e)}")
