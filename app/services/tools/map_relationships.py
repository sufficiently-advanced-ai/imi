"""
Map Relationships Tool - Connect entities and concepts.
"""

import time
from typing import Any

from ..agent_tools import AgentTool, ToolResult


class MapRelationshipsTool(AgentTool):
    """Tool for mapping relationships between entities and concepts."""

    @property
    def name(self) -> str:
        return "map_relationships"

    @property
    def description(self) -> str:
        return (
            "Connect entities and concepts, identifying relationships and dependencies"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Content to analyze for relationships",
                },
                "relationship_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["collaboration", "hierarchy", "dependency", "conflict"],
                },
            },
            "required": ["content"],
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
                            "strength": {"type": "number"},
                            "description": {"type": "string"},
                        },
                    },
                }
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute relationship mapping using the knowledge graph."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            from ..knowledge_graph import get_knowledge_graph

            content = inputs.get("content", "")
            relationship_types = inputs.get(
                "relationship_types",
                ["collaboration", "hierarchy", "dependency", "conflict"],
            )

            # Build/update knowledge graph
            await get_knowledge_graph().build_graph()

            # Extract entities from content using existing entity extraction
            extracted_entities = await self._extract_entities_from_content(content)

            # Find relationships between extracted entities
            relationships = []
            entities = []

            # Collect all entity IDs from extracted entities
            for entity_type, entity_list in extracted_entities.items():
                for entity in entity_list:
                    entity_id = (
                        f"{entity_type[:-1]}:{entity['name'].lower().replace(' ', '-')}"
                    )
                    entities.append(entity_id)

            # Find relationships between each pair of entities
            for i, entity1 in enumerate(entities):
                for entity2 in entities[i + 1 :]:
                    related = await get_knowledge_graph().find_related_entities(
                        entity1, max_results=1
                    )

                    for relation in related:
                        if relation["entity"]["id"] == entity2:
                            relationships.append(
                                {
                                    "source": get_knowledge_graph().nodes[entity1].name
                                    if entity1 in get_knowledge_graph().nodes
                                    else entity1,
                                    "target": get_knowledge_graph().nodes[entity2].name
                                    if entity2 in get_knowledge_graph().nodes
                                    else entity2,
                                    "type": relation["relationship"]["type"],
                                    "strength": relation["relationship"]["strength"],
                                    "description": f"Connected through {relation['relationship']['shared_documents']} shared documents",
                                }
                            )
                            break

            result = ToolResult(
                success=True,
                data={
                    "relationships": relationships,
                    "entities_analyzed": len(entities),
                    "graph_stats": await self._get_graph_stats(),
                    "relationship_types_requested": relationship_types,
                    "content_length": len(content),
                },
                execution_time_ms=int((time.time() - start_time) * 1000),
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

    async def _extract_entities_from_content(
        self, content: str
    ) -> dict[str, list[dict]]:
        """Extract entities from content using simple text analysis."""
        # This is a simplified version - in practice, you'd use the ExtractEntitiesTool
        entities = {"people": [], "projects": [], "teams": []}

        # Simple name detection (words starting with capital letters)
        import re

        # Find potential person names (two consecutive capitalized words)
        person_matches = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", content)
        for match in person_matches[:5]:  # Limit to avoid noise
            entities["people"].append({"name": match})

        # Find project mentions (Project X, Initiative Y, etc.)
        project_matches = re.findall(
            r"\b(?:Project|Initiative|Program)\s+([A-Z][a-zA-Z\s]+)\b", content
        )
        for match in project_matches:
            entities["projects"].append({"name": f"Project {match.strip()}"})

        # Find team mentions (X Team, Y Department)
        team_matches = re.findall(
            r"\b([A-Z][a-zA-Z\s]+)\s+(?:Team|Department|Group)\b", content
        )
        for match in team_matches:
            entities["teams"].append({"name": f"{match.strip()} Team"})

        return entities

    async def _get_graph_stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics."""
        try:
            from ..knowledge_graph import get_knowledge_graph

            return get_knowledge_graph()._get_graph_stats()
        except Exception:
            return {}
