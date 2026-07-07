"""
Domain-aware entity processor for generating entity profiles.

This module replaces hardcoded entity profile generation with
domain-configurable processing that supports any entity type.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from app.git_ops import git_ops
from app.model_schemas.domain_config import DomainConfiguration
from app.services.claude_client import ClaudeClient
from app.services.prompt_template_engine import PromptTemplateEngine

logger = logging.getLogger(__name__)


class DomainAwareEntityProcessor:
    """Processes entity profiles based on domain configuration."""

    @staticmethod
    def _sanitize_frontmatter(content: str) -> str:
        """Sanitize Claude-generated content to ensure valid YAML frontmatter.

        Fixes two common issues:
        1. Claude preamble before frontmatter (e.g., "Based on the meeting
           transcript, I'll create a profile..." before the opening ---)
        2. Closing --- concatenated with last YAML value (e.g., '- value---')
        """
        lines = content.split("\n")

        # Find the first --- line (may not be line 0 if Claude added preamble)
        first_delim = None
        for i, line in enumerate(lines):
            if line.strip() == "---":
                first_delim = i
                break

        if first_delim is None:
            return content

        # Strip any preamble before the opening ---
        if first_delim > 0:
            lines = lines[first_delim:]

        if not lines or lines[0].strip() != "---":
            return content

        result = [lines[0]]
        found_closing = False

        for i in range(1, len(lines)):
            line = lines[i]
            stripped = line.rstrip()

            if not found_closing and stripped == "---":
                # Clean closing delimiter
                found_closing = True
                result.append("---")
            elif not found_closing and len(stripped) > 3 and stripped.endswith("---"):
                # Corrupted: value glued to closing delimiter
                # e.g., "  - project-smart-simple---"
                value_part = stripped[:-3].rstrip()
                result.append(value_part)
                result.append("---")
                found_closing = True
            else:
                result.append(line)

        return "\n".join(result)

    @staticmethod
    def _ensure_required_frontmatter(
        content: str, entity_type: str, entity_id: str
    ) -> str:
        """Ensure the frontmatter contains required id and entity_type fields.

        Claude-generated profiles often omit the id and entity_type fields
        that the graph builder needs to classify the file as an entity.
        """
        if not content.startswith("---"):
            return content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return content

        try:
            frontmatter = yaml.safe_load(parts[1])
            if not isinstance(frontmatter, dict):
                frontmatter = {}
        except yaml.YAMLError:
            return content

        modified = False

        if "id" not in frontmatter:
            frontmatter["id"] = entity_id
            modified = True

        if "entity_type" not in frontmatter and "type" not in frontmatter:
            frontmatter["entity_type"] = entity_type
            modified = True

        if not modified:
            return content

        new_frontmatter = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        return f"---\n{new_frontmatter}---{parts[2]}"

    # Default storage path mappings for common entity types
    DEFAULT_STORAGE_PATHS = {
        "person": "people",
        "project": "projects",
        "team": "teams",
        "account": "accounts",
        "contact": "contacts",
        "company": "companies",
        "activity": "activities",
    }

    def __init__(self, claude_client: ClaudeClient):
        """Initialize the processor with required dependencies.

        Args:
            claude_client: Claude client for AI processing
        """
        self.git_ops = git_ops
        self.claude_client = claude_client
        self.prompt_engine = PromptTemplateEngine()

    def _get_entity_storage_path(
        self, entity_type: str, entity_id: str, domain_config: DomainConfiguration
    ) -> str:
        """Generate storage path for entity based on domain configuration.

        Args:
            entity_type: Type of entity (e.g., 'account', 'person')
            entity_id: Unique identifier for the entity
            domain_config: Domain configuration

        Returns:
            str: Full path to entity file
        """
        # Get entity config to get plural form
        if entity_type in domain_config.entities:
            entity_config = domain_config.entities[entity_type]
            # Use plural field from config if available
            folder = entity_config.plural if hasattr(entity_config, "plural") else None
            if not folder:
                # Fallback to default mapping
                folder = self.DEFAULT_STORAGE_PATHS.get(entity_type, f"{entity_type}s")
        else:
            # Use default mapping or add 's' to entity type
            folder = self.DEFAULT_STORAGE_PATHS.get(entity_type, f"{entity_type}s")

        # Build full path — strip entity type prefix to match EntityService convention
        # e.g., entity_id "person-melinda-fountain" → filename "melinda-fountain.md"
        prefix = f"{entity_type}-"
        safe_id = entity_id[len(prefix):] if entity_id.startswith(prefix) else entity_id
        if os.path.isabs(safe_id) or ".." in Path(safe_id).parts or "/" in safe_id or "\\" in safe_id:
            raise ValueError(f"Invalid entity_id for storage path: {entity_id}")
        filename = f"{safe_id}.md"
        return os.path.join(self.git_ops.repo_path, folder, filename)

    async def _build_entity_context(
        self, entity_type: str, entity_id: str, domain_config: DomainConfiguration
    ) -> dict[str, Any]:
        """Build context for entity including attributes and content.

        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            domain_config: Domain configuration

        Returns:
            Context dictionary with entity data
        """
        context = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "attributes": {},
            "content": "",
            "relationships": {},
        }

        # Get entity file path
        entity_path = self._get_entity_storage_path(
            entity_type, entity_id, domain_config
        )

        # Load existing entity file if it exists
        if os.path.exists(entity_path):
            try:
                rel_path = entity_path.replace(self.git_ops.repo_path + "/", "")
                file_content = await self.git_ops.read_file(rel_path)

                # Parse frontmatter and content
                if file_content.startswith("---"):
                    parts = file_content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        context["attributes"] = frontmatter or {}
                        context["content"] = parts[2].strip()
                else:
                    context["content"] = file_content

                # Extract relationships from attributes
                if entity_type in domain_config.entities:
                    entity_config = domain_config.entities[entity_type]
                    if hasattr(entity_config, "relationships"):
                        for rel in entity_config.relationships:
                            # The relationship name is typically the inverse_name or derived from relationship_type
                            # For now, we'll look for any attribute that matches the target entity type
                            target = rel.target_entity
                            for attr_name, attr_value in context["attributes"].items():
                                # Check if attribute name suggests a relationship to the target entity
                                if target in attr_name or f"has_{target}" in attr_name:
                                    context["relationships"][attr_name] = attr_value

            except Exception as e:
                logger.warning(f"Error loading entity file {entity_path}: {e}")

        return context

    def _get_domain_prompt_template(
        self, entity_type: str, domain_config: DomainConfiguration
    ) -> str | None:
        """Get domain-specific prompt template for entity type.

        Loads rich profile prompts from app/prompts/{entity_type}_update.xml
        which contain structured templates for generating comprehensive entity profiles.

        Args:
            entity_type: Type of entity (person, project, team, etc.)
            domain_config: Domain configuration

        Returns:
            Prompt template content or None if not found
        """
        # Load from app/prompts/ directory - these contain rich profile generation templates
        prompt_path = Path(__file__).parent.parent / "prompts" / f"{entity_type}_update.xml"

        if prompt_path.exists():
            try:
                template_content = prompt_path.read_text(encoding="utf-8")
                logger.info(f"Loaded rich profile prompt template for {entity_type} from {prompt_path}")
                return template_content
            except Exception as e:
                logger.warning(f"Failed to load prompt template from {prompt_path}: {e}")
                return None

        logger.debug(f"No prompt template found for entity type '{entity_type}' at {prompt_path}")
        return None

    # Frontmatter keys that represent typed, grounded relationships (not weak
    # co-occurrence). Surfaced to the profile prompt as authoritative facts so
    # attribution/relationships aren't inferred from a shared-meeting transcript.
    _GROUNDED_RELATIONSHIP_KEYS = (
        "reports_to", "manages", "works_on", "leads", "member_of_team",
        "belongs_to_account", "owns", "depends_on", "collaborates_with",
        "direct_reports", "teams", "projects",
    )

    def _build_grounded_facts(self, context: dict[str, Any]) -> str:
        """Assemble the attribution-authoritative facts block for the prompt.

        Pulls (1) the entity's "## Recent Signals" section (signals this entity
        owns / is named in, written by the enricher's signal phase) out of the
        existing content, and (2) typed relationship fields from frontmatter.
        These are the only facts the profile may attribute to the entity; the
        prompt treats this block as authoritative instead of inferring
        attribution from co-attended meetings.
        """
        parts: list[str] = []

        # (0) Manual corrections — highest authority. Human-entered facts that
        # override anything derived. Placed first so the model weighs them
        # before signals/relationships, and omitted entirely when empty so no
        # stale "Corrections" header is emitted.
        attrs0 = context.get("attributes", {}) or {}
        raw_corrections = attrs0.get("manual_corrections")
        correction_lines: list[str] = []
        if isinstance(raw_corrections, (list, tuple, set)):
            correction_lines = [
                f"- {str(c).strip()}" for c in raw_corrections if str(c).strip()
            ]
        elif isinstance(raw_corrections, str) and raw_corrections.strip():
            correction_lines = [f"- {raw_corrections.strip()}"]
        if correction_lines:
            parts.append(
                "## Corrections (authoritative — always honor these; they "
                "override any conflicting derived fact)\n"
                + "\n".join(correction_lines)
            )

        # (1) Recent Signals section sliced from existing content (stop at next H2)
        content = context.get("content", "") or ""
        marker = "## Recent Signals"
        idx = content.find(marker)
        if idx != -1:
            section = content[idx:]
            next_h2 = section.find("\n## ", len(marker))
            if next_h2 != -1:
                section = section[:next_h2]
            parts.append(section.strip())

        # (2) Typed relationships from frontmatter
        attrs = context.get("attributes", {}) or {}
        rel_lines: list[str] = []
        for key in self._GROUNDED_RELATIONSHIP_KEYS:
            value = attrs.get(key)
            if not value:
                continue
            if isinstance(value, (list, tuple, set)):
                rendered = ", ".join(str(v) for v in value if v)
            else:
                rendered = str(value)
            if rendered:
                rel_lines.append(f"- {key}: {rendered}")
        if rel_lines:
            parts.append("## Typed Relationships\n" + "\n".join(rel_lines))

        result = "\n\n".join(parts).strip()
        logger.debug(
            "[PROFILE] Grounded facts for %s: signals_section=%s, typed_relationships=%d",
            context.get("entity_id"),
            any(p.startswith("## Recent Signals") for p in parts),
            len(rel_lines),
        )
        return result

    async def _generate_entity_profile(
        self,
        entity_type: str,
        entity_id: str,
        context: dict[str, Any],
        domain_config: DomainConfiguration,
    ) -> str:
        """Generate entity profile using AI.

        Uses rich XML prompt templates from app/prompts/ when available,
        falling back to a default prompt for entity types without templates.

        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            context: Entity context data
            domain_config: Domain configuration

        Returns:
            Generated profile content
        """
        # Get domain-specific prompt template
        prompt_template = self._get_domain_prompt_template(entity_type, domain_config)

        if prompt_template:
            # Prepare template variables for XML template
            # The XML templates use {{variable}} syntax
            existing_profile = context.get("content", "")
            if context.get("attributes"):
                # Build existing profile from frontmatter + content
                import yaml as yaml_module
                frontmatter_str = yaml_module.dump(context["attributes"], default_flow_style=False)
                existing_profile = f"---\n{frontmatter_str}---\n\n{existing_profile}"

            trigger_files_content = context.get("trigger_contents", "")
            if not trigger_files_content and context.get("trigger_files"):
                trigger_files_content = "\n".join(f"- {f}" for f in context["trigger_files"])

            # Substitute template variables (XML templates use {{var}} syntax).
            # Escape content-bearing values: they are injected into XML-like tags
            # and can contain <, >, & (transcripts, prior profiles, signal text).
            # Unescaped markup could corrupt prompt structure or inject tags that
            # bypass the attribution constraints. entity_id is a slug (safe).
            import html

            prompt = prompt_template.replace("{{entity_id}}", entity_id)
            prompt = prompt.replace(
                "{{existing_profile}}", html.escape(existing_profile)
            )
            prompt = prompt.replace(
                "{{trigger_files}}", html.escape(trigger_files_content)
            )
            prompt = prompt.replace("{{recent_digests}}", context.get("recent_digests", ""))
            prompt = prompt.replace(
                "{{grounded_facts}}", html.escape(self._build_grounded_facts(context))
            )

            logger.info(f"Using rich profile template for {entity_type}: {entity_id}")
        else:
            # Default prompt for entity types without templates
            prompt = f"""Update the profile for {entity_type} '{entity_id}'.

Current context:
- Entity Type: {entity_type}
- Entity ID: {entity_id}
- Attributes: {context.get('attributes', {})}
- Current Content: {context.get('content', '')}
- Trigger Files: {context.get('trigger_files', [])}

Generate an updated profile that:
1. Includes all relevant attributes for this {entity_type}
2. Summarizes key information from trigger files
3. Maintains markdown format with YAML frontmatter
4. Uses domain-appropriate language and structure

Return the complete updated profile."""

        # Generate response using Claude's message API
        messages = [{"role": "user", "content": prompt}]
        response = await self.claude_client.generate_message(
            messages=messages,
            system=f"You are updating entity profiles for a {domain_config.name} system.",
            max_tokens=2048,
            operation="entity_profile_generation",
        )

        # Extract text from response — fail fast if empty to avoid overwriting profiles
        if response and hasattr(response, "content") and response.content:
            if len(response.content) > 0 and hasattr(response.content[0], "text"):
                text = response.content[0].text
                if text:
                    return text
        logger.error(f"Empty Claude response for {entity_type} profile generation: {entity_id}")
        raise RuntimeError(f"Claude returned empty response for {entity_type} {entity_id}")

    async def update_entity_profile(
        self,
        entity_type: str,
        entity_id: str,
        trigger_files: list[str],
        domain_config: DomainConfiguration,
    ) -> str:
        """Update entity profile based on trigger files and domain config.

        Args:
            entity_type: Type of entity to update
            entity_id: Unique identifier for the entity
            trigger_files: List of files that triggered this update
            domain_config: Domain configuration

        Returns:
            Status message

        Raises:
            ValueError: If entity type is not defined in domain config
        """
        # Validate entity type
        if entity_type not in domain_config.entities:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Build entity context
        context = await self._build_entity_context(
            entity_type, entity_id, domain_config
        )
        context["trigger_files"] = trigger_files

        # Add trigger file contents to context
        if trigger_files:
            trigger_contents = []
            for file_path in trigger_files:
                try:
                    normalized = os.path.normpath(file_path)
                    if os.path.isabs(normalized) or normalized.startswith(".."):
                        logger.warning(f"Skipping unsafe trigger file path: {file_path}")
                        continue
                    content = await self.git_ops.read_file(normalized)
                    if content:
                        trigger_contents.append(f"=== {file_path} ===\n{content}")
                except Exception as e:
                    logger.warning(f"Error loading trigger file {file_path}: {e}")

            if trigger_contents:
                context["trigger_contents"] = "\n\n".join(trigger_contents)

        # Generate updated profile
        profile_content = await self._generate_entity_profile(
            entity_type, entity_id, context, domain_config
        )

        # Sanitize frontmatter — Claude may generate closing --- glued to
        # the last YAML value, or add preamble text before the opening ---.
        profile_content = self._sanitize_frontmatter(profile_content)

        # Ensure id and entity_type are in frontmatter so the graph builder
        # can classify this file as an entity (not a generic Document).
        profile_content = self._ensure_required_frontmatter(
            profile_content, entity_type, entity_id
        )

        # Save updated profile
        storage_path = self._get_entity_storage_path(
            entity_type, entity_id, domain_config
        )

        # Ensure directory exists
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)

        # Save file
        with open(storage_path, "w", encoding="utf-8") as f:
            f.write(profile_content)

        return f"Updated {entity_type} profile for {entity_id}"

    async def update_entity_with_analysis(
        self,
        entity_type: str,
        entity_id: str,
        context: str,
        analysis_type: str,
        domain_config: DomainConfiguration,
        batch_mode: bool = False,  # Keep for compatibility
    ) -> bool:
        """Update entity with analysis results using domain configuration.

        This method provides compatibility with EntityBrain.update_entity_with_analysis
        while using domain-aware processing.

        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            context: Analysis context (e.g., meeting transcript)
            analysis_type: Type of analysis (e.g., 'meeting_insights')
            domain_config: Domain configuration
            batch_mode: Ignored, kept for compatibility

        Returns:
            True if update successful, False otherwise
        """
        try:
            # Get entity storage path
            storage_path = self._get_entity_storage_path(
                entity_type, entity_id, domain_config
            )

            # Load existing entity file
            existing_content = ""
            if os.path.exists(storage_path):
                existing_content = await self.git_ops.get_file_content(storage_path)

            # Parse frontmatter and content
            lines = existing_content.split("\n")
            frontmatter_end = 0
            if lines and lines[0] == "---":
                for i, line in enumerate(lines[1:], 1):
                    if line == "---":
                        frontmatter_end = i + 1
                        break

            frontmatter = "\n".join(lines[:frontmatter_end]) if frontmatter_end else ""
            body = (
                "\n".join(lines[frontmatter_end:])
                if frontmatter_end
                else existing_content
            )

            # Build prompt for analysis update
            prompt = f"""You are updating the {entity_type} profile for {entity_id} based on new {analysis_type}.

Current profile content:
{body}

New context from {analysis_type}:
{context}

Please generate an updated profile section that incorporates the new insights from the {analysis_type}.
The update should:
1. Add new relevant information
2. Update existing sections if needed
3. Maintain the existing structure and style
4. Be concise but comprehensive

Return only the new/updated content to be appended or integrated into the profile."""

            # Generate update using Claude's message API
            messages = [{"role": "user", "content": prompt}]
            update_response = await self.claude_client.generate_message(
                messages=messages,
                temperature=0.7,
                max_tokens=2048,
                operation="entity_profile_update",
            )

            # Extract text from response
            updated_section = ""
            if update_response and hasattr(update_response, "content") and update_response.content:
                if len(update_response.content) > 0 and hasattr(update_response.content[0], "text"):
                    updated_section = update_response.content[0].text

            # Determine where to add the update
            if analysis_type == "meeting_insights":
                section_header = "\n\n## Recent Meeting Insights\n"
            elif analysis_type == "commitments":
                section_header = "\n\n## Commitments & Action Items\n"
            elif analysis_type == "project_insights":
                section_header = "\n\n## Project Updates\n"
            elif analysis_type == "team_insights":
                section_header = "\n\n## Team Dynamics\n"
            else:
                section_header = f"\n\n## {analysis_type.replace('_', ' ').title()}\n"

            # Check if section already exists
            if section_header.strip() in body:
                # Replace existing section
                parts = body.split(section_header.strip())
                if len(parts) > 1:
                    # Find next section
                    next_section_idx = parts[1].find("\n##")
                    if next_section_idx > 0:
                        parts[1] = updated_section + parts[1][next_section_idx:]
                    else:
                        parts[1] = updated_section
                    updated_body = section_header.strip().join(parts)
                else:
                    updated_body = body + section_header + updated_section
            else:
                # Add new section
                updated_body = body + section_header + updated_section

            # Combine frontmatter and updated body
            final_content = frontmatter + updated_body if frontmatter else updated_body

            # Save updated file
            with open(storage_path, "w", encoding="utf-8") as f:
                f.write(final_content)

            # Commit the changes
            await self.git_ops.commit_and_push(
                [storage_path], f"Update {entity_type} {entity_id} with {analysis_type}"
            )

            logger.info(f"Updated {entity_type} {entity_id} with {analysis_type}")
            return True

        except Exception as e:
            logger.error(f"Error updating entity with analysis: {e}")
            return False
