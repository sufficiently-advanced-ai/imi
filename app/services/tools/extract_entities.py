"""
Extract Entities Tool - Find people, projects, teams in any content.

Leverages the existing EntityBrain service to provide standardized entity extraction
with performance tracking and caching.
"""

import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..agent_tools import AgentTool, ToolResult
from ..domain_aware_entity_extractor import DomainAwareEntityExtractor
from ..entity_registry import get_entity_repository


class ExtractEntitiesTool(AgentTool):
    """Tool for extracting people, projects, and teams from content."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)
        # Initialize DomainAwareEntityExtractor
        try:
            self.entity_extractor = DomainAwareEntityExtractor()
        except Exception:
            # For tests, we might not have entity extractor
            self.entity_extractor = None

        # Initialize entity registry
        try:
            self.registry = get_entity_repository()
        except Exception as e:
            print(f"Warning: Could not load entity registry: {e}")
            self.registry = None

        # Configuration
        self.confidence_threshold = (
            0.85  # Minimum confidence for new entity registration
        )

        # Prompt template cache
        self._prompt_cache = {}

    @property
    def name(self) -> str:
        return "extract_entities"

    @property
    def description(self) -> str:
        return "Find people, projects, and teams mentioned in any content with normalized IDs and metadata"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to analyze for entities. Can include natural language instructions like 'find only people' or 'extract projects and teams'",
                }
            },
            "required": ["content"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "object",
                    "properties": {
                        "people": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "role": {"type": "string"},
                                    "teams": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "mentions": {"type": "integer"},
                                },
                            },
                        },
                        "projects": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "status": {"type": "string"},
                                    "timeline": {"type": "string"},
                                    "mentions": {"type": "integer"},
                                },
                            },
                        },
                        "teams": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "department": {"type": "string"},
                                    "mentions": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "total_entities": {"type": "integer"},
                        "people_count": {"type": "integer"},
                        "projects_count": {"type": "integer"},
                        "teams_count": {"type": "integer"},
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute entity extraction on the provided content."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            content = inputs["content"]

            # Parse natural language instructions and extract content/preferences
            parsed_input = self._parse_natural_input(content)
            actual_content = parsed_input["content"]
            entity_types = parsed_input["entity_types"]
            include_metadata = parsed_input["include_metadata"]
            file_path = parsed_input.get("file_path")

            # Use DomainAwareEntityExtractor for file-based extraction, fall back to LLM
            entities_result = None
            if file_path and self.entity_extractor:
                try:
                    entities_result = await self.entity_extractor.extract_entities_from_metadata(
                        file_path, None
                    )
                    # Validate format: must be dict with list-of-dict values
                    if not entities_result or not any(
                        isinstance(v, list) and v and isinstance(v[0], dict)
                        for v in entities_result.values()
                        if isinstance(v, list) and v
                    ):
                        entities_result = None  # Incompatible format, fall back
                except Exception:
                    entities_result = None

            if entities_result is None:
                entities_result = await self._extract_entities_from_content(actual_content)

            # Process and filter results based on requested types
            extracted_entities = {"people": [], "projects": [], "teams": []}

            # Track entities for deduplication
            seen_entities = {"people": set(), "projects": set(), "teams": set()}

            # Process people
            if "people" in entity_types and "people" in entities_result:
                for person in entities_result["people"]:
                    # Normalize with registry
                    normalized = self._normalize_with_registry(
                        {
                            "type": "person",
                            "name": person["name"],
                            "id": person.get(
                                "id",
                                f"person-{person['name'].lower().replace(' ', '-')}",
                            ),
                            "role": person.get("role", ""),
                            "teams": person.get("teams", []),
                        }
                    )

                    # Skip if already seen (deduplication)
                    if normalized["id"] in seen_entities["people"]:
                        continue
                    seen_entities["people"].add(normalized["id"])

                    person_data = {
                        "id": normalized["id"],
                        "name": normalized.get("canonical_name", normalized["name"]),
                        "mentions": 1,
                    }

                    if include_metadata:
                        person_data.update(
                            {
                                "role": normalized.get("role", ""),
                                "teams": normalized.get("teams", []),
                            }
                        )

                    extracted_entities["people"].append(person_data)

            # Process projects
            if "projects" in entity_types and "projects" in entities_result:
                for project in entities_result["projects"]:
                    # Normalize with registry
                    normalized = self._normalize_with_registry(
                        {
                            "type": "project",
                            "name": project["name"],
                            "id": project.get(
                                "id",
                                f"project-{project['name'].lower().replace(' ', '-')}",
                            ),
                            "status": project.get("status", ""),
                            "timeline": project.get("timeline", ""),
                        }
                    )

                    # Skip if already seen (deduplication)
                    if normalized["id"] in seen_entities["projects"]:
                        continue
                    seen_entities["projects"].add(normalized["id"])

                    project_data = {
                        "id": normalized["id"],
                        "name": normalized.get("canonical_name", normalized["name"]),
                        "mentions": 1,
                    }

                    if include_metadata:
                        project_data.update(
                            {
                                "status": normalized.get("status", ""),
                                "timeline": normalized.get("timeline", ""),
                            }
                        )

                    extracted_entities["projects"].append(project_data)

            # Process teams
            if "teams" in entity_types and "teams" in entities_result:
                for team in entities_result["teams"]:
                    # Normalize with registry
                    normalized = self._normalize_with_registry(
                        {
                            "type": "team",
                            "name": team["name"],
                            "id": team.get(
                                "id", f"team-{team['name'].lower().replace(' ', '-')}"
                            ),
                            "department": team.get("department", ""),
                        }
                    )

                    # Skip if already seen (deduplication)
                    if normalized["id"] in seen_entities["teams"]:
                        continue
                    seen_entities["teams"].add(normalized["id"])

                    team_data = {
                        "id": normalized["id"],
                        "name": normalized.get("canonical_name", normalized["name"]),
                        "mentions": 1,
                    }

                    if include_metadata:
                        team_data.update(
                            {"department": normalized.get("department", "")}
                        )

                    extracted_entities["teams"].append(team_data)

            # Create summary
            summary = {
                "total_entities": (
                    len(extracted_entities["people"])
                    + len(extracted_entities["projects"])
                    + len(extracted_entities["teams"])
                ),
                "people_count": len(extracted_entities["people"]),
                "projects_count": len(extracted_entities["projects"]),
                "teams_count": len(extracted_entities["teams"]),
            }

            # Record metrics
            from ...metrics import record_document_processed, record_entities_discovered

            if len(extracted_entities["people"]) > 0:
                record_entities_discovered("person", len(extracted_entities["people"]))
            if len(extracted_entities["projects"]) > 0:
                record_entities_discovered(
                    "project", len(extracted_entities["projects"])
                )
            if len(extracted_entities["teams"]) > 0:
                record_entities_discovered("team", len(extracted_entities["teams"]))

            # Record document processed if from a file
            if file_path:
                doc_type = "meeting" if "meeting" in file_path else "document"
                record_document_processed(doc_type)

            # Calculate quality score based on extraction completeness

            execution_time_ms = int((time.time() - start_time) * 1000)

            result = ToolResult(
                success=True,
                data={"entities": extracted_entities, "summary": summary},
                execution_time_ms=execution_time_ms,
                metadata={
                    "file_path": file_path,
                    "entity_types_requested": entity_types,
                    "include_metadata": include_metadata,
                },
            )

            self._finish_execution(execution, result)
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=False,
                data={},
                execution_time_ms=execution_time_ms,
                error=str(e),
            )

            self._finish_execution(execution, result)
            return result

    def _parse_natural_input(self, input_text: str) -> dict[str, Any]:
        """Parse natural language input to extract content and preferences."""
        from .parsing_utils import (
            extract_file_path,
            normalize_whitespace,
            remove_patterns,
        )

        # Default values
        result = {
            "content": input_text,
            "entity_types": ["people", "projects", "teams"],
            "include_metadata": True,
            "file_path": None,
        }

        if not input_text:
            return result

        # Normalize whitespace first
        normalized_text = normalize_whitespace(input_text)

        # Extract and validate file path
        file_path, remaining_text = extract_file_path(normalized_text)
        if file_path:
            result["file_path"] = file_path
            normalized_text = remaining_text

        # Process case-insensitive
        lower_text = normalized_text.lower()

        # Extract entity type preferences using specific patterns (more specific first)
        entity_patterns = [
            (
                r"\b(?:find |extract |get )?(?:only )?(?:both )?people and projects?\b",
                ["people", "projects"],
            ),
            (
                r"\b(?:find |extract |get )?(?:only )?(?:both )?projects? and teams?\b",
                ["projects", "teams"],
            ),
            (
                r"\b(?:find |extract |get )?(?:only )?(?:both )?people and teams?\b",
                ["people", "teams"],
            ),
            (r"\b(?:find |extract |get )?only people\b", ["people"]),
            (r"\b(?:find |extract |get )?only projects?\b", ["projects"]),
            (r"\b(?:find |extract |get )?only teams?\b", ["teams"]),
            (r"\b(?:find |extract |get )?just people\b", ["people"]),
            (r"\b(?:find |extract |get )?just projects?\b", ["projects"]),
            (r"\b(?:find |extract |get )?just teams?\b", ["teams"]),
        ]

        for pattern, entity_types in entity_patterns:
            if re.search(pattern, lower_text):
                result["entity_types"] = entity_types
                break

        # Check for metadata preferences
        if re.search(r"\b(?:without|no) metadata\b", lower_text) or re.search(
            r"\bsimple\b", lower_text
        ):
            result["include_metadata"] = False

        # Remove instruction patterns to get clean content
        instruction_patterns = [
            r"\b(?:find |extract |get )?only (?:people|projects?|teams?)\b",
            r"\b(?:find |extract |get )?just (?:people|projects?|teams?)\b",
            r"\b(?:find |extract |get )?(?:both )?(?:people|projects?|teams?) and (?:people|projects?|teams?)\b",
            r"\b(?:without|no) metadata\b",
            r"\bsimple\b",
            r"\bfind\b",
            r"\bextract\b",
            r"\bget\b",
            r"\bin\b",
            r"\bfor\b",
            r"\bthe\b",
        ]

        content = remove_patterns(normalized_text, instruction_patterns)

        # Preserve original if nothing left
        if not content and input_text:
            # Try to extract content after a colon
            colon_match = re.search(r":\s*(.+)", normalized_text)
            if colon_match:
                content = colon_match.group(1).strip()
            else:
                content = normalized_text

        result["content"] = content
        return result

    async def _extract_entities_from_content(self, content: str) -> dict[str, list[dict]]:
        """Extract entities directly from content using LLM."""
        try:
            # Build enhanced prompt with known entities
            prompt = self._build_prompt_with_known_entities(content)

            messages = [{"role": "user", "content": prompt}]
            # Use Haiku for cost-effective NER extraction
            from app.config import settings
            model_kwargs = {"model": settings.CLAUDE_HAIKU_MODEL}
            response = await self.claude_client.generate_message(
                messages, operation="entity_extraction", **model_kwargs
            )

            # Extract content from response
            if hasattr(response, "content"):
                content_data = response.content
                if isinstance(content_data, list) and len(content_data) > 0:
                    response_text = (
                        content_data[0].text
                        if hasattr(content_data[0], "text")
                        else str(content_data[0])
                    )
                else:
                    response_text = str(content_data)
            else:
                response_text = str(response)

            # Parse YAML response
            yaml_start = response_text.find("```yaml")
            if yaml_start != -1:
                yaml_end = response_text.find("```", yaml_start + 7)
                if yaml_end != -1:
                    yaml_content = response_text[yaml_start + 7 : yaml_end].strip()
                else:
                    yaml_content = response_text[yaml_start + 7 :].strip()
            else:
                # Try to find any code block
                code_start = response_text.find("```")
                if code_start != -1:
                    code_end = response_text.find("```", code_start + 3)
                    if code_end != -1:
                        yaml_content = response_text[code_start + 3 : code_end].strip()
                        # Remove language identifier if present
                        lines = yaml_content.split("\n")
                        if lines and not lines[0].strip().startswith(
                            ("people:", "projects:", "teams:")
                        ):
                            yaml_content = "\n".join(lines[1:])
                    else:
                        yaml_content = response_text[code_start + 3 :].strip()
                else:
                    yaml_content = response_text.strip()

            import yaml

            parsed_data = yaml.safe_load(yaml_content)

            if not isinstance(parsed_data, dict):
                return {"people": [], "projects": [], "teams": []}

            return {
                "people": parsed_data.get("people", []),
                "projects": parsed_data.get("projects", []),
                "teams": parsed_data.get("teams", []),
            }

        except Exception as e:
            print(f"Entity extraction from content failed: {e}")
            return {"people": [], "projects": [], "teams": []}

    def _normalize_with_registry(self, entity: dict[str, Any]) -> dict[str, Any]:
        """Normalize entity with registry lookup and fuzzy matching."""
        if not self.registry:
            # No registry available, return entity as-is
            entity["matched"] = False
            entity["registered"] = False
            return entity

        entity_type = entity["type"]
        entity_name = entity["name"]

        try:
            # Try to find matching entity in registry
            similar_entities = self.registry.find_similar_entities(
                entity_name, entity_type=entity_type, limit=1
            )
            if similar_entities and similar_entities[0][1] >= self.confidence_threshold:
                matched_entity = similar_entities[0][0]
                match_confidence = similar_entities[0][1]
            else:
                matched_entity = None
                match_confidence = 0.0
        except Exception as e:
            print(f"Registry lookup failed: {e}")
            entity["matched"] = False
            entity["registered"] = False
            return entity

        if matched_entity:
            # Entity found in registry
            entity["id"] = matched_entity.id
            entity["canonical_name"] = matched_entity.canonical_name
            entity["matched"] = True
            entity["confidence"] = match_confidence

            # Check match type
            if matched_entity.canonical_name.lower() == entity_name.lower():
                entity["match_type"] = "exact"
            elif entity_name in matched_entity.aliases:
                entity["match_type"] = "alias"
            else:
                # Fuzzy match - calculate similarity
                similarity = SequenceMatcher(
                    None, entity_name.lower(), matched_entity.canonical_name.lower()
                ).ratio()
                entity["match_type"] = "fuzzy"
                entity["similarity_score"] = similarity

            # Merge additional data from registry if available
            if hasattr(matched_entity, "email") and matched_entity.email:
                entity["email"] = matched_entity.email
            if hasattr(matched_entity, "titles") and matched_entity.titles:
                entity["titles"] = matched_entity.titles

        else:
            # No match found - register new entity if confidence is high enough
            confidence = entity.get(
                "confidence", 0.9
            )  # Default high confidence for new entities

            if confidence >= self.confidence_threshold:
                # Try to register as canonical entity — failures are non-fatal
                try:
                    if entity_type == "person":
                        self.registry.register_person(
                            canonical_name=entity["name"],
                            titles=[entity.get("role")] if entity.get("role") else [],
                            confidence=confidence,
                        )
                    elif entity_type == "project":
                        self.registry.register_project(
                            canonical_name=entity["name"],
                            status=entity.get("status") or "active",
                            confidence=confidence,
                        )
                    elif entity_type == "team":
                        self.registry.register_team(
                            canonical_name=entity["name"],
                            department=entity.get("department"),
                            confidence=confidence,
                        )

                    entity["canonical_name"] = entity["name"]
                    entity["matched"] = False
                    entity["registered"] = True
                except Exception:
                    # Registration failed (missing save(), validation, etc.)
                    # Entity is still usable — just not persisted to registry
                    entity["matched"] = False
                    entity["registered"] = False
            else:
                # Low confidence, don't register
                entity["matched"] = False
                entity["registered"] = False
                entity["low_confidence"] = True

        return entity

    def _reload_registry(self):
        """Reload the entity registry."""
        try:
            self.registry = get_entity_repository()
        except Exception as e:
            print(f"Warning: Could not reload entity registry: {e}")
            self.registry = None

    def _normalize_batch(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize a batch of entities with deduplication."""
        normalized = []
        seen_ids = set()

        for entity in entities:
            norm_entity = self._normalize_with_registry(entity)

            # Deduplicate by ID
            if norm_entity["id"] not in seen_ids:
                seen_ids.add(norm_entity["id"])
                normalized.append(norm_entity)

        return normalized

    def _get_known_entities_for_prompt(
        self, limit: int = 50
    ) -> dict[str, list[dict[str, Any]]]:
        """Get top known entities from registry for prompt context.

        Args:
            limit: Maximum number of entities per type to include

        Returns:
            Dictionary with people, projects, teams containing canonical names and variations
        """
        if not self.registry:
            return {"people": [], "projects": [], "teams": []}

        known: dict[str, list[dict[str, Any]]] = {"people": [], "projects": [], "teams": []}

        try:
            # Use get_all_entities() which works with the current EntityRegistry API
            all_entities = self.registry.get_all_entities() if hasattr(self.registry, "get_all_entities") else {}

            type_map = {"person": "people", "project": "projects", "team": "teams"}

            for _entity_id, entity in all_entities.items():
                entity_type = getattr(entity, "entity_type", None) or entity.get("entity_type", "") if isinstance(entity, dict) else ""
                category = type_map.get(entity_type)
                if not category:
                    continue

                name = getattr(entity, "canonical_name", None) or (entity.get("name") if isinstance(entity, dict) else str(entity))
                aliases = getattr(entity, "aliases", []) if hasattr(entity, "aliases") else []

                if name and len(known[category]) < limit:
                    known[category].append({
                        "canonical": name,
                        "variations": aliases[:5],
                    })

        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Could not load known entities for prompt: {e}")

        return known

    def _load_prompt_template(self, template_name: str) -> str:
        """Load prompt template from file with caching.

        Args:
            template_name: Name of template file (e.g., 'entity_extract.xml')

        Returns:
            Template content as string
        """
        if template_name in self._prompt_cache:
            return self._prompt_cache[template_name]

        # Construct path to prompt file
        prompt_dir = Path(__file__).parent.parent.parent / "prompts"
        template_path = prompt_dir / template_name

        try:
            with open(template_path, encoding="utf-8") as f:
                content = f.read()
                self._prompt_cache[template_name] = content
                return content
        except Exception as e:
            print(f"Error loading prompt template {template_name}: {e}")
            # Return basic fallback prompt
            return """Extract entities from the following content:
{content}

Return results in YAML format with people, projects, and teams."""

    def _populate_template(self, template: str, variables: dict[str, Any]) -> str:
        """Populate template with variables.

        Args:
            template: Template string with {variable} placeholders
            variables: Dictionary of variable values

        Returns:
            Populated template string
        """
        result = template
        for key, value in variables.items():
            placeholder = f"{{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    def _format_known_entities_section(
        self, known_entities: dict[str, list[dict[str, Any]]]
    ) -> str:
        """Format known entities into a prompt section.

        Args:
            known_entities: Dictionary with people, projects, teams

        Returns:
            Formatted string for prompt insertion
        """
        if not any(known_entities.values()):
            return ""

        sections = []
        sections.append("<known_entities>")
        sections.append("  <instructions>")
        sections.append(
            "    When extracting entities, prefer these canonical names when you encounter variations:"
        )
        sections.append("    - Match case-insensitively")
        sections.append(
            "    - Consider partial matches (e.g., 'Sarah' -> 'Sarah Chen')"
        )
        sections.append("    - Use canonical form when confidence is >80%")
        sections.append("  </instructions>")

        # Format people
        if known_entities["people"]:
            sections.append("  <people>")
            for person in known_entities["people"]:
                variations = (
                    ", ".join(person["variations"]) if person["variations"] else ""
                )
                sections.append(
                    f'    <entity canonical="{person["canonical"]}" variations="{variations}"/>'
                )
            sections.append("  </people>")

        # Format projects
        if known_entities["projects"]:
            sections.append("  <projects>")
            for project in known_entities["projects"]:
                variations = (
                    ", ".join(project["variations"]) if project["variations"] else ""
                )
                sections.append(
                    f'    <entity canonical="{project["canonical"]}" variations="{variations}"/>'
                )
            sections.append("  </projects>")

        # Format teams
        if known_entities["teams"]:
            sections.append("  <teams>")
            for team in known_entities["teams"]:
                variations = ", ".join(team["variations"]) if team["variations"] else ""
                sections.append(
                    f'    <entity canonical="{team["canonical"]}" variations="{variations}"/>'
                )
            sections.append("  </teams>")

        sections.append("  <examples>")
        sections.append("    <example>")
        sections.append(
            '      Input: "Meeting with S. Chen about the CRM system overhaul"'
        )
        sections.append("      Output:")
        sections.append('      - Person: "Sarah Chen" (matched from "S. Chen")')
        sections.append(
            '      - Project: "CRM Modernization Initiative" (matched from "CRM system overhaul")'
        )
        sections.append("    </example>")
        sections.append("  </examples>")
        sections.append("</known_entities>")

        return "\n".join(sections)

    def _build_prompt_with_known_entities(self, content: str) -> str:
        """Build enhanced prompt with known entities context.

        Args:
            content: The content to extract entities from

        Returns:
            Complete prompt with known entities
        """
        # Load template
        template = self._load_prompt_template("entity_extract.xml")

        # Get known entities
        known_entities = self._get_known_entities_for_prompt()

        # Format known entities section
        known_entities_section = self._format_known_entities_section(known_entities)

        # Populate template
        prompt = self._populate_template(
            template,
            {"known_entities_section": known_entities_section, "content": content},
        )

        return prompt

    def _format_known_entities_for_prompt(
        self, known_entities: dict[str, list[dict[str, Any]]]
    ) -> str:
        """Format known entities for direct inclusion in prompt.

        This is an alias for _format_known_entities_section for compatibility.
        """
        return self._format_known_entities_section(known_entities)

    def _load_prompt_with_known_entities(self, content: str) -> str:
        """Load prompt with known entities included.

        This is an alias for _build_prompt_with_known_entities for compatibility.
        """
        return self._build_prompt_with_known_entities(content)
