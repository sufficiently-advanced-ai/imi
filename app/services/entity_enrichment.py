"""Entity Enrichment Engine for building entity profiles with relationships - Issue #59"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.entities.services import EntityRepository
from app.git_ops import GitOperations
from app.models import (
    CanonicalPerson,
    CanonicalProject,
    CanonicalTeam,
    EnrichedEntity,
    RelationshipType,
)
from app.models import (
    EnrichedEntityRelationship as EntityRelationship,
)

# OrganizationalContext was historically in app.models.meeting.intelligence but
# is used by this core entity service which runs in both kb and full mode.  It
# has been moved to app.models.entity.enrichment (kb-safe, no meeting imports).
from app.models.entity.enrichment import OrganizationalContext
from app.services.claude_client import ClaudeClient
from app.services.entity_utils import extract_entity_type_from_id

logger = logging.getLogger(__name__)

# Global enrichment engine instance
_enrichment_engine = None


def get_entity_enrichment_service() -> "EntityEnrichmentEngine":
    """Get or create the global entity enrichment engine instance - Issue #60"""
    global _enrichment_engine
    if _enrichment_engine is None:
        from app.domain.entities.services import get_entity_repository
        from app.git_ops import git_ops

        _enrichment_engine = EntityEnrichmentEngine(
            entity_registry=get_entity_repository(), git_ops=git_ops
        )
    return _enrichment_engine


class EntityMention:
    """Represents a mention of entities in a document"""

    def __init__(
        self,
        file_path: str,
        entity_ids: list[str],
        content: str,
        metadata: dict[str, Any],
    ):
        self.file_path = file_path
        self.entity_ids = entity_ids
        self.content = content
        self.metadata = metadata


