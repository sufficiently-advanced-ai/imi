"""API routes for entity registry reset functionality."""

import logging
import os
import shutil

from fastapi import APIRouter, Query

from app.domain.entities.services import get_entity_repository
from app.models import ProcessingResult
from app.services.graph import get_knowledge_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entity-reset", tags=["entity-reset"])


@router.post("/registry")
async def reset_entity_registry() -> ProcessingResult:
    """Reset the entity registry to empty state."""
    try:
        registry = get_entity_repository()

        # Clear all collections
        registry.people.clear()
        registry.projects.clear()
        registry.teams.clear()
        registry._alias_index.clear()

        # Clear relationships if present
        if hasattr(registry, "relationships"):
            registry.relationships.clear()

        # Save empty state
        registry.save()

        return ProcessingResult(
            success=True,
            message="Entity registry reset successfully",
            data={
                "people_cleared": True,
                "projects_cleared": True,
                "teams_cleared": True,
                "aliases_cleared": True,
            },
        )
    except Exception as e:
        return ProcessingResult(
            success=False, message=f"Failed to reset entity registry: {str(e)}"
        )


@router.post("/knowledge-graph")
async def reset_knowledge_graph() -> ProcessingResult:
    """Reset the knowledge graph to empty state (both in-memory and Neo4j)."""
    try:
        # Use the global singleton — creating a new instance wouldn't affect the
        # singleton that all other API endpoints reference.
        kg = get_knowledge_graph()

        # Clear in-memory caches on whichever implementation is active
        if kg:
            for attr in ("nodes", "edges", "document_entities", "entity_documents"):
                cache = getattr(kg, attr, None)
                if cache is not None and hasattr(cache, "clear"):
                    cache.clear()

        # Remove legacy cache file
        cache_file = ".knowledge_graph.json"
        if os.path.exists(cache_file):
            os.remove(cache_file)

        # Clear Neo4j if the active implementation supports it
        neo4j_cleared = False
        try:
            if kg and hasattr(kg, "clear_all_data"):
                wipe_stats = await kg.clear_all_data()
                neo4j_cleared = True
                logger.info(f"Neo4j cleared during knowledge graph reset: {wipe_stats}")
        except Exception as neo4j_err:
            logger.warning(f"Neo4j clear failed (may not be available): {neo4j_err}")

        return ProcessingResult(
            success=True,
            message="Knowledge graph reset successfully",
            data={
                "nodes_cleared": True,
                "edges_cleared": True,
                "cache_removed": True,
                "neo4j_cleared": neo4j_cleared,
            },
        )
    except Exception as e:
        return ProcessingResult(
            success=False, message=f"Failed to reset knowledge graph: {str(e)}"
        )


@router.get("/stubs")
async def list_stub_entities() -> ProcessingResult:
    """List all stub entities in the knowledge graph.

    Stubs are entity nodes created as relationship targets that don't have
    source files. Some are legitimate; others are duplicates or corrupted.
    """
    try:
        from app.services.graph import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg or not hasattr(kg, "get_stub_entities"):
            return ProcessingResult(
                success=False,
                message="Neo4j knowledge graph not available",
            )

        stubs = await kg.get_stub_entities()
        return ProcessingResult(
            success=True,
            message=f"Found {len(stubs)} stub entities",
            data={"stubs": stubs, "count": len(stubs)},
        )
    except Exception as e:
        return ProcessingResult(
            success=False, message=f"Failed to list stubs: {str(e)}"
        )


@router.post("/stubs/process")
async def process_stub_entities(
    dry_run: bool = Query(True, description="If true, classify but don't delete"),
) -> ProcessingResult:
    """Classify and optionally delete bad stub entities.

    Bad stubs include:
    - YAML-contaminated (newlines in IDs)
    - Empty names
    - Near-duplicates of real entities
    - Suffix variants (e.g., "team-grants" stub when "team-grants-team" is real)
    """
    try:
        from app.services.graph import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg or not hasattr(kg, "process_stubs"):
            return ProcessingResult(
                success=False,
                message="Neo4j knowledge graph not available",
            )

        result = await kg.process_stubs(dry_run=dry_run)
        return ProcessingResult(
            success=True,
            message=(
                f"Processed {result['total_stubs']} stubs: "
                f"{result['bad']} bad, {result['good']} good, "
                f"{result['deleted']} deleted (dry_run={dry_run})"
            ),
            data=result,
        )
    except Exception as e:
        return ProcessingResult(
            success=False, message=f"Failed to process stubs: {str(e)}"
        )


@router.post("/repo")
async def clear_repo_directory() -> ProcessingResult:
    """Clear the repository directory."""
    try:
        repo_dir = "repo"

        if os.path.exists(repo_dir):
            # Remove all contents
            for filename in os.listdir(repo_dir):
                file_path = os.path.join(repo_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
        else:
            os.makedirs(repo_dir, exist_ok=True)

        return ProcessingResult(
            success=True,
            message="Repository directory cleared successfully",
            data={"path": repo_dir},
        )
    except Exception as e:
        return ProcessingResult(
            success=False, message=f"Failed to clear repository: {str(e)}"
        )


@router.post("/all")
async def reset_all() -> ProcessingResult:
    """Reset everything - registry, graph, and repo."""
    results = []

    # Reset entity registry
    registry_result = await reset_entity_registry()
    results.append(("registry", registry_result.success))

    # Reset knowledge graph
    graph_result = await reset_knowledge_graph()
    results.append(("graph", graph_result.success))

    # Clear repo
    repo_result = await clear_repo_directory()
    results.append(("repo", repo_result.success))

    all_success = all(success for _, success in results)

    return ProcessingResult(
        success=all_success,
        message="Full reset "
        + ("completed successfully" if all_success else "completed with errors"),
        data={"results": {name: success for name, success in results}},
    )
