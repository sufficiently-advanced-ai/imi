"""
Infer Relationships Tool — Discover entity relationships from content via LLM.

Unlike map_relationships (which searches existing graph edges), this tool
*infers* relationships directly from raw text. Designed for the ingestion
pipeline where the graph may be empty or sparse.
"""

import logging
import time
from typing import Any

import yaml

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from .yaml_utils import extract_yaml_block, parse_yaml_list

logger = logging.getLogger(__name__)

# Edges below this confidence do not get written to the graph. Matches the
# decision_detector threshold convention in config/workflows/*.yaml.
MIN_RELATIONSHIP_CONFIDENCE = 0.7

# Fallback vocabulary when no domain config is active. With a domain config,
# the allowed types come from the domain's relationship schema instead.
VALID_RELATIONSHIP_TYPES = {
    "LEADS", "OWNS", "MEMBER_OF", "WORKS_ON", "REPORTS_TO",
    "COLLABORATES_WITH", "DEPENDS_ON",
}


# Relationship types excluded from LLM inference: discussed_topic degenerates
# into "every speaker discussed every project" — typed-edge co-occurrence
# noise. It remains valid when sourced from explicit entity-file frontmatter.
NOISY_RELATIONSHIP_TYPES = {"discussed_topic"}


def _load_domain_relationship_schema() -> list[dict] | None:
    """Allowed relationship signatures from the active domain config.

    Returns [{"type", "source", "target"}] including inverse names, or None
    when no domain is configured. NOISY_RELATIONSHIP_TYPES are excluded.
    """
    try:
        from app.core.domain_config.domain_config_service import (
            get_domain_config_service,
        )
    except ImportError as e:
        # Domain-config subsystem genuinely absent → no schema to enforce.
        logger.warning("[INFER-REL] Domain config service unavailable: %s", e)
        return None

    # Fail closed on unexpected load failures: returning None here drops into
    # permissive fallback validation and could persist schema-invalid edges.
    # Let unexpected errors propagate to the tool's outer error handling.
    domain = get_domain_config_service().get_active_domain()
    if not domain or not domain.entities:
        return None

    schema: list[dict] = []
    seen: set[tuple] = set()
    for source_type, entity in domain.entities.items():
        for rel in entity.relationships:
            if rel.type in NOISY_RELATIONSHIP_TYPES or (
                rel.inverse_name and rel.inverse_name in NOISY_RELATIONSHIP_TYPES
            ):
                continue
            key = (rel.type, source_type, rel.target)
            if key not in seen:
                seen.add(key)
                schema.append(
                    {
                        "type": rel.type,
                        "source": source_type,
                        "target": rel.target,
                        "inverse": rel.inverse_name or None,
                    }
                )
            if rel.inverse_name:
                inv = (rel.inverse_name, rel.target, source_type)
                if inv not in seen:
                    seen.add(inv)
                    schema.append(
                        {
                            "type": rel.inverse_name,
                            "source": rel.target,
                            "target": source_type,
                            "inverse": rel.type,
                        }
                    )
    return schema or None


