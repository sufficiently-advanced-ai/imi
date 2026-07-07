import re

from ..models import File
from .file_cache import file_cache


class DependencyTracker:
    """Track dependencies between files and entities to enable partial processing."""

    def __init__(self):
        # Legacy mappings for backward compatibility
        self.person_mentions: dict[
            str, set[str]
        ] = {}  # person_id -> set of files mentioning them
        self.file_people: dict[
            str, set[str]
        ] = {}  # file_path -> set of people mentioned

        # New multi-entity mappings
        self.entity_mentions: dict[str, dict[str, set[str]]] = {
            "people": {},  # entity_id -> set of files
            "projects": {},  # entity_id -> set of files
            "teams": {},  # entity_id -> set of files
        }
        self.file_entities: dict[
            str, dict[str, set[str]]
        ] = {}  # file_path -> {entity_type -> set of entity_ids}

        self.initialized = False

    async def initialize(self):
        """Initialize dependency data from all files."""
        if self.initialized:
            return

        # Get all markdown files
        files = await file_cache.get_all_markdown_files()

        # Extract mentions from all files
        for file in files:
            people = await self._extract_people_from_file(file)
            self.file_people[file.path] = people

            # Update reverse lookup
            for person_id in people:
                if person_id not in self.person_mentions:
                    self.person_mentions[person_id] = set()
                self.person_mentions[person_id].add(file.path)

        self.initialized = True

    async def update_file_dependencies(self, file: File):
        """Update dependency information for a single file."""
        old_people = self.file_people.get(file.path, set())
        new_people = await self._extract_people_from_file(file)

        # Update file_people map
        self.file_people[file.path] = new_people

        # Remove from old mentions
        for person_id in old_people:
            if (
                person_id in self.person_mentions
                and file.path in self.person_mentions[person_id]
            ):
                self.person_mentions[person_id].remove(file.path)

        # Add to new mentions
        for person_id in new_people:
            if person_id not in self.person_mentions:
                self.person_mentions[person_id] = set()
            self.person_mentions[person_id].add(file.path)

    def get_affected_people(self, file_paths: list[str]) -> set[str]:
        """Get all people affected by changes to the given files."""
        affected_people = set()
        for path in file_paths:
            if path in self.file_people:
                affected_people.update(self.file_people[path])
        return affected_people

    def get_affected_files(self, person_ids: list[str]) -> set[str]:
        """Get all files that mention any of the given people."""
        affected_files = set()
        for person_id in person_ids:
            if person_id in self.person_mentions:
                affected_files.update(self.person_mentions[person_id])
        return affected_files

    async def _extract_people_from_file(self, file: File) -> set[str]:
        """Extract people mentioned in a file.

        This uses a simple regex approach for performance rather than
        calling the LLM. It's less accurate but much faster.
        """
        people = set()

        # Try to extract from frontmatter first (most reliable)
        from ..services.frontmatter import frontmatter

        metadata = frontmatter.extract_all(file.content)

        # Check people-related fields in frontmatter
        if metadata:
            for field in ["people", "participants", "attendees", "authors"]:
                if field in metadata and isinstance(metadata[field], list):
                    people.update(metadata[field])

        # If no people found in frontmatter, try regex patterns
        if not people:
            # Pattern for person mentions like @person-john-doe or "Person: John Doe"
            patterns = [
                r"@person-([a-z0-9-]+)",  # @person-john-doe
                r"Person:\s*([A-Za-z\s-]+)",  # Person: John Doe
                r"Attendees?:\s*([A-Za-z\s,-]+)",  # Attendees: John Doe, Jane Smith
                r"Participants?:\s*([A-Za-z\s,-]+)",  # Participants: John Doe, Jane Smith
            ]

            for pattern in patterns:
                matches = re.findall(pattern, file.content)
                for match in matches:
                    # Handle comma-separated lists
                    if "," in match:
                        for name in match.split(","):
                            clean_name = name.strip()
                            if clean_name:
                                people.add(self._normalize_person_id(clean_name))
                    else:
                        people.add(self._normalize_person_id(match.strip()))

        return people

    @staticmethod
    def _normalize_person_id(name: str) -> str:
        """Normalize a person name to a person ID."""
        # Remove any existing 'person-' prefix to avoid duplication
        name = name.lower().replace("person-", "")
        # Convert to lowercase, replace spaces with hyphens
        normalized = name.lower().replace(" ", "-")
        # Add person- prefix if not already present
        return f"person-{normalized}"

    # Multi-entity support methods
    async def update_file_entities(self, file_path: str, entities: dict[str, set[str]]):
        """Update entity dependencies for a file.

        Args:
            file_path: Path to the file
            entities: Dict mapping entity types to sets of entity IDs
        """
        # Get old entities for this file
        old_entities = self.file_entities.get(file_path, {})

        # Update file_entities map
        self.file_entities[file_path] = entities

        # Update entity_mentions for each entity type
        for entity_type in ["people", "projects", "teams"]:
            old_set = old_entities.get(entity_type, set())
            new_set = entities.get(entity_type, set())

            # Remove old mentions
            for entity_id in old_set - new_set:
                if entity_id in self.entity_mentions[entity_type]:
                    self.entity_mentions[entity_type][entity_id].discard(file_path)
                    if not self.entity_mentions[entity_type][entity_id]:
                        del self.entity_mentions[entity_type][entity_id]

            # Add new mentions
            for entity_id in new_set:
                if entity_id not in self.entity_mentions[entity_type]:
                    self.entity_mentions[entity_type][entity_id] = set()
                self.entity_mentions[entity_type][entity_id].add(file_path)

        # Update legacy mappings for people (backward compatibility)
        if "people" in entities:
            self.file_people[file_path] = entities["people"]
            # Update person_mentions
            old_people = old_entities.get("people", set())
            new_people = entities["people"]

            for person_id in old_people - new_people:
                if person_id in self.person_mentions:
                    self.person_mentions[person_id].discard(file_path)

            for person_id in new_people:
                if person_id not in self.person_mentions:
                    self.person_mentions[person_id] = set()
                self.person_mentions[person_id].add(file_path)

    def get_affected_entities(
        self, file_paths: list[str], entity_type: str
    ) -> set[str]:
        """Get all entities of a specific type affected by changes to the given files."""
        affected_entities = set()
        for path in file_paths:
            if path in self.file_entities:
                affected_entities.update(
                    self.file_entities[path].get(entity_type, set())
                )
        return affected_entities

    def get_files_mentioning_entity(self, entity_type: str, entity_id: str) -> set[str]:
        """Get all files that mention a specific entity."""
        return self.entity_mentions.get(entity_type, {}).get(entity_id, set())


# Global instance
dependency_tracker = DependencyTracker()
