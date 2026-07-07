"""
Graph Node Maintenance Tools — CRUD operations for knowledge graph nodes.

These agent tools provide validated, auditable node operations on the
Neo4j-backed knowledge graph. Each tool validates against the domain
config and delegates to Neo4jKnowledgeGraph public methods.
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import yaml

from ..agent_tools import AgentTool, ToolResult

logger = logging.getLogger(__name__)


async def _archive_source_file(
    graph_service,
    entity_id: str,
    git_ops,
    reason: str = "deleted",
) -> dict[str, Any]:
    """Soft-delete the source file for a graph entity by adding is_archived: true.

    Must be called BEFORE graph deletion — we need the node's source_file property.
    """
    # 1. Get source_file from graph (BEFORE deletion removes the node)
    entity_data = await graph_service.get_entity_by_id(entity_id)
    if not entity_data:
        return {"file_archived": False, "reason": "entity_not_found"}

    source_file = entity_data.get("metadata", {}).get("source_file")
    if not source_file:
        return {"file_archived": False, "reason": "no_source_file"}

    # 2. Read file, parse frontmatter, add is_archived
    repo_root = os.path.abspath(git_ops.repo_path)
    full_path = os.path.abspath(os.path.join(repo_root, source_file))
    if os.path.commonpath([full_path, repo_root]) != repo_root:
        return {"file_archived": False, "reason": "invalid_source_path"}
    if not os.path.exists(full_path):
        return {"file_archived": False, "reason": "file_not_found"}

    try:
        def _read_and_patch(path: str, _reason: str) -> str | None:
            """Read file, patch frontmatter with archive fields, return new content.

            Returns the patched content string, or None if frontmatter is missing.
            Runs in a thread to avoid blocking the event loop.
            """
            with open(path, encoding="utf-8") as f:
                content = f.read()

            if not content.startswith("---\n"):
                return None
            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                return None

            yaml_block = content[4:end_idx]
            body = content[end_idx + 5:]

            fm = yaml.safe_load(yaml_block) or {}
            fm["is_archived"] = True
            fm["archived_at"] = datetime.now(UTC).isoformat()
            fm["archive_reason"] = _reason

            new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
            return f"---\n{new_yaml}---\n{body}"

        patched = await asyncio.to_thread(_read_and_patch, full_path, reason)
        if patched is None:
            return {"file_archived": False, "reason": "no_frontmatter"}

        def _write_file(path: str, data: str) -> None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)

        await asyncio.to_thread(_write_file, full_path, patched)

        # 3. Git commit
        commit_msg = f"archive: soft-delete {entity_id} ({reason})"
        await git_ops.commit_and_push([source_file], commit_msg)

        return {"file_archived": True, "source_file": source_file}

    except Exception as e:
        logger.exception(f"Failed to archive source file for {entity_id}")
        return {"file_archived": False, "reason": f"error: {e}"}


def _get_graph_service():
    """Lazy import to avoid circular dependencies."""
    from ..graph.factory import get_knowledge_graph
    return get_knowledge_graph()


def _slugify(text: str) -> str:
    """Lowercase + dash-separate; mirrors the convention used by entity IDs."""
    import re

    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "unnamed"


def _entity_folder(entity_type: str) -> str | None:
    """Resolve the markdown folder convention for an entity type via domain config.

    Returns the entity type's `plural` field (e.g. 'members', 'focus_areas') or
    None if the entity type isn't defined in the active domain config.
    """
    try:
        from app.core.domain_config import get_domain_config

        config = get_domain_config()
        if not config or not config.entities:
            return None
        entity_def = config.entities.get(entity_type)
        if entity_def is None:
            return None
        return getattr(entity_def, "plural", None) or entity_type
    except Exception as e:
        logger.debug(f"_entity_folder: domain config lookup failed for {entity_type}: {e}")
        return None


async def _create_source_file(
    entity_type: str,
    name: str,
    properties: dict,
    git_ops,
) -> dict[str, Any]:
    """Create a markdown source file for a new entity and git-commit it.

    File path convention: `{plural}/{slug}.md` where `plural` comes from
    the domain config's entity definition and `slug` is derived from the
    display name. If the domain config doesn't define this entity type,
    skips file creation and returns {file_created: False, reason: ...}
    so the caller can still create the Neo4j node — graph-only is a
    valid fallback for synthetic entities not represented in the repo.

    Phase E2 of the MCP cleanup. Brings graph_add_node to parity with
    graph_delete_node and graph_merge_nodes, both of which already manage
    source files.
    """
    import os

    folder = _entity_folder(entity_type)
    if folder is None:
        return {
            "file_created": False,
            "reason": f"no_domain_config_for_type:{entity_type}",
        }

    slug = _slugify(name)
    rel_path = f"{folder}/{slug}.md"
    repo_root = os.path.abspath(git_ops.repo_path)
    full_path = os.path.abspath(os.path.join(repo_root, rel_path))

    # Guard against path traversal via maliciously crafted entity_type/name.
    if os.path.commonpath([full_path, repo_root]) != repo_root:
        return {"file_created": False, "reason": "invalid_path_after_join"}

    if os.path.exists(full_path):
        # Don't clobber existing files; let the caller resolve the conflict.
        return {"file_created": False, "reason": "file_already_exists", "source_file": rel_path}

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    fm = {
        "name": name,
        "entity_type": entity_type,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if properties:
        for k, v in properties.items():
            if k not in fm:
                fm[k] = v

    new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{new_yaml}---\n\n# {name}\n"

    def _write_file(path: str, data: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    await asyncio.to_thread(_write_file, full_path, content)

    try:
        await git_ops.commit_and_push([rel_path], f"add: create {entity_type} {name}")
    except Exception as e:
        # Non-fatal: file is on disk; git push failure shouldn't block the
        # graph mutation. Surface it in the response so the caller can decide.
        logger.warning(f"_create_source_file: git commit failed (non-fatal): {e}")
        return {
            "file_created": True,
            "source_file": rel_path,
            "git_committed": False,
            "git_error": str(e),
        }

    return {"file_created": True, "source_file": rel_path, "git_committed": True}


async def _update_source_file(
    graph_service,
    entity_id: str,
    properties: dict,
    git_ops,
) -> dict[str, Any]:
    """Patch an existing entity's source-file frontmatter and git-commit.

    Reads the entity's `source_file` from graph metadata, parses the
    markdown frontmatter, merges in the new property values, writes
    back, and git-commits. If the entity has no source_file (e.g. a
    synthetic entity not represented in the repo) returns
    {file_updated: False, reason: 'no_source_file'} — the caller still
    proceeds with the Neo4j update.

    Phase E2 of the MCP cleanup. Brings graph_update_node to parity
    with graph_delete_node and graph_merge_nodes.
    """
    entity_data = await graph_service.get_entity_by_id(entity_id)
    if not entity_data:
        return {"file_updated": False, "reason": "entity_not_found"}

    source_file = entity_data.get("metadata", {}).get("source_file")
    if not source_file:
        return {"file_updated": False, "reason": "no_source_file"}

    repo_root = os.path.abspath(git_ops.repo_path)
    full_path = os.path.abspath(os.path.join(repo_root, source_file))
    if os.path.commonpath([full_path, repo_root]) != repo_root:
        return {"file_updated": False, "reason": "invalid_source_path"}
    if not os.path.exists(full_path):
        return {"file_updated": False, "reason": "file_not_found"}

    try:
        def _read_and_patch(path: str, props: dict) -> str | None:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if not content.startswith("---\n"):
                return None
            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                return None
            yaml_block = content[4:end_idx]
            body = content[end_idx + 5:]
            fm = yaml.safe_load(yaml_block) or {}
            for k, v in props.items():
                fm[k] = v
            fm["updated_at"] = datetime.now(UTC).isoformat()
            new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
            return f"---\n{new_yaml}---\n{body}"

        patched = await asyncio.to_thread(_read_and_patch, full_path, properties)
        if patched is None:
            return {"file_updated": False, "reason": "no_frontmatter"}

        def _write_file(path: str, data: str) -> None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)

        await asyncio.to_thread(_write_file, full_path, patched)

        try:
            field_names = ", ".join(properties.keys())
            await git_ops.commit_and_push(
                [source_file], f"update: {entity_id} ({field_names})"
            )
            return {"file_updated": True, "source_file": source_file, "git_committed": True}
        except Exception as e:
            logger.warning(f"_update_source_file: git commit failed (non-fatal): {e}")
            return {
                "file_updated": True,
                "source_file": source_file,
                "git_committed": False,
                "git_error": str(e),
            }

    except Exception as e:
        logger.exception(f"_update_source_file failed for {entity_id}")
        return {"file_updated": False, "reason": f"error: {e}"}


class AddNodeTool(AgentTool):
    """Add a new entity node to the knowledge graph."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_add_node"

    @property
    def description(self) -> str:
        return "Add a new entity node to the knowledge graph with domain validation"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Entity type (must match domain config, e.g. 'person', 'project')",
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the entity",
                },
                "properties": {
                    "type": "object",
                    "description": "Optional additional properties",
                },
            },
            "required": ["entity_type", "name"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
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
            entity_type = inputs["entity_type"]
            name = inputs["name"]
            properties = inputs.get("properties") or {}

            # Phase E2: create the markdown source file FIRST (so the entity
            # has a discoverable file path), then create the Neo4j node.
            # If file creation is skipped (no domain config for type, or
            # file already exists) we still proceed with the graph-only
            # mutation — the caller gets file_created=False in the response
            # so they know to manage the file themselves.
            file_result = await _create_source_file(
                entity_type=entity_type,
                name=name,
                properties=properties,
                git_ops=self.git_ops,
            )

            graph = _get_graph_service()
            # If the file was created, propagate its source_file path into
            # the graph node properties so future updates / archival can
            # find it.
            graph_props = dict(properties)
            if file_result.get("file_created"):
                graph_props.setdefault("source_file", file_result["source_file"])

            node = await graph.add_node(
                entity_type=entity_type,
                name=name,
                properties=graph_props,
            )

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={
                    "node": node,
                    "created": True,
                    "file_created": file_result.get("file_created", False),
                    "source_file": file_result.get("source_file"),
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


class UpdateNodeTool(AgentTool):
    """Update properties on an existing entity node."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_update_node"

    @property
    def description(self) -> str:
        return "Update properties on an existing entity node in the knowledge graph"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to update",
                },
                "properties": {
                    "type": "object",
                    "description": "Properties to merge onto the node",
                },
            },
            "required": ["entity_id", "properties"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
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
            entity_id = inputs["entity_id"]
            properties = inputs["properties"]

            graph = _get_graph_service()

            # Phase E2: update the markdown source file's frontmatter
            # BEFORE the Neo4j update so the file mutation gets the
            # entity's existing source_file metadata (Neo4j update may
            # not be a destructive write to that field, but doing files
            # first is consistent with the delete/merge ordering).
            file_result = await _update_source_file(
                graph_service=graph,
                entity_id=entity_id,
                properties=properties,
                git_ops=self.git_ops,
            )

            node = await graph.update_node(
                entity_id=entity_id,
                properties=properties,
            )
            node_out = {
                **node,
                "updated_fields": node.get("updated_fields", []),
            }

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={
                    "node": node_out,
                    "file_updated": file_result.get("file_updated", False),
                    "source_file": file_result.get("source_file"),
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


class DeleteNodeTool(AgentTool):
    """Delete an entity node from the knowledge graph and archive its source file."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_delete_node"

    @property
    def description(self) -> str:
        return "Delete an entity node and archive its source file (soft-delete). Cascades to relationships by default."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to delete",
                },
                "cascade": {
                    "type": "boolean",
                    "description": "If true, also removes all relationships (default: true)",
                    "default": True,
                },
            },
            "required": ["entity_id"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "deleted_node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                    },
                },
                "relationships_removed": {"type": "integer"},
                "file_archived": {"type": "boolean"},
                "source_file": {"type": "string"},
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            entity_id = inputs["entity_id"]
            cascade = inputs.get("cascade", True)

            graph = _get_graph_service()

            # Archive source file BEFORE graph deletion (need node data)
            archive_result = await _archive_source_file(
                graph, entity_id, self.git_ops, reason="node_deleted"
            )

            deleted = await graph.delete_node(
                entity_id=entity_id,
                cascade=cascade,
            )

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={
                    "deleted_node": {
                        "id": deleted["id"],
                        "name": deleted["name"],
                        "type": deleted["type"],
                    },
                    "relationships_removed": deleted["relationships_removed"],
                    "file_archived": archive_result.get("file_archived", False),
                    "source_file": archive_result.get("source_file"),
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


class MergeNodesTool(AgentTool):
    """Merge a duplicate entity into a primary node, archiving the duplicate's source file."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)

    @property
    def name(self) -> str:
        return "graph_merge_nodes"

    @property
    def description(self) -> str:
        return (
            "Merge a duplicate entity into a primary entity, transferring "
            "all relationships and tracking the duplicate as an alias"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "primary_id": {
                    "type": "string",
                    "description": "The surviving entity ID",
                },
                "duplicate_id": {
                    "type": "string",
                    "description": "The entity ID to merge away",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["primary_wins", "duplicate_wins", "merge_all"],
                    "description": "Property conflict resolution strategy (default: primary_wins)",
                    "default": "primary_wins",
                },
            },
            "required": ["primary_id", "duplicate_id"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "merged_node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "properties": {"type": "object"},
                    },
                },
                "relationships_transferred": {"type": "integer"},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "duplicate_file_archived": {"type": "boolean"},
                "duplicate_source_file": {"type": "string"},
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            primary_id = inputs["primary_id"]
            duplicate_id = inputs["duplicate_id"]
            strategy = inputs.get("strategy", "primary_wins")

            graph = _get_graph_service()

            # Archive duplicate's source file BEFORE merge removes it
            archive_result = await _archive_source_file(
                graph, duplicate_id, self.git_ops,
                reason=f"merged_into_{primary_id}",
            )

            merged = await graph.merge_nodes(
                primary_id=primary_id,
                duplicate_id=duplicate_id,
                strategy=strategy,
            )

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={
                    "merged_node": {
                        "id": merged["id"],
                        "name": merged["name"],
                        "type": merged["type"],
                        "properties": merged.get("properties", {}),
                    },
                    "relationships_transferred": merged["relationships_transferred"],
                    "aliases": merged["aliases"],
                    "duplicate_file_archived": archive_result.get("file_archived", False),
                    "duplicate_source_file": archive_result.get("source_file"),
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
