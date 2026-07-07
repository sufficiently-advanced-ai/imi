"""
Entity deduplication service for batch uploads
"""

import re
from difflib import SequenceMatcher
from typing import Any

from app.models import EntityType

from .entity_utils import extract_entity_type_from_id

# Import Entity class at runtime to avoid circular imports
Entity = None


class EntityDeduplicator:
    """Service for deduplicating entities across multiple sources"""

    def __init__(self, registry=None):
        self.similarity_threshold = 0.85  # For fuzzy matching
        self.registry = registry  # Optional EntityRegistry for canonical resolution

    def deduplicate(self, entities: list[Any]) -> list[Any]:
        """
        Deduplicate entities using multiple strategies

        Args:
            entities: List of entities to deduplicate

        Returns:
            List of unique entities
        """
        if not entities:
            return []

        # Group by entity type
        by_type = self._group_by_type(entities)

        # Deduplicate each type
        deduplicated = []
        for entity_type, type_entities in by_type.items():
            if entity_type == EntityType.PERSON:
                deduplicated.extend(self._deduplicate_people(type_entities))
            elif entity_type == EntityType.PROJECT:
                deduplicated.extend(self._deduplicate_projects(type_entities))
            elif entity_type == EntityType.TEAM:
                deduplicated.extend(self._deduplicate_teams(type_entities))
            else:
                deduplicated.extend(type_entities)

        return deduplicated

    def _group_by_type(self, entities: list[Any]) -> dict[EntityType, list[Any]]:
        """Group entities by type"""
        groups = {}
        for entity in entities:
            if entity.type not in groups:
                groups[entity.type] = []
            groups[entity.type].append(entity)
        return groups

    def _deduplicate_people(self, people: list[Any]) -> list[Any]:
        """Deduplicate people using email and name matching"""
        # If registry is available, use it for canonical resolution
        if self.registry:
            canonical_groups = {}  # canonical_id -> list of entities

            for person in people:
                # Look up canonical entity
                # Check if person.name has entity type prefix
                entity_type = extract_entity_type_from_id(person.name)
                if entity_type:
                    canonical = self.registry.get_canonical_entity(
                        entity_type, person.name
                    )
                else:
                    # Assume it's a person if no type prefix
                    canonical = self.registry.get_canonical_entity(
                        "person", person.name
                    )

                if canonical:
                    canonical_id = canonical.id
                    canonical_name = canonical.canonical_name
                else:
                    # Register new person
                    canonical_id = self.registry.register_person(
                        canonical_name=person.name,
                        email=person.email
                        if hasattr(person, "email") and person.email
                        else None,
                    )
                    canonical = self.registry.get_canonical_entity(
                        "person", canonical_id
                    )
                    canonical_name = canonical.canonical_name

                # Update person name to canonical form
                person.name = canonical_name

                # Group by canonical ID
                if canonical_id not in canonical_groups:
                    canonical_groups[canonical_id] = []
                canonical_groups[canonical_id].append(person)

            # Merge each canonical group
            return [
                self._merge_entity_group(group) for group in canonical_groups.values()
            ]

        # Fallback to original logic if no registry
        # Group by email first
        by_email = {}
        no_email = []

        for person in people:
            if person.email:
                if person.email not in by_email:
                    by_email[person.email] = []
                by_email[person.email].append(person)
            else:
                no_email.append(person)

        # Merge people with same email
        merged = []
        for _email, group in by_email.items():
            merged.append(self._merge_entity_group(group))

        # Handle people without email using fuzzy name matching
        for person in no_email:
            # Check if similar to any already merged
            matched = False
            for existing in merged:
                if self._are_names_similar(person.name, existing.name):
                    # Merge contexts
                    existing.context.update(person.context)
                    matched = True
                    break

            if not matched:
                merged.append(person)

        return merged

    def _deduplicate_projects(self, projects: list[Any]) -> list[Any]:
        """Deduplicate projects by exact name match"""
        # If registry is available, use it for canonical resolution
        if self.registry:
            canonical_groups = {}  # canonical_id -> list of entities

            for project in projects:
                # Look up canonical entity
                # Check if project.name has entity type prefix
                entity_type = extract_entity_type_from_id(project.name)
                if entity_type:
                    canonical = self.registry.get_canonical_entity(
                        entity_type, project.name
                    )
                else:
                    # Assume it's a project if no type prefix
                    canonical = self.registry.get_canonical_entity(
                        "project", project.name
                    )

                if canonical:
                    canonical_id = canonical.id
                    canonical_name = canonical.canonical_name
                else:
                    # Register new project
                    canonical_id = self.registry.register_project(
                        canonical_name=project.name,
                        status=project.status
                        if hasattr(project, "status")
                        else "active",
                    )
                    canonical = self.registry.get_canonical_entity(
                        "project", canonical_id
                    )
                    canonical_name = canonical.canonical_name

                # Update project name to canonical form
                project.name = canonical_name

                # Group by canonical ID
                if canonical_id not in canonical_groups:
                    canonical_groups[canonical_id] = []
                canonical_groups[canonical_id].append(project)

            # Merge each canonical group
            return [
                self._merge_entity_group(group) for group in canonical_groups.values()
            ]

        # Fallback to original logic
        by_name = {}

        for project in projects:
            name_lower = project.name.lower().strip()
            if name_lower not in by_name:
                by_name[name_lower] = []
            by_name[name_lower].append(project)

        # Merge each group
        return [self._merge_entity_group(group) for group in by_name.values()]

    def _deduplicate_teams(self, teams: list[Any]) -> list[Any]:
        """Deduplicate teams by case-insensitive name match"""
        by_name = {}

        for team in teams:
            name_lower = team.name.lower().strip()
            if name_lower not in by_name:
                by_name[name_lower] = []
            by_name[name_lower].append(team)

        # Merge each group
        return [self._merge_entity_group(group) for group in by_name.values()]

    def _merge_entity_group(self, group: list[Any]) -> Any:
        """Merge a group of entities into one canonical entity"""
        if len(group) == 1:
            return group[0]

        # Choose the most complete name
        canonical = max(group, key=lambda e: len(e.name))

        # Merge all contexts
        merged_context = {}
        for entity in group:
            merged_context.update(entity.context)
            # Track all sources
            if "sources" not in merged_context:
                merged_context["sources"] = []
            if "source" in entity.context:
                merged_context["sources"].append(entity.context["source"])

        canonical.context = merged_context
        return canonical

    def _are_names_similar(self, name1: str, name2: str) -> bool:
        """Check if two names are similar enough to be the same person"""
        # Normalize names
        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)

        # Exact match after normalization
        if norm1 == norm2:
            return True

        # Check common variations
        if self._are_name_variations(name1, name2):
            return True

        # Fuzzy matching
        similarity = SequenceMatcher(None, norm1, norm2).ratio()
        return similarity >= self.similarity_threshold

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison"""
        # Remove titles
        name = re.sub(r"\b(Mr|Mrs|Ms|Dr|Prof)\.?\s+", "", name, flags=re.IGNORECASE)
        # Remove extra spaces
        name = " ".join(name.split())
        # Lowercase
        return name.lower().strip()

    def _are_name_variations(self, name1: str, name2: str) -> bool:
        """Check for common name variations"""
        # Common nicknames
        nicknames = {
            "robert": ["bob", "rob"],
            "william": ["bill", "will"],
            "james": ["jim", "jimmy"],
            "john": ["jack"],
            "richard": ["dick", "rick"],
            "michael": ["mike"],
            "elizabeth": ["liz", "beth"],
            "jennifer": ["jen", "jenny"],
            "patricia": ["pat", "patty"],
        }

        parts1 = name1.lower().split()
        parts2 = name2.lower().split()

        # Check if one is abbreviation of the other
        if len(parts1) == len(parts2):
            for p1, p2 in zip(parts1, parts2, strict=False):
                # Check initials
                if len(p1) == 1 and p2.startswith(p1):
                    continue
                elif len(p2) == 1 and p1.startswith(p2):
                    continue
                # Check nicknames
                elif p1 in nicknames and p2 in nicknames[p1]:
                    continue
                elif p2 in nicknames and p1 in nicknames[p2]:
                    continue
                elif p1 != p2:
                    return False
            return True

        return False
