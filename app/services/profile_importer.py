"""Profile importer service — imports person entities from LinkedIn profile text.

Uses Claude to extract structured data from pasted profile text, creates an
entity markdown file, adds the entity to the knowledge graph, and clears caches.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.services.claude_client import get_claude_client
from app.services.entity_file_service import EntityFileService, clear_entity_cache

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are extracting structured profile data from raw LinkedIn profile text.

Given the following profile text for a person named "{name}", extract these fields
as a JSON object. Use null for any field you cannot determine.

Fields:
- name (string): Full name (use the provided name if the text is ambiguous)
- current_title (string): Most recent job title
- company (string): Current company/employer
- location (string): City, State/Country
- experience (array of objects): Key roles, each with "title", "company", "dates"
- skills (array of strings): Technical and professional skills
- education (array of objects): Each with "degree", "institution"
- bio (string): A concise 2-3 sentence professional narrative summary

Respond ONLY with valid JSON, no markdown fences or explanation.

Profile text:
{profile_text}
"""


class ProfileImporter:
    """Import person entities from pasted LinkedIn profile text."""

    def __init__(self, domain_config=None):
        self._domain_config = domain_config

    async def import_profile(
        self,
        name: str,
        profile_text: str,
        source: str = "linkedin",
    ) -> dict[str, Any]:
        """Import a person profile from raw text (e.g. LinkedIn).

        1. Call Claude to extract structured fields from profile_text
        2. Normalize entity ID
        3. Create/update entity markdown file with YAML frontmatter + rich body
        4. Add node to Neo4j graph
        5. Clear entity cache

        Returns:
            Dict with entity_id, name, title, company, file_path, status
        """
        # --- 1. Extract structured data via Claude ---
        extracted = await self._extract_profile_data(name, profile_text)

        # Use extracted name if richer, otherwise keep the provided name
        display_name = extracted.get("name") or name
        current_title = extracted.get("current_title")
        company = extracted.get("company")

        # --- 2. Normalize entity ID ---
        entity_id = self._normalize_entity_id(display_name)

        # --- 3. Check for existing entity and create/update ---
        entity_file_service = EntityFileService(self._domain_config)
        existing = await entity_file_service.get_entity(entity_id)
        status = "updated" if existing else "created"

        entity_data = self._build_entity_data(
            entity_id=entity_id,
            display_name=display_name,
            extracted=extracted,
            source=source,
        )
        content = self._render_markdown_body(display_name, extracted)

        entity_payload = {
            "id": entity_id,
            "entity_type": "person",
            "attributes": entity_data,
            "content": content,
        }

        saved = await entity_file_service.save_entity(
            entity_payload,
            commit_message=f"Import {source} profile: {display_name}",
        )
        if not saved:
            raise RuntimeError(f"Failed to save entity file for {entity_id}")

        # Resolve the file path that was written
        file_path = entity_file_service.get_entity_path("person", entity_id)

        # --- 4. Add to knowledge graph ---
        await self._add_to_knowledge_graph(entity_id, display_name)

        # --- 5. Clear caches ---
        clear_entity_cache("person", entity_id)

        logger.info(f"[PROFILE_IMPORT] {status.capitalize()} entity {entity_id} from {source}")

        return {
            "status": status,
            "entity_id": entity_id,
            "name": display_name,
            "title": current_title,
            "company": company,
            "file_path": file_path,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_profile_data(self, name: str, profile_text: str) -> dict[str, Any]:
        """Use Claude to extract structured data from profile text."""
        claude = get_claude_client()

        prompt = EXTRACTION_PROMPT.format(name=name, profile_text=profile_text)

        from app.config import settings
        response = await claude.generate_message(
            messages=[{"role": "user", "content": prompt}],
            model=settings.CLAUDE_HAIKU_MODEL,
            max_tokens=2048,
            temperature=0.2,
            operation="profile_extraction",
        )

        # Parse response — handle both raw text and Message objects
        text = response.content[0].text if hasattr(response, "content") else str(response)

        # Strip markdown fences if Claude included them despite instructions
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[PROFILE_IMPORT] Failed to parse Claude response as JSON: {text[:200]}")
            return {"name": name}

    def _normalize_entity_id(self, name: str) -> str:
        """Normalize a person name into a consistent entity ID."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            slug = "unnamed"
        return f"person-{slug}"

    def _build_entity_data(
        self,
        entity_id: str,
        display_name: str,
        extracted: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        """Build the entity attributes dict for frontmatter."""
        now = datetime.now(UTC).isoformat()

        data: dict[str, Any] = {
            "name": display_name,
            "canonical_name": display_name.lower(),
            "source": f"{source}_import",
            "imported_at": now,
        }

        if extracted.get("current_title"):
            data["titles"] = [extracted["current_title"]]
        if extracted.get("company"):
            data["company"] = extracted["company"]
        if extracted.get("location"):
            data["location"] = extracted["location"]
        if extracted.get("skills"):
            data["skills"] = extracted["skills"]

        return data

    def _render_markdown_body(self, display_name: str, extracted: dict[str, Any]) -> str:
        """Render the rich markdown body below the frontmatter."""
        lines = [f"# {display_name}", ""]

        # Overview
        title = extracted.get("current_title", "")
        company = extracted.get("company", "")
        location = extracted.get("location", "")
        overview_parts = []
        if title and company:
            overview_parts.append(f"{title} at {company}")
        elif title:
            overview_parts.append(title)
        if location:
            overview_parts.append(f"based in {location}")

        if overview_parts:
            lines.append("## Overview")
            lines.append(", ".join(overview_parts) + ".")
            lines.append("")

        # Bio / Professional Background
        if extracted.get("bio"):
            lines.append("## Professional Background")
            lines.append(extracted["bio"])
            lines.append("")

        # Experience
        experience = extracted.get("experience") or []
        if experience:
            lines.append("## Experience")
            for role in experience:
                if isinstance(role, dict):
                    role_title = role.get("title", "")
                    role_company = role.get("company", "")
                    dates = role.get("dates", "")
                    parts = [p for p in [role_title, role_company] if p]
                    entry = " at ".join(parts)
                    if dates:
                        entry += f" ({dates})"
                    lines.append(f"- {entry}")
                else:
                    lines.append(f"- {role}")
            lines.append("")

        # Skills & Expertise
        skills = extracted.get("skills") or []
        if skills:
            lines.append("## Skills & Expertise")
            for skill in skills:
                lines.append(f"- {skill}")
            lines.append("")

        # Education
        education = extracted.get("education") or []
        if education:
            lines.append("## Education")
            for edu in education:
                if isinstance(edu, dict):
                    degree = edu.get("degree", "")
                    institution = edu.get("institution", "")
                    parts = [p for p in [degree, institution] if p]
                    lines.append(f"- {', '.join(parts)}")
                else:
                    lines.append(f"- {edu}")
            lines.append("")

        return "\n".join(lines)

    async def _add_to_knowledge_graph(self, entity_id: str, display_name: str) -> bool:
        """Add the imported entity to the knowledge graph (lightweight upsert)."""
        try:
            from app.services.graph import get_knowledge_graph

            kg = get_knowledge_graph()
            if not kg:
                logger.warning("[PROFILE_IMPORT] Knowledge graph not available, skipping graph update")
                return False

            await kg.add_node(
                entity_type="person",
                name=display_name,
                entity_id=entity_id,
            )
            logger.info(f"[PROFILE_IMPORT] Added {entity_id} to knowledge graph")
            return True

        except Exception as e:
            logger.warning(f"[PROFILE_IMPORT] Failed to add entity to knowledge graph: {e}")
            return False
