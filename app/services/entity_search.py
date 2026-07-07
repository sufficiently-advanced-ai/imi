"""
Entity Search Service - Issue #244.

This service provides advanced search and filtering capabilities
for entities, including full-text search, attribute filtering,
relationship filtering, and performance optimization.
"""

import logging
from typing import Any

from dateutil import parser as date_parser

from app.core.dependencies import get_entity_repository
from app.domain.entities.services import EntityRepository

from ..model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)


class EntitySearchService:
    """Service for searching and filtering entities."""

    def __init__(
        self,
        domain_config: DomainConfiguration | None = None,
        entity_registry: EntityRepository | None = None,
    ):
        """
        Initialize the search service.

        Args:
            domain_config: Domain configuration to use
            entity_registry: Entity registry instance
        """
        self.domain_config = domain_config
        self.entity_registry = entity_registry or get_entity_repository()

        # Entity storage for search (would be replaced with proper storage in production)
        self._entities: dict[str, dict[str, Any]] = {}

        # Load domain config into registry if provided
        if domain_config:
            self.entity_registry.load_domain_config(domain_config)

    async def search_entities(
        self,
        search_query: str | None = None,
        filters: dict[str, Any | None] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Search entities with text query, filters, and sorting.

        Args:
            search_query: Text to search for
            filters: Filters to apply
            sort_by: Field to sort by
            sort_order: Sort direction
            limit: Maximum results to return
            offset: Number of results to skip

        Returns:
            Search results with entities and metadata
        """
        try:
            # Get all entities
            all_entities = self._get_all_entities()

            # Apply text search
            if search_query:
                all_entities = self._apply_text_search(all_entities, search_query)

            # Apply filters
            if filters:
                all_entities = self._apply_filters(all_entities, filters)

            # Apply sorting
            all_entities = self._apply_sorting(all_entities, sort_by, sort_order)

            # Count total matches before pagination
            total_matches = len(all_entities)

            # Apply pagination
            paginated_entities = all_entities[offset : offset + limit]

            return {
                "entities": paginated_entities,
                "total_matches": total_matches,
                "search_terms": search_query.split() if search_query else [],
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            logger.error(f"Error searching entities: {e}")
            return {
                "entities": [],
                "total_matches": 0,
                "search_terms": [],
                "limit": limit,
                "offset": offset,
                "error": str(e),
            }

    async def filter_by_entity_type(self, entity_type: str) -> list[dict[str, Any]]:
        """
        Get all entities of a specific type.

        Args:
            entity_type: The entity type to filter by

        Returns:
            List of entities of the specified type
        """
        all_entities = self._get_all_entities()
        return [
            entity
            for entity in all_entities
            if entity.get("entity_type") == entity_type
        ]

    async def filter_by_attributes(
        self, attribute_filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Filter entities by specific attribute values.

        Args:
            attribute_filters: Dictionary of attribute filters

        Returns:
            List of matching entities
        """
        all_entities = self._get_all_entities()
        filtered = []

        for entity in all_entities:
            attributes = entity.get("attributes", {})
            match = True

            for attr_name, expected_value in attribute_filters.items():
                actual_value = attributes.get(attr_name)
                if actual_value != expected_value:
                    match = False
                    break

            if match:
                filtered.append(entity)

        return filtered

    async def filter_by_date_range(
        self, field_name: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """
        Filter entities by date range.

        Args:
            field_name: Name of the date field
            start_date: Start date string
            end_date: End date string

        Returns:
            List of entities within the date range
        """
        try:
            start_dt = date_parser.parse(start_date).date()
            end_dt = date_parser.parse(end_date).date()

            all_entities = self._get_all_entities()
            filtered = []

            for entity in all_entities:
                attributes = entity.get("attributes", {})
                if field_name in attributes:
                    try:
                        entity_date = date_parser.parse(attributes[field_name]).date()
                        if start_dt <= entity_date <= end_dt:
                            filtered.append(entity)
                    except (ValueError, TypeError):
                        # Skip entities with invalid date format
                        continue

            return filtered

        except (ValueError, TypeError) as e:
            logger.error(f"Invalid date range: {e}")
            return []

    async def filter_by_numeric_range(
        self, field_name: str, min_value: float, max_value: float
    ) -> list[dict[str, Any]]:
        """
        Filter entities by numeric range.

        Args:
            field_name: Name of the numeric field
            min_value: Minimum value
            max_value: Maximum value

        Returns:
            List of entities within the numeric range
        """
        all_entities = self._get_all_entities()
        filtered = []

        for entity in all_entities:
            attributes = entity.get("attributes", {})
            if field_name in attributes:
                try:
                    entity_value = float(attributes[field_name])
                    if min_value <= entity_value <= max_value:
                        filtered.append(entity)
                except (ValueError, TypeError):
                    # Skip entities with non-numeric values
                    continue

        return filtered

    async def filter_by_relationships(
        self, relationship_filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Filter entities by their relationships.

        Args:
            relationship_filters: Dictionary of relationship filters

        Returns:
            List of entities matching relationship criteria
        """
        all_entities = self._get_all_entities()
        filtered = []

        for entity in all_entities:
            relationships = entity.get("relationships", {})
            match = True

            for rel_name, expected_targets in relationship_filters.items():
                actual_targets = relationships.get(rel_name, [])

                # Ensure actual_targets is a list
                if not isinstance(actual_targets, list):
                    actual_targets = [actual_targets]

                # Ensure expected_targets is a list
                if not isinstance(expected_targets, list):
                    expected_targets = [expected_targets]

                # Check if any expected target is in actual targets
                if not any(target in actual_targets for target in expected_targets):
                    match = False
                    break

            if match:
                filtered.append(entity)

        return filtered

    def _get_all_entities(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """Get all entities from storage."""
        all_entities = []
        for _entity_type, entities in self._entities.items():
            for _entity_id, entity in entities.items():
                # Skip archived entities unless explicitly requested
                if include_archived or not entity.get("is_archived", False):
                    all_entities.append(entity)
        return all_entities

    def _apply_text_search(
        self, entities: list[dict[str, Any]], search_query: str
    ) -> list[dict[str, Any]]:
        """Apply full-text search to entities."""
        search_terms = search_query.lower().split()
        filtered = []

        for entity in entities:
            # Build searchable text content
            searchable_text = []

            # Add entity ID and type
            searchable_text.append(entity.get("id", "").lower())
            searchable_text.append(entity.get("entity_type", "").lower())

            # Add all string attributes
            attributes = entity.get("attributes", {})
            for key, value in attributes.items():
                if isinstance(value, str):
                    searchable_text.append(f"{key}:{value}".lower())
                    searchable_text.append(value.lower())

            # Join all searchable text
            full_text = " ".join(searchable_text)

            # Check if all search terms are present
            if all(term in full_text for term in search_terms):
                filtered.append(entity)

        return filtered

    def _apply_filters(
        self, entities: list[dict[str, Any]], filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Apply various filters to entities."""
        filtered = []

        for entity in entities:
            match = True

            # Entity type filter
            if "entity_type" in filters:
                if entity.get("entity_type") != filters["entity_type"]:
                    match = False
                    continue

            # Attribute filters
            attributes = entity.get("attributes", {})

            for filter_key, filter_value in filters.items():
                if filter_key == "entity_type":
                    continue

                # Date range filters
                if filter_key.endswith("_from") or filter_key.endswith("_to"):
                    match = self._apply_date_range_filter(
                        attributes, filter_key, filter_value
                    )
                    if not match:
                        break
                    continue

                # Numeric range filters
                if filter_key.endswith("_min") or filter_key.endswith("_max"):
                    match = self._apply_numeric_range_filter(
                        attributes, filter_key, filter_value
                    )
                    if not match:
                        break
                    continue

                # Relationship filters
                relationships = entity.get("relationships", {})
                if filter_key in relationships:
                    rel_values = relationships[filter_key]
                    if not isinstance(rel_values, list):
                        rel_values = [rel_values]

                    if isinstance(filter_value, list):
                        # Any of the filter values should be in relationship values
                        if not any(fv in rel_values for fv in filter_value):
                            match = False
                            break
                    else:
                        # Single filter value should be in relationship values
                        if filter_value not in rel_values:
                            match = False
                            break
                    continue

                # Direct attribute filter
                if filter_key in attributes:
                    if attributes[filter_key] != filter_value:
                        match = False
                        break

            if match:
                filtered.append(entity)

        return filtered

    def _apply_date_range_filter(
        self, attributes: dict[str, Any], filter_key: str, filter_value: Any
    ) -> bool:
        """Apply date range filter to attributes."""
        try:
            if filter_key.endswith("_from"):
                base_field = filter_key[:-5]  # Remove "_from"
                if base_field in attributes:
                    entity_date = date_parser.parse(attributes[base_field]).date()
                    filter_date = date_parser.parse(filter_value).date()
                    return entity_date >= filter_date
            elif filter_key.endswith("_to"):
                base_field = filter_key[:-3]  # Remove "_to"
                if base_field in attributes:
                    entity_date = date_parser.parse(attributes[base_field]).date()
                    filter_date = date_parser.parse(filter_value).date()
                    return entity_date <= filter_date
        except (ValueError, TypeError, KeyError):
            return False

        return True

    def _apply_numeric_range_filter(
        self, attributes: dict[str, Any], filter_key: str, filter_value: Any
    ) -> bool:
        """Apply numeric range filter to attributes."""
        try:
            if filter_key.endswith("_min"):
                base_field = filter_key[:-4]  # Remove "_min"
                if base_field in attributes:
                    entity_value = float(attributes[base_field])
                    return entity_value >= float(filter_value)
            elif filter_key.endswith("_max"):
                base_field = filter_key[:-4]  # Remove "_max"
                if base_field in attributes:
                    entity_value = float(attributes[base_field])
                    return entity_value <= float(filter_value)
        except (ValueError, TypeError, KeyError):
            return False

        return True

    def _apply_sorting(
        self, entities: list[dict[str, Any]], sort_by: str, sort_order: str
    ) -> list[dict[str, Any]]:
        """Apply sorting to entities."""
        reverse = sort_order.lower() == "desc"

        def get_sort_key(entity):
            # Entity-level fields
            if sort_by in entity:
                value = entity[sort_by]
                return value if value is not None else ""

            # Attribute fields
            attributes = entity.get("attributes", {})
            if sort_by in attributes:
                value = attributes[sort_by]
                return value if value is not None else ""

            return ""

        try:
            return sorted(entities, key=get_sort_key, reverse=reverse)
        except Exception as e:
            logger.error(f"Error sorting entities by {sort_by}: {e}")
            return entities

    def add_entity_to_search_index(self, entity: dict[str, Any]) -> None:
        """
        Add an entity to the search index.

        Args:
            entity: Entity to add
        """
        entity_type = entity.get("entity_type")
        entity_id = entity.get("id")

        if not entity_type or not entity_id:
            logger.warning("Cannot index entity without type or ID")
            return

        if entity_type not in self._entities:
            self._entities[entity_type] = {}

        self._entities[entity_type][entity_id] = entity

    def remove_entity_from_search_index(self, entity_id: str) -> None:
        """
        Remove an entity from the search index.

        Args:
            entity_id: ID of entity to remove
        """
        for _entity_type, entities in self._entities.items():
            if entity_id in entities:
                del entities[entity_id]
                break

    def get_search_statistics(self) -> dict[str, Any]:
        """
        Get search index statistics.

        Returns:
            Dictionary with search statistics
        """
        total_entities = sum(len(entities) for entities in self._entities.values())
        entity_type_counts = {
            entity_type: len(entities)
            for entity_type, entities in self._entities.items()
        }

        return {
            "total_entities": total_entities,
            "entity_types": len(self._entities),
            "entity_type_counts": entity_type_counts,
            "searchable_fields": self._get_searchable_fields(),
        }

    def _get_searchable_fields(self) -> list[str]:
        """Get list of fields that can be searched."""
        searchable = set()

        for _entity_type, entities in self._entities.items():
            for _entity_id, entity in entities.items():
                # Add entity-level fields
                for key in entity.keys():
                    searchable.add(key)

                # Add attribute fields
                attributes = entity.get("attributes", {})
                for attr_name in attributes.keys():
                    searchable.add(attr_name)

        return sorted(list(searchable))
