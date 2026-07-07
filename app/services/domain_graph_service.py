"""
Domain-aware graph service - Issue #243.

This service builds graph visualization data based on the current
domain configuration, adapting nodes and edges to match the domain's
entity types and relationships.

Enhanced with visual improvements from Issue #269.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

import yaml

from app.domain.entities.services import get_entity_repository
from app.git_ops import git_ops
from app.model_schemas.domain_config import DomainConfiguration
from app.services.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)

# Module-level cache for graph data
# This cache persists across requests and is cleared when the repo is updated via webhooks
_graph_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}


def clear_graph_cache():
    """Clear the graph cache. Called when repository is updated via webhooks."""
    global _graph_cache
    _graph_cache.clear()
    logger.info("Domain graph cache cleared")


class DomainGraphService:
    """Service for building domain-aware graph visualizations."""

    def __init__(self, domain_config: DomainConfiguration):
        """
        Initialize with a domain configuration.

        Args:
            domain_config: The domain configuration to use
        """
        self.domain_config = domain_config
        self.entity_registry = get_entity_repository()
        self.knowledge_graph = KnowledgeGraph()
        self.git_ops = git_ops

    async def build_domain_graph(
        self,
        entity_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Build graph data based on domain configuration.

        Args:
            entity_types: Optional filter for specific entity types
            relationship_types: Optional filter for specific relationship types

        Returns:
            Dictionary with 'nodes' and 'edges' lists
        """
        # Create cache key based on domain and filters
        entity_types_str = ",".join(sorted(entity_types)) if entity_types else "all"
        relationship_types_str = (
            ",".join(sorted(relationship_types)) if relationship_types else "all"
        )
        cache_key = (
            f"{self.domain_config.id}:{entity_types_str}:{relationship_types_str}"
        )

        # Check cache first
        if cache_key in _graph_cache:
            logger.debug(f"Using cached graph for key: {cache_key}")
            return _graph_cache[cache_key]

        logger.info(
            f"Building new graph for domain: {self.domain_config.id}, filters: entity_types={entity_types}, relationship_types={relationship_types}"
        )

        nodes = []
        edges = []

        try:
            # Get all entity files from the repository
            entity_files = await self._get_entity_files()

            # Process each entity file
            for file_path in entity_files:
                entity_data = await self._load_entity_file(file_path)
                if not entity_data:
                    continue

                # Determine entity type from the file path or metadata
                entity_type = self._determine_entity_type(file_path, entity_data)

                # Skip if filtering by entity types
                if entity_types and entity_type not in entity_types:
                    continue

                # Skip if not a valid entity type for this domain
                if entity_type not in self.domain_config.entities:
                    continue

                # Create node
                node = self._create_node(entity_data, entity_type, file_path)
                nodes.append(node)

            # Build edges based on relationships
            edges = await self._build_edges(nodes, relationship_types)

            graph_data = {"nodes": nodes, "edges": edges}

            # Cache the result
            _graph_cache[cache_key] = graph_data
            logger.info(f"Cached graph with {len(nodes)} nodes and {len(edges)} edges")

            return graph_data

        except Exception as e:
            logger.error(f"Error building domain graph: {e}")
            return {"nodes": [], "edges": []}

    async def _get_entity_files(self) -> list[str]:
        """Get all entity files from the repository."""
        entity_files = []

        # Check common entity directories
        entity_dirs = [
            "entities",
            "people",
            "projects",
            "teams",
            "accounts",
            "contacts",
        ]

        for dir_name in entity_dirs:
            dir_path = os.path.join(self.git_ops.repo_path, dir_name)
            if os.path.exists(dir_path):
                for filename in os.listdir(dir_path):
                    if filename.endswith(".md"):
                        entity_files.append(os.path.join(dir_name, filename))

        return entity_files

    async def _load_entity_file(self, file_path: str) -> dict[str, Any] | None:
        """Load and parse an entity file."""
        try:
            full_path = os.path.join(self.git_ops.repo_path, file_path)
            if not os.path.exists(full_path):
                return None

            with open(full_path) as f:
                content = f.read()

            # Parse frontmatter (can be after initial content)
            # Look for frontmatter anywhere in the file
            if "---" in content:
                # Find the first occurrence of standalone ---
                lines = content.split("\n")
                start_idx = None
                end_idx = None

                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        if start_idx is None:
                            start_idx = i
                        elif end_idx is None:
                            end_idx = i
                            break

                if start_idx is not None and end_idx is not None:
                    # Extract YAML content
                    yaml_lines = lines[start_idx + 1 : end_idx]
                    yaml_content = "\n".join(yaml_lines)

                    try:
                        metadata = yaml.safe_load(yaml_content)
                        return {
                            "metadata": metadata,
                            "content": "\n".join(
                                lines[:start_idx] + lines[end_idx + 1 :]
                            ).strip(),
                            "file_path": file_path,
                        }
                    except yaml.YAMLError as e:
                        logger.error(f"YAML parse error in {file_path}: {e}")

            # Original logic for files starting with ---
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    metadata = yaml.safe_load(parts[1])
                    return {
                        "metadata": metadata,
                        "content": parts[2].strip(),
                        "file_path": file_path,
                    }

            # If no frontmatter, create basic metadata
            return {
                "metadata": {"name": os.path.basename(file_path).replace(".md", "")},
                "content": content,
                "file_path": file_path,
            }

        except Exception as e:
            logger.error(f"Error loading entity file {file_path}: {e}")
            return None

    def _determine_entity_type(
        self, file_path: str, entity_data: dict[str, Any]
    ) -> str:
        """Determine the entity type from file path or metadata."""
        # Check metadata first
        metadata = entity_data.get("metadata", {})
        if "entity_type" in metadata:
            return metadata["entity_type"]

        # Infer from directory
        dir_name = os.path.dirname(file_path)

        # Map directory names to entity types
        dir_mapping = {
            "people": "person",
            "projects": "project",
            "teams": "team",
            "accounts": "account",
            "contacts": "contact",
            "companies": "company",
        }

        if dir_name in dir_mapping:
            return dir_mapping[dir_name]

        # Check if directory name matches any domain entity type
        for entity_id in self.domain_config.entities:
            if (
                dir_name == entity_id
                or dir_name == self.domain_config.entities[entity_id].plural
            ):
                return entity_id

        # Default to first part of directory name
        return dir_name.rstrip("s") if dir_name else "unknown"

    def _create_node(
        self, entity_data: dict[str, Any], entity_type: str, file_path: str
    ) -> dict[str, Any]:
        """Create a node from entity data."""
        metadata = entity_data.get("metadata", {})

        # Generate ID
        entity_id = metadata.get("id")
        if not entity_id:
            # Generate from file path
            entity_id = (
                f"{entity_type}-{os.path.basename(file_path).replace('.md', '')}"
            )

        # Extract attributes based on domain configuration
        attributes = {"name": metadata.get("name", "Unknown")}

        if entity_type in self.domain_config.entities:
            entity_config = self.domain_config.entities[entity_type]
            # attributes is a list of DomainAttribute objects
            for attr in entity_config.attributes:
                if attr.name in metadata:
                    attributes[attr.name] = metadata[attr.name]

        # Add any additional metadata attributes
        for key, value in metadata.items():
            if (
                key not in ["id", "entity_type", "created_at", "updated_at"]
                and key not in attributes
            ):
                attributes[key] = value

        # Debug logging
        if "manages_accounts" in metadata or "works_on_projects" in metadata:
            logger.info(f"Entity {entity_id} metadata: {metadata}")
            logger.info(f"Entity {entity_id} attributes: {attributes}")

        return {
            "id": entity_id,
            "entity_type": entity_type,
            "attributes": attributes,
            "metadata": {
                "created_at": metadata.get("created_at", datetime.utcnow().isoformat()),
                "updated_at": metadata.get("updated_at", datetime.utcnow().isoformat()),
                "file_path": file_path,
            },
        }

    async def _build_edges(
        self,
        nodes: list[dict[str, Any]],
        relationship_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build edges based on domain relationships."""
        edges = []
        edge_id_counter = 1

        # Create a node lookup for efficiency
        node_lookup = {node["id"]: node for node in nodes}

        # Process each node to find relationships
        for node in nodes:
            entity_type = node["entity_type"]

            if entity_type not in self.domain_config.entities:
                continue

            entity_config = self.domain_config.entities[entity_type]

            # Debug logging
            if node["id"] == "person-sarah-chen":
                logger.info(f"Processing node: {node}")
                logger.info(
                    f"Entity config relationships: {entity_config.relationships}"
                )

            # Check each relationship type defined for this entity
            for relationship in entity_config.relationships:
                # Skip if filtering by relationship types
                if relationship_types and relationship.type not in relationship_types:
                    continue

                # Look for references in the node's attributes or metadata
                target_refs = self._find_relationship_targets(
                    node, relationship.target, relationship.type
                )

                # Create edges for each target
                for target_id in target_refs:
                    if target_id in node_lookup:
                        edge = {
                            "id": f"edge-{edge_id_counter}",
                            "source": node["id"],
                            "target": target_id,
                            "relationship_type": relationship.type,
                            "attributes": {},
                        }
                        edges.append(edge)
                        edge_id_counter += 1

        return edges

    def _find_relationship_targets(
        self, node: dict[str, Any], target_entity_type: str, relationship_type: str
    ) -> list[str]:
        """Find target entity IDs for a relationship."""
        targets = []

        # Check attributes for relationship references
        attributes = node.get("attributes", {})

        # Common relationship patterns
        relationship_fields = [
            relationship_type,
            f"{target_entity_type}_id",
            f"{target_entity_type}_ids",
            f"{relationship_type}_id",
            f"{relationship_type}_ids",
            target_entity_type,
            self.domain_config.entities.get(target_entity_type, {}).plural,
        ]

        for field in relationship_fields:
            if field in attributes:
                value = attributes[field]
                if isinstance(value, list):
                    # Handle list of IDs
                    for item in value:
                        if isinstance(item, str):
                            # Check if item already has entity type prefix
                            if item.startswith(f"{target_entity_type}-"):
                                targets.append(item)
                            else:
                                targets.append(f"{target_entity_type}-{item}")
                        elif isinstance(item, dict) and "id" in item:
                            targets.append(item["id"])
                elif isinstance(value, str):
                    # Single ID reference
                    if value.startswith(f"{target_entity_type}-"):
                        targets.append(value)
                    else:
                        targets.append(f"{target_entity_type}-{value}")
                elif isinstance(value, dict) and "id" in value:
                    # Object with ID
                    targets.append(value["id"])

        return targets

    async def get_statistics(self) -> dict[str, Any]:
        """Get statistics about the graph."""
        try:
            # Build full graph to get statistics
            graph_data = await self.build_domain_graph()

            nodes = graph_data["nodes"]
            edges = graph_data["edges"]

            # Count nodes by type
            nodes_by_type = {}
            for node in nodes:
                entity_type = node["entity_type"]
                nodes_by_type[entity_type] = nodes_by_type.get(entity_type, 0) + 1

            # Count edges by type
            edges_by_type = {}
            for edge in edges:
                rel_type = edge["relationship_type"]
                edges_by_type[rel_type] = edges_by_type.get(rel_type, 0) + 1

            return {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "nodes_by_type": nodes_by_type,
                "edges_by_type": edges_by_type,
                "domain": self.domain_config.id,
            }

        except Exception as e:
            logger.error(f"Error getting graph statistics: {e}")
            return {
                "total_nodes": 0,
                "total_edges": 0,
                "nodes_by_type": {},
                "edges_by_type": {},
                "domain": self.domain_config.id,
            }

    # === Enhanced Visual Methods (from Issue #269) ===

    # Visual Enhancement Constants
    MIN_NODE_SIZE = 10
    MAX_NODE_SIZE = 200
    MIN_OPACITY = 0.1
    MAX_OPACITY = 1.0
    MIN_EDGE_THICKNESS = 0.5
    MAX_EDGE_THICKNESS = 10.0

    async def build_enhanced_domain_graph(
        self, entity_types=None, relationship_types=None, display_config=None
    ):
        """
        Build enhanced graph with visual properties.

        Args:
            entity_types: Optional filter for entity types
            relationship_types: Optional filter for relationship types
            display_config: Display configuration with colors, shapes, icons

        Returns:
            Enhanced graph data with visual properties
        """
        # Get base graph data
        graph_data = await self.build_domain_graph(entity_types, relationship_types)

        if not display_config:
            display_config = {}

        # Enhance nodes with visual properties
        if graph_data["nodes"]:
            graph_data["nodes"] = await self.enhance_graph_nodes(
                graph_data["nodes"], display_config
            )

        # Enhance edges with visual properties
        if graph_data["edges"]:
            graph_data["edges"] = await self.enhance_graph_edges(graph_data["edges"])

        return graph_data

    async def enhance_graph_nodes(
        self, nodes: list[dict[str, Any]], display_config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Enhance nodes with visual properties."""
        enhanced_nodes = []

        for node in nodes:
            enhanced_node = node.copy()
            entity_type = node.get("entity_type", "default")

            # Add visual properties
            enhanced_node.update({
                "size": self._get_node_size(node),
                "color": self._get_node_color(entity_type, display_config),
                "shape": self._get_node_shape(entity_type, display_config),
                "opacity": self._get_node_opacity(node),
            })

            enhanced_nodes.append(enhanced_node)

        return enhanced_nodes

    async def enhance_graph_edges(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enhance edges with visual properties."""
        enhanced_edges = []

        for edge in edges:
            enhanced_edge = edge.copy()
            relationship_type = edge.get("relationship_type", "default")

            # Add visual properties
            enhanced_edge.update({
                "thickness": self._get_edge_thickness(relationship_type),
                "color": self._get_edge_color(relationship_type),
                "style": self._get_edge_style(relationship_type),
            })

            enhanced_edges.append(enhanced_edge)

        return enhanced_edges

    def _validate_color(self, color: str) -> str:
        """Validate and return a color value, defaulting to #666 if invalid."""
        if not color:
            return "#666"

        # Check for valid hex color
        if re.match(r"^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?$", color):
            return color

        # Check for valid named colors (basic list)
        valid_named_colors = {
            "red", "blue", "green", "yellow", "orange", "purple", "pink",
            "brown", "black", "white", "gray", "grey", "cyan", "magenta"
        }

        if color.lower() in valid_named_colors:
            return color.lower()

        return "#666"

    def _get_node_size(self, node: dict[str, Any]) -> int:
        """Calculate node size based on importance/connections."""
        # Base size
        size = 30

        # Increase size based on attributes or metadata
        attributes = node.get("attributes", {})
        if "importance" in attributes:
            importance = attributes["importance"]
            if isinstance(importance, (int, float)):
                size += min(importance * 10, 50)

        # Constrain to valid range
        return max(self.MIN_NODE_SIZE, min(size, self.MAX_NODE_SIZE))

    def _get_node_color(self, entity_type: str, display_config: dict[str, Any]) -> str:
        """Get color for node based on entity type."""
        colors = display_config.get("colors", {})
        color = colors.get(entity_type, self._get_default_color(entity_type))
        return self._validate_color(color)

    def _get_node_shape(self, entity_type: str, display_config: dict[str, Any]) -> str:
        """Get shape for node based on entity type."""
        shapes = display_config.get("shapes", {})
        return shapes.get(entity_type, "circle")

    def _get_node_opacity(self, node: dict[str, Any]) -> float:
        """Get opacity for node."""
        # Default opacity
        opacity = 0.8

        # Adjust based on node properties
        attributes = node.get("attributes", {})
        if "active" in attributes and not attributes["active"]:
            opacity = 0.4

        return max(self.MIN_OPACITY, min(opacity, self.MAX_OPACITY))

    def _get_edge_thickness(self, relationship_type: str) -> float:
        """Get thickness for edge based on relationship type."""
        thickness_map = {
            "reports_to": 3.0,
            "works_with": 2.0,
            "manages": 3.5,
            "collaborates": 2.0,
            "default": 1.5,
        }
        thickness = thickness_map.get(relationship_type, thickness_map["default"])
        return max(self.MIN_EDGE_THICKNESS, min(thickness, self.MAX_EDGE_THICKNESS))

    def _get_edge_color(self, relationship_type: str) -> str:
        """Get color for edge based on relationship type."""
        color_map = {
            "reports_to": "#ff6b6b",
            "works_with": "#4ecdc4",
            "manages": "#45b7d1",
            "collaborates": "#96ceb4",
            "default": "#666",
        }
        return color_map.get(relationship_type, color_map["default"])

    def _get_edge_style(self, relationship_type: str) -> str:
        """Get style for edge based on relationship type."""
        style_map = {
            "reports_to": "solid",
            "works_with": "dashed",
            "manages": "solid",
            "collaborates": "dotted",
            "default": "solid",
        }
        return style_map.get(relationship_type, style_map["default"])

    def _get_default_color(self, entity_type: str) -> str:
        """Get default color for entity type."""
        default_colors = {
            "person": "#4ecdc4",
            "project": "#45b7d1",
            "organization": "#96ceb4",
            "team": "#feca57",
            "default": "#666",
        }
        return default_colors.get(entity_type, default_colors["default"])