class InferRelationshipsTool(AgentTool):
    """Infer relationships between entities from content using LLM analysis."""

    @property
    def name(self) -> str:
        return "infer_relationships"

    @property
    def description(self) -> str:
        return "Identify relationships between entities mentioned in content"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to analyze for relationships",
                },
                "entities": {
                    "type": "array",
                    "description": "Entities already extracted from the content",
                    "items": {"type": "object"},
                },
            },
            "required": ["content", "entities"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                            "evidence": {"type": "string"},
                        },
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Infer relationships between entities from content."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            content = inputs.get("content", "")
            entities = inputs.get("entities", [])

            if not entities or len(entities) < 2:
                # Need at least 2 entities to form relationships
                result = ToolResult(
                    success=True,
                    data={"relationships": []},
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )
                self._finish_execution(execution, result)
                return result

            schema = _load_domain_relationship_schema()
            prompt = self._build_prompt(content, entities, schema)
            response = await self.claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=2048,
                temperature=0.0,
                operation="infer_relationships",
            )

            # Extract response text
            response_text = self._get_response_text(response)

            # Parse YAML
            yaml_content = extract_yaml_block(response_text)
            raw_relationships = parse_yaml_list(yaml_content, "relationships")

            # Validate and clean relationships
            entity_ids = {e.get("id", "") for e in entities}
            relationships = self._validate_relationships(
                raw_relationships, entity_ids, schema
            )

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={"relationships": relationships},
                execution_time_ms=execution_time_ms,
            )
            self._finish_execution(execution, result)
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=False,
                data={"relationships": []},
                execution_time_ms=execution_time_ms,
                error=str(e),
            )
            self._finish_execution(execution, result)
            return result

    def _build_prompt(
        self,
        content: str,
        entities: list[dict],
        schema: list[dict] | None = None,
    ) -> str:
        """Build the relationship inference prompt (domain-constrained when a
        domain relationship schema is available)."""
        entity_list = yaml.dump(
            [{"id": e.get("id", ""), "name": e.get("name", ""), "type": e.get("type", "")}
             for e in entities],
            default_flow_style=False,
        )

        if schema:
            schema_lines = "\n".join(
                f"- {r['type']}: {r['source']} -> {r['target']}" for r in schema
            )
        else:
            schema_lines = "\n".join(
                f"- {t}: any -> any" for t in sorted(VALID_RELATIONSHIP_TYPES)
            )

        from app.services.prompt_loader import load_prompt

        template = load_prompt("extract_relationships")
        # The XML stores < and > escaped; restore for the actual prompt text.
        template = template.replace("&lt;", "<").replace("&gt;", ">")
        return template.format(
            entity_list=entity_list,
            content=content,
            relationship_schema=schema_lines,
        )

    @staticmethod
    def _entity_type_of(entity_id: str) -> str:
        return entity_id.split("-", 1)[0] if "-" in entity_id else ""

    def _validate_relationships(
        self,
        raw: list,
        entity_ids: set[str],
        schema: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate parsed relationships against the domain schema.

        Schema-invalid or unknown types are DROPPED, never coerced — a wrong
        typed edge is worse than no edge. Confidence below
        MIN_RELATIONSHIP_CONFIDENCE is dropped too.
        """
        allowed: set[tuple] | None = None
        inverse_of: dict[str, str] = {}
        if schema:
            allowed = {(r["type"], r["source"], r["target"]) for r in schema}
            inverse_of = {
                r["type"]: r["inverse"] for r in schema if r.get("inverse")
            }

        valid = []
        seen_edges: set[tuple] = set()
        for rel in raw:
            if not isinstance(rel, dict):
                continue
            source = rel.get("source", "")
            target = rel.get("target", "")
            rel_type = (rel.get("type") or "").strip()

            # Both entities must exist in extracted set
            if source not in entity_ids or target not in entity_ids:
                continue
            # No self-relationships
            if source == target:
                continue

            raw_confidence = rel.get("confidence")
            if raw_confidence is None:
                confidence = 0.8  # legacy outputs carry no confidence
            elif isinstance(raw_confidence, bool):
                # bool is a subclass of int — treat as invalid, not 1.0/0.0
                logger.info(
                    "[INFER-REL] Dropping edge with non-numeric confidence "
                    "%r %s-[%s]->%s",
                    raw_confidence, source, rel_type, target,
                )
                continue
            elif isinstance(raw_confidence, (int, float)):
                confidence = float(raw_confidence)
            else:
                # Parse numeric strings (e.g. "0.2"); drop genuinely invalid
                # values rather than defaulting them above threshold.
                try:
                    confidence = float(str(raw_confidence).strip())
                except (TypeError, ValueError):
                    logger.info(
                        "[INFER-REL] Dropping edge with non-numeric confidence "
                        "%r %s-[%s]->%s",
                        raw_confidence, source, rel_type, target,
                    )
                    continue
            if confidence < MIN_RELATIONSHIP_CONFIDENCE:
                logger.debug(
                    "[INFER-REL] Dropping low-confidence edge %s-[%s]->%s (%.2f)",
                    source, rel_type, target, confidence,
                )
                continue

            if allowed is not None:
                key = (
                    rel_type.lower(),
                    self._entity_type_of(source),
                    self._entity_type_of(target),
                )
                if key not in allowed:
                    logger.info(
                        "[INFER-REL] Dropping schema-invalid edge %s-[%s]->%s",
                        source, rel_type, target,
                    )
                    continue
                rel_type = rel_type.lower()
            elif rel_type.upper() not in VALID_RELATIONSHIP_TYPES:
                logger.info(
                    "[INFER-REL] Dropping unknown-type edge %s-[%s]->%s",
                    source, rel_type, target,
                )
                continue
            else:
                rel_type = rel_type.upper()

            # Drop duplicates and inverse-pair duplicates: the model sometimes
            # emits both directions of the same edge (belongs_to_account AND
            # has_projects) despite instructions; writing both would create
            # two redundant graph edges.
            edge_key = (rel_type, source, target)
            inverse_key = (inverse_of.get(rel_type, ""), target, source)
            if edge_key in seen_edges or (
                inverse_key[0] and inverse_key in seen_edges
            ):
                logger.debug(
                    "[INFER-REL] Dropping duplicate/inverse-duplicate edge "
                    "%s-[%s]->%s",
                    source, rel_type, target,
                )
                continue
            seen_edges.add(edge_key)

            valid.append({
                "source": source,
                "target": target,
                "type": rel_type,
                "confidence": round(float(confidence), 2),
                "description": rel.get("description", ""),
                "evidence": rel.get("evidence", ""),
            })
        return valid

    @staticmethod
    def _get_response_text(response) -> str:
        """Extract text from Claude API response."""
        if hasattr(response, "content"):
            content_data = response.content
            if isinstance(content_data, list) and len(content_data) > 0:
                return content_data[0].text if hasattr(content_data[0], "text") else str(content_data[0])
            return str(content_data)
        return str(response)