class EntityEnrichmentEngine:
    """Engine for enriching entities with relationships and organizational context"""

    def __init__(
        self,
        entity_registry: EntityRepository,
        git_ops: GitOperations,
        claude_client: ClaudeClient | None = None,
    ):
        self.entity_registry = entity_registry
        self.git_ops = git_ops
        self.claude_client = claude_client
        self._cache: dict[str, EnrichedEntity] = {}
        self._cache_ttl = timedelta(minutes=15)

    async def enrich_entity(
        self,
        entity_id: str,
        sources: list[str] | None = None,
        fields: list[str] | None = None,
        confidence_threshold: float = 0.7,
    ) -> EnrichedEntity:
        """
        Enrich an entity with relationships and organizational context

        Args:
            entity_id: ID of the entity to enrich

        Returns:
            EnrichedEntity with relationships and context
        """
        # Check cache
        if entity_id in self._cache:
            cached = self._cache[entity_id]
            if datetime.now(UTC) - cached.last_enriched < self._cache_ttl:
                return cached

        # Get base entity
        # Extract entity type from ID
        entity_type = extract_entity_type_from_id(entity_id)
        if not entity_type:
            raise ValueError(f"Invalid entity ID format: {entity_id}")

        base_entity = self.entity_registry.get_canonical_entity(entity_type, entity_id)
        if not base_entity:
            raise ValueError(f"Entity not found: {entity_id}")

        # Collect mentions across documents
        mentions = await self._collect_entity_mentions(entity_id)

        # Infer relationships
        relationships = await self._infer_relationships(entity_id, mentions)

        # Build organizational context for people
        org_context = None
        if isinstance(base_entity, CanonicalPerson):
            org_context = await self._build_organizational_context(
                entity_id, relationships
            )

        # Create enriched entity
        enriched = EnrichedEntity(
            base_entity=base_entity,
            relationships=relationships,
            organizational_context=org_context,
            last_enriched=datetime.now(UTC),
            enrichment_version="1.0",
        )

        # Cache result
        self._cache[entity_id] = enriched

        # Return the fully built EnrichedEntity so callers can access its attributes directly
        return enriched

    async def _collect_entity_mentions(self, entity_id: str) -> list[EntityMention]:
        """Collect all mentions of an entity across documents"""
        mentions = []

        # Get all markdown files
        all_files = self.git_ops.get_all_files()
        markdown_files = [f for f in all_files if f.endswith(".md")]

        for file_path in markdown_files:
            try:
                content = await self._read_file_content(file_path)
                if not content:
                    continue

                # Parse frontmatter for entity references
                metadata = self._extract_frontmatter(content)
                entity_ids = metadata.get("entities", [])

                # Check if our entity is mentioned
                if entity_id in entity_ids:
                    mentions.append(
                        EntityMention(
                            file_path=file_path,
                            entity_ids=entity_ids,
                            content=content,
                            metadata=metadata,
                        )
                    )

            except Exception as e:
                logger.warning(f"Error processing file {file_path}: {str(e)}")

        return mentions

    async def _read_file_content(self, file_path: str) -> str:
        """Read file content (async wrapper for git_ops)"""
        return self.git_ops.read_file(file_path)

    def _extract_frontmatter(self, content: str) -> dict[str, Any]:
        """Extract frontmatter metadata from markdown content"""
        import yaml

        if not content.startswith("---"):
            return {}

        try:
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return yaml.safe_load(parts[1]) or {}
        except Exception as e:
            logger.warning(f"Failed to parse YAML frontmatter: {e}")

        return {}

    async def _infer_relationships(
        self, entity_id: str, mentions: list[EntityMention]
    ) -> list[EntityRelationship]:
        """Infer relationships from entity mentions"""
        relationships = defaultdict(
            lambda: {"evidence": [], "contexts": [], "dates": []}
        )

        # Analyze each mention for relationships
        for mention in mentions:
            # Get co-occurring entities
            co_entities = [e for e in mention.entity_ids if e != entity_id]

            # Analyze content for relationship patterns
            for co_entity in co_entities:
                rel_type = await self._detect_relationship_type(
                    entity_id, co_entity, mention.content, mention.metadata
                )

                if rel_type:
                    key = (co_entity, rel_type)
                    relationships[key]["evidence"].append(mention.file_path)
                    relationships[key]["contexts"].append(mention.content[:200])
                    relationships[key]["dates"].append(mention.metadata.get("date"))

        # Convert to EntityRelationship objects
        result = []
        for (target_id, rel_type), data in relationships.items():
            strength = self._calculate_relationship_strength(data)
            confidence = self._calculate_relationship_confidence(data)

            result.append(
                EntityRelationship(
                    target_entity_id=target_id,
                    relationship_type=rel_type,
                    strength=strength,
                    confidence=confidence,
                    evidence_documents=list(set(data["evidence"])),
                )
            )

        return result

    async def _detect_relationship_type(
        self, source_id: str, target_id: str, content: str, metadata: dict
    ) -> RelationshipType | None:
        """Detect the type of relationship between two entities"""
        # Get entity types
        source_type = extract_entity_type_from_id(source_id)
        target_type = extract_entity_type_from_id(target_id)

        if not source_type or not target_type:
            return None

        source = self.entity_registry.get_canonical_entity(source_type, source_id)
        target = self.entity_registry.get_canonical_entity(target_type, target_id)

        if not source or not target:
            return None

        # Extract entity names for pattern matching
        source_name = source.canonical_name
        target_name = target.canonical_name

        # Check for hierarchical relationships
        if isinstance(source, CanonicalPerson) and isinstance(target, CanonicalPerson):
            if await self._detect_hierarchy_pattern(content, source_name, target_name):
                return RelationshipType.REPORTS_TO

        # Check for team membership
        if isinstance(source, CanonicalPerson) and isinstance(target, CanonicalTeam):
            if source_id in target.members:
                return RelationshipType.MEMBER_OF

        # Check for project membership
        if isinstance(source, CanonicalPerson) and isinstance(target, CanonicalProject):
            if self._detect_project_participation(content, source_name, target_name):
                return RelationshipType.MEMBER_OF

        # Default to collaboration for same-type entities
        if type(source) is type(target) and self._detect_collaboration(
            content, source_name, target_name
        ):
            return RelationshipType.COLLABORATES_WITH

        return None

    async def _detect_hierarchy_pattern(
        self, content: str, person1: str, person2: str
    ) -> bool:
        """Detect if person1 reports to person2"""
        patterns = [
            rf"{person1}.*reports to.*{person2}",
            rf"{person1}.*reporting to.*{person2}",
            rf"{person2}.*manages.*{person1}",
            rf"{person2}.*supervises.*{person1}",
            rf"{person1}'s manager.*{person2}",
        ]

        content_lower = content.lower()
        for pattern in patterns:
            if re.search(pattern.lower(), content_lower):
                return True

        return False

    def _detect_project_participation(
        self, content: str, person: str, project: str
    ) -> bool:
        """Detect if a person is working on a project"""
        patterns = [
            rf"{person}.*working on.*{project}",
            rf"{person}.*leads.*{project}",
            rf"{person}.*assigned to.*{project}",
            rf"{project}.*team.*{person}",
        ]

        content_lower = content.lower()
        for pattern in patterns:
            if re.search(pattern.lower(), content_lower):
                return True

        return False

    def _detect_collaboration(self, content: str, entity1: str, entity2: str) -> bool:
        """Detect collaboration between entities"""
        patterns = [
            rf"{entity1}.*collaborated with.*{entity2}",
            rf"{entity1}.*worked with.*{entity2}",
            rf"{entity1}.*and.*{entity2}.*together",
            rf"{entity1}.*partnered with.*{entity2}",
        ]

        content_lower = content.lower()
        for pattern in patterns:
            if re.search(pattern.lower(), content_lower):
                return True

        return False

    def _calculate_relationship_strength(self, data: dict) -> float:
        """Calculate relationship strength based on evidence"""
        # Factors: number of mentions, recency, consistency
        num_mentions = len(data["evidence"])

        # Base strength on mention frequency
        if num_mentions >= 10:
            base_strength = 0.9
        elif num_mentions >= 5:
            base_strength = 0.7
        elif num_mentions >= 3:
            base_strength = 0.5
        else:
            base_strength = 0.3

        # Adjust for recency if dates available
        if data["dates"]:
            recent_dates = [d for d in data["dates"] if d and self._is_recent(d)]
            recency_factor = len(recent_dates) / len(data["dates"])
            base_strength = base_strength * 0.7 + recency_factor * 0.3

        return min(base_strength, 1.0)

    def _calculate_relationship_confidence(self, data: dict) -> float:
        """Calculate confidence in the relationship"""
        # Based on evidence quality and consistency
        num_evidence = len(data["evidence"])

        if num_evidence >= 5:
            return 0.9
        elif num_evidence >= 3:
            return 0.7
        elif num_evidence >= 2:
            return 0.5
        else:
            return 0.3

    def _is_recent(self, date_str: str, days: int = 90) -> bool:
        """Check if a date string represents a recent date"""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return (datetime.now(UTC) - date).days <= days
        except Exception as e:
            logger.warning(f"Failed to parse date string '{date_str}': {e}")
            return False

    async def _build_organizational_context(
        self, entity_id: str, relationships: list[EntityRelationship]
    ) -> OrganizationalContext | None:
        """Build organizational context for an entity"""
        # Extract entity type from ID
        entity_type = extract_entity_type_from_id(entity_id)
        if not entity_type:
            return None

        entity = self.entity_registry.get_canonical_entity(entity_type, entity_id)
        if not isinstance(entity, CanonicalPerson):
            return None

        # Find reporting relationships
        reports_to = [
            r
            for r in relationships
            if r.relationship_type == RelationshipType.REPORTS_TO
        ]
        manages = [
            r for r in relationships if r.relationship_type == RelationshipType.MANAGES
        ]

        # Build reporting chain
        reporting_chain = []
        current_id = entity_id
        visited = set()

        while reports_to and current_id not in visited:
            visited.add(current_id)
            # Find who this person reports to
            supervisor_rels = [
                r for r in reports_to if r.target_entity_id not in visited
            ]
            if supervisor_rels:
                # Take the highest confidence relationship
                supervisor_rel = max(supervisor_rels, key=lambda r: r.confidence)
                reporting_chain.append(supervisor_rel.target_entity_id)

                # Get supervisor's relationships for next iteration
                supervisor_enriched = await self.enrich_entity(
                    supervisor_rel.target_entity_id
                )
                current_id = supervisor_rel.target_entity_id
                reports_to = [
                    r
                    for r in supervisor_enriched.relationships
                    if r.relationship_type == RelationshipType.REPORTS_TO
                ]
            else:
                break

        # Determine hierarchy level
        hierarchy_level = len(reporting_chain)

        # Find peers (people at same level in same department)
        peer_entities = []
        if reporting_chain:
            # Get supervisor's direct reports
            supervisor_id = reporting_chain[0]
            supervisor_enriched = await self.enrich_entity(supervisor_id)
            peer_entities = [
                r.target_entity_id
                for r in supervisor_enriched.relationships
                if r.relationship_type == RelationshipType.MANAGES
                and r.target_entity_id != entity_id
            ]

        # Get direct reports
        subordinate_entities = [r.target_entity_id for r in manages]

        return OrganizationalContext(
            hierarchy_level=hierarchy_level,
            department=entity.departments[0] if entity.departments else None,
            division=None,  # Could be inferred from higher-level departments
            reporting_chain=reporting_chain,
            peer_entities=peer_entities,
            subordinate_entities=subordinate_entities,
        )

    async def batch_enrich_entities(
        self, entity_ids: list[str]
    ) -> dict[str, EnrichedEntity]:
        """Enrich multiple entities in batch"""
        tasks = [self.enrich_entity(entity_id) for entity_id in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched: dict[str, EnrichedEntity] = {}
        for entity_id, result in zip(entity_ids, results, strict=False):
            if isinstance(result, Exception):
                logger.error(f"Failed to enrich {entity_id}: {str(result)}")
            else:
                if isinstance(result, EnrichedEntity):
                    enriched[entity_id] = result
                else:
                    logger.error(
                        "Unexpected result type from enrich_entity for %s: %s",
                        entity_id,
                        type(result),
                    )

        return enriched

    async def get_organizational_hierarchy(self) -> dict[str, Any]:
        """Build the full organizational hierarchy"""
        # Get all people
        all_people = [
            entity_id
            for entity_id, entity in self.entity_registry._entities.items()
            if isinstance(entity, CanonicalPerson)
        ]

        # Enrich all people
        enriched_people = await self.batch_enrich_entities(all_people)

        # Build hierarchy levels
        levels = defaultdict(list)
        for entity_id, enriched in enriched_people.items():
            if enriched.organizational_context:
                level = enriched.organizational_context.hierarchy_level
                levels[level].append(
                    {
                        "id": entity_id,
                        "name": enriched.base_entity.canonical_name,
                        "title": enriched.base_entity.titles[0]
                        if enriched.base_entity.titles
                        else "",
                        "department": enriched.organizational_context.department,
                    }
                )

        # Convert to sorted list
        hierarchy_levels = []
        for level in sorted(levels.keys()):
            hierarchy_levels.append(
                {
                    "level": level,
                    "title": self._get_level_title(level),
                    "entities": levels[level],
                }
            )

        return {
            "levels": hierarchy_levels,
            "total_entities": len(enriched_people),
            "max_depth": max(levels.keys()) if levels else 0,
        }

    def _get_level_title(self, level: int) -> str:
        """Get a title for organizational level"""
        titles = {
            0: "Executive",
            1: "C-Suite",
            2: "VPs",
            3: "Directors",
            4: "Managers",
            5: "Team Leads",
            6: "Individual Contributors",
        }
        return titles.get(level, f"Level {level}")

    async def _analyze_relationship_patterns(
        self, mentions: list[EntityMention]
    ) -> list[EntityRelationship]:
        """Analyze mentions to extract relationship patterns"""
        # This would use more sophisticated NLP or Claude API
        # For now, using rule-based patterns
        relationships = []

        for _mention in mentions:
            # Implementation would analyze the content
            pass

        return relationships

    async def _evaluate_evidence_quality(
        self, relationship_type: RelationshipType, evidence_docs: list[str]
    ) -> float:
        """Evaluate the quality of evidence for a relationship"""
        if not evidence_docs:
            return 0.0

        # Factors to consider:
        # 1. Number of documents
        # 2. Recency of documents
        # 3. Type of documents (meetings vs emails vs projects)

        base_score = min(len(evidence_docs) / 5.0, 1.0)  # More docs = higher quality

        # Adjust for document types
        meeting_docs = sum(1 for doc in evidence_docs if "meetings/" in doc)
        if meeting_docs > len(evidence_docs) / 2:
            base_score *= 1.2  # Meetings are high-quality evidence

        return min(base_score, 1.0)
