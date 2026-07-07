"""
Graph Edge Maintenance Tools — CRUD operations for knowledge graph relationships.

These agent tools provide validated, auditable edge operations on the
Neo4j-backed knowledge graph. Each tool validates against the domain
config and delegates to Neo4jKnowledgeGraph public methods.
"""

import logging
import time
from typing import Any

from ..agent_tools import AgentTool, ToolResult

logger = logging.getLogger(__name__)


def _get_graph_service():
    """Lazy import to avoid circular dependencies."""
    from ..graph.factory import get_knowledge_graph
    return get_knowledge_graph()


class AddEdgeTool(AgentTool):
    """Add a relationship between two entities in the knowledge graph."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_add_edge"

    @property
    def description(self) -> str:
        return "Add a relationship between two entities in the knowledge graph with domain validation"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Source entity ID",
                },
                "target_id": {
                    "type": "string",
                    "description": "Target entity ID",
                },
                "relationship_type": {
                    "type": "string",
                    "description": "Relationship type (must match domain config, e.g. 'has_projects')",
                },
                "properties": {
                    "type": "object",
                    "description": "Optional relationship properties",
                },
            },
            "required": ["source_id", "target_id", "relationship_type"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "edge": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                        "properties": {"type": "object"},
                    },
                },
                "created": {"type": "boolean"},
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            source_id = inputs["source_id"]
            target_id = inputs["target_id"]
            relationship_type = inputs["relationship_type"]
            properties = inputs.get("properties")

            graph = _get_graph_service()
            edge = await graph.add_edge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                properties=properties,
            )
            edge_out = {
                "source": edge["source"],
                "target": edge["target"],
                "type": edge.get("relationship_type", edge.get("type")),
                "properties": edge.get("properties", {}),
            }

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={"edge": edge_out, "created": True},
                execution_time_ms=execution_time_ms,
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


class UpdateEdgeTool(AgentTool):
    """Update properties on an existing relationship."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_update_edge"

    @property
    def description(self) -> str:
        return "Update properties on an existing relationship in the knowledge graph"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Source entity ID",
                },
                "target_id": {
                    "type": "string",
                    "description": "Target entity ID",
                },
                "relationship_type": {
                    "type": "string",
                    "description": "The relationship type to update",
                },
                "properties": {
                    "type": "object",
                    "description": "Properties to merge onto the relationship",
                },
            },
            "required": ["source_id", "target_id", "relationship_type", "properties"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "edge": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                        "properties": {"type": "object"},
                        "updated_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            source_id = inputs["source_id"]
            target_id = inputs["target_id"]
            relationship_type = inputs["relationship_type"]
            properties = inputs["properties"]

            graph = _get_graph_service()
            edge = await graph.update_edge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                properties=properties,
            )
            edge_out = {
                "source": edge["source"],
                "target": edge["target"],
                "type": edge.get("relationship_type", edge.get("type")),
                "properties": edge.get("properties", {}),
                "updated_fields": edge.get("updated_fields", []),
            }

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={"edge": edge_out},
                execution_time_ms=execution_time_ms,
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


class DeleteEdgeTool(AgentTool):
    """Delete a relationship from the knowledge graph."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_delete_edge"

    @property
    def description(self) -> str:
        return "Delete a relationship between two entities in the knowledge graph"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Source entity ID",
                },
                "target_id": {
                    "type": "string",
                    "description": "Target entity ID",
                },
                "relationship_type": {
                    "type": "string",
                    "description": "The relationship type to delete",
                },
            },
            "required": ["source_id", "target_id", "relationship_type"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "deleted_edge": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                    },
                },
                "success": {"type": "boolean"},
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            source_id = inputs["source_id"]
            target_id = inputs["target_id"]
            relationship_type = inputs["relationship_type"]

            graph = _get_graph_service()
            deleted = await graph.delete_edge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
            )

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={
                    "deleted_edge": {
                        "source": deleted["source"],
                        "target": deleted["target"],
                        "type": deleted["relationship_type"],
                    },
                    "success": True,
                },
                execution_time_ms=execution_time_ms,
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
