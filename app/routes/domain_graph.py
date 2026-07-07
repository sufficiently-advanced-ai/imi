"""
Domain-aware graph visualization API endpoints - Issue #243.

These endpoints provide graph data that adapts to the current domain
configuration, returning nodes and edges based on entity types and
relationships defined in the domain.

Uses unified KnowledgeGraph via GraphVisualizationAdapter which includes
semantic relationships discovered during meeting processing.
"""

import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.domain_config import DomainConfigService as DomainConfigLoader
from app.core.domain_config import get_domain_config
from app.services.domain_config import (
    get_domain_config_loader,
)
from app.services.graph import GraphVisualizationAdapter, get_knowledge_graph
from app.services.graph.factory import (
    GRAPH_RESPONSE_CACHE_TTL,
    get_graph_response_cache,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/domain-graph", tags=["domain-graph"])


@router.get("")
@router.get("/")
async def get_domain_graph(
    domain: str | None = Query(None, description="Specific domain ID to use"),
    entity_types: str | None = Query(
        None, description="Comma-separated entity types"
    ),
    relationship_types: str | None = Query(
        None, description="Comma-separated relationship types"
    ),
    snapshot: str | None = Query(None, description="Demo snapshot: day1, week1, month1, month3"),
    include_signals: bool = Query(
        False,
        description="Include Signal nodes (decisions, action items, etc.). "
                    "Off by default to keep the default view navigable.",
    ),
    limit: int = Query(
        500,
        ge=10,
        le=5000,
        description="Max nodes returned, ranked by connection count. "
                    "Prevents multi-minute layout times on large graphs.",
    ),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> dict:
    """
    Get graph data based on current domain configuration.
    This is the main endpoint that the frontend uses.
    """
    # Parse comma-separated values
    entity_type_list = entity_types.split(",") if entity_types else None
    relationship_type_list = (
        relationship_types.split(",") if relationship_types else None
    )

    # Forward to the visualization endpoint
    return await get_domain_graph_visualization(
        domain=domain,
        entity_types=entity_type_list,
        relationship_types=relationship_type_list,
        snapshot=snapshot,
        include_signals=include_signals,
        limit=limit,
        domain_loader=domain_loader,
    )


@router.get("/visualization")
async def get_domain_graph_visualization(
    domain: str | None = Query(None, description="Specific domain ID to use"),
    entity_types: list[str] | None = Query(
        None, description="Filter by entity types"
    ),
    relationship_types: list[str] | None = Query(
        None, description="Filter by relationship types"
    ),
    snapshot: str | None = Query(None, description="Demo snapshot: day1, week1, month1, month3"),
    include_signals: bool = Query(False, description="Include Signal nodes"),
    limit: int = Query(500, ge=10, le=5000, description="Max nodes (by degree)"),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> dict:
    """
    Get graph visualization data based on current domain configuration.

    Uses the unified KnowledgeGraph via GraphVisualizationAdapter,
    which includes relationships discovered during meeting processing.

    In DEMO_MODE, can return pre-built snapshots using the snapshot parameter.

    Args:
        domain: Optional specific domain ID to use
        entity_types: Optional list of entity types to include
        relationship_types: Optional list of relationship types to include
        snapshot: Optional demo snapshot (day1, week1, month1, month3)

    Returns:
        Dictionary containing nodes, edges, domain config, and statistics
    """
    try:
        # Check response cache
        _graph_cache, _graph_cache_time = get_graph_response_cache()
        # Include include_signals + limit in the cache key so toggling these
        # on the client doesn't return stale data from a different payload shape.
        cache_key = (
            f"{domain}|{entity_types}|{relationship_types}|{snapshot}"
            f"|signals={include_signals}|limit={limit}"
        )
        now = time.time()
        # Prune expired entries to prevent unbounded growth
        expired = [k for k, ts in _graph_cache_time.items() if (now - ts) >= GRAPH_RESPONSE_CACHE_TTL]
        for k in expired:
            _graph_cache.pop(k, None)
            _graph_cache_time.pop(k, None)
        if cache_key in _graph_cache and (now - _graph_cache_time.get(cache_key, 0)) < GRAPH_RESPONSE_CACHE_TTL:
            logger.info(f"[DOMAIN_GRAPH] Cache hit for key={cache_key[:60]}")
            return _graph_cache[cache_key]

        # Demo mode: return fixture data if snapshot specified or DEMO_MODE enabled
        from app.config import settings
        if snapshot or settings.DEMO_MODE:
            from app.routes.demo import load_fixture
            snapshot_name = snapshot or "month3"  # Default to largest snapshot
            fixture_data = load_fixture("graph", f"snapshot_{snapshot_name}")
            if fixture_data:
                logger.info(f"[DOMAIN_GRAPH] Returning demo snapshot: {snapshot_name}")
                return {
                    "nodes": fixture_data.get("nodes", []),
                    "edges": fixture_data.get("edges", []),
                    "domain_config": {"id": "demo", "name": "Demo Domain"},
                    "statistics": fixture_data.get("statistics", {}),
                    "demo_mode": True,
                    "snapshot": snapshot_name,
                }
        # Get domain configuration
        if domain:
            config = await domain_loader.get_config_by_id(domain)
            if not config:
                raise HTTPException(
                    status_code=404, detail=f"Domain not found: {domain}"
                )
        else:
            config = get_domain_config()

        knowledge_graph = get_knowledge_graph()
        adapter = GraphVisualizationAdapter(knowledge_graph, config)

        # Fast path: filtered Cypher directly, no build_graph() / _sync_from_neo4j.
        # This is what makes cold-start requests respond in ~1s instead of minutes.
        # Fall back to the legacy full-load path if the fast path is unavailable
        # (e.g. a different KG implementation during Semantica development).
        fast_path_used = False
        graph_data: dict[str, Any]
        if hasattr(knowledge_graph, "query_for_visualization"):
            try:
                nodes_dict, edges_dict = await knowledge_graph.query_for_visualization(
                    entity_types=entity_types,
                    relationship_types=relationship_types,
                    include_signals=include_signals,
                    limit=limit,
                )
                vis = adapter.convert_subgraph(nodes_dict, edges_dict)
                total_available = await knowledge_graph.count_entities_for_visualization(
                    entity_types=entity_types,
                    include_signals=include_signals,
                )
                graph_data = {
                    "nodes": vis["nodes"],
                    "edges": vis["edges"],
                    "truncation": {
                        "truncated": total_available > limit,
                        "total_available_nodes": total_available,
                        "limit": limit,
                    },
                }
                fast_path_used = True
            except Exception as fast_err:
                logger.warning(
                    f"[DOMAIN_GRAPH] Fast path failed, falling back to full build: {fast_err}"
                )

        if not fast_path_used:
            graph_data = await adapter.build_visualization_data(
                entity_types=entity_types,
                relationship_types=relationship_types,
                include_semantic_edges=True,
                include_signals=include_signals,
                limit=limit,
            )

        # Statistics: if we took the fast path, kg.nodes may be empty (we didn't
        # force a full build), so get_statistics() would return zeros. Synthesize
        # a minimal stats dict from the fast-path result instead.
        if fast_path_used:
            statistics = {
                "total_nodes": graph_data["truncation"]["total_available_nodes"],
                "total_edges": len(graph_data["edges"]),
                "fast_path": True,
            }
        else:
            statistics = await adapter.get_statistics()

        truncation = graph_data.get("truncation", {})
        statistics["truncated"] = truncation.get("truncated", False)
        statistics["total_available_nodes"] = truncation.get("total_available_nodes", len(graph_data["nodes"]))
        statistics["limit"] = truncation.get("limit")
        statistics["include_signals"] = include_signals

        logger.info(
            f"[DOMAIN_GRAPH] Built graph: {len(graph_data['nodes'])} nodes, "
            f"{len(graph_data['edges'])} edges "
            f"(signals={include_signals}, limit={limit}, "
            f"truncated={statistics['truncated']})"
        )

        result = {
            "nodes": graph_data["nodes"],
            "edges": graph_data["edges"],
            "domain_config": config.dict(),
            "statistics": statistics,
        }

        # Store in cache
        _graph_cache[cache_key] = result
        _graph_cache_time[cache_key] = time.time()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building domain graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/neighborhood")
async def get_domain_graph_neighborhood(
    seed: str = Query(..., description="Entity ID to center the subgraph on"),
    depth: int = Query(2, ge=1, le=3, description="Hops from seed (1-3)"),
    include_signals: bool = Query(False, description="Include Signal nodes attached to entities in scope"),
    limit: int = Query(200, ge=10, le=1000, description="Max entity nodes; signals add on top"),
    domain: str | None = Query(None, description="Specific domain ID (for display config)"),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> dict:
    """Return a focused k-hop subgraph around a seed entity.

    Unlike /api/domain-graph (which builds the full graph in memory), this
    runs ~2-4 bounded Cypher queries and is fast regardless of total graph
    size. Intended for the context-graph UX: pick a seed, see its neighborhood,
    expand by re-rooting on any visible node.
    """
    try:
        _graph_cache, _graph_cache_time = get_graph_response_cache()
        # Separate namespace so these never collide with full-graph cache keys
        cache_key = f"nbhd:{domain}|seed={seed}|d={depth}|s={include_signals}|l={limit}"
        now = time.time()
        # Prune expired entries — the neighborhood endpoint shares the graph
        # response cache with /visualization, and without this sweep those
        # keys grow unbounded when /visualization isn't also being exercised.
        expired = [k for k, ts in _graph_cache_time.items() if (now - ts) >= GRAPH_RESPONSE_CACHE_TTL]
        for k in expired:
            _graph_cache.pop(k, None)
            _graph_cache_time.pop(k, None)
        if cache_key in _graph_cache and (now - _graph_cache_time.get(cache_key, 0)) < GRAPH_RESPONSE_CACHE_TTL:
            logger.info(f"[NEIGHBORHOOD] Cache hit for {cache_key[:80]}")
            return _graph_cache[cache_key]

        if domain:
            config = await domain_loader.get_config_by_id(domain)
            if not config:
                raise HTTPException(status_code=404, detail=f"Domain not found: {domain}")
        else:
            config = get_domain_config()

        knowledge_graph = get_knowledge_graph()
        # Neighborhood skips build_graph — it runs directly against Neo4j
        nodes, edges = await knowledge_graph.neighborhood(
            seed_id=seed,
            depth=depth,
            include_signals=include_signals,
            limit=limit,
        )
        if not nodes:
            raise HTTPException(status_code=404, detail=f"Seed entity not found: {seed}")

        adapter = GraphVisualizationAdapter(knowledge_graph, config)
        vis = adapter.convert_subgraph(nodes, edges)

        result = {
            "nodes": vis["nodes"],
            "edges": vis["edges"],
            "domain_config": config.dict(),
            "statistics": {
                "seed": seed,
                "depth": depth,
                "total_nodes": len(vis["nodes"]),
                "total_edges": len(vis["edges"]),
                "include_signals": include_signals,
                "truncated": len(vis["nodes"]) >= limit,
                "limit": limit,
            },
        }

        _graph_cache[cache_key] = result
        _graph_cache_time[cache_key] = time.time()
        logger.info(
            f"[NEIGHBORHOOD] seed={seed} depth={depth} signals={include_signals} "
            f"→ {len(vis['nodes'])} nodes, {len(vis['edges'])} edges"
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building neighborhood for seed={seed}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/top-entities")
async def get_top_entities(
    limit: int = Query(20, ge=5, le=100, description="Number of entities to return"),
    entity_types: str | None = Query(
        None, description="Comma-separated entity types to filter"
    ),
    domain: str | None = Query(
        None,
        description="Domain ID. Constrains the seed list to entity types defined "
                    "by this domain so seeds line up with /neighborhood and /visualization.",
    ),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> dict:
    """Return the most-connected entities, ranked by degree.

    Cheap direct Cypher — bypasses build_graph() entirely. Intended as the
    initial seed picker in the context-graph UI, so the page loads instantly
    and the user picks a starting point rather than waiting for a full graph.
    """
    try:
        knowledge_graph = get_knowledge_graph()

        # Resolve domain-scoped entity types so the picker never suggests seeds
        # outside the active domain. Without this, a /top-entities result can
        # expand into a different graph than the /neighborhood call that
        # follows, producing 404s or cross-domain neighborhoods.
        domain_types: list[str] | None = None
        if domain:
            config = await domain_loader.get_config_by_id(domain)
            if not config:
                raise HTTPException(status_code=404, detail=f"Domain not found: {domain}")
            domain_types = list(config.entities.keys())

        # Degree-ranked query directly against Neo4j. No in-memory graph
        # materialization — this returns in tens of ms even on large graphs.
        #
        # Always exclude :Signal nodes. Signals are dual-labeled `:Entity:Signal`
        # so they'd otherwise slip into this picker; but the neighborhood path
        # rejects signal seeds, which means the UI would show buttons that 404
        # the moment you click them.
        clauses: list[str] = ["NOT n:Signal"]
        params: dict[str, Any] = {"limit": limit}
        effective_types: list[str] | None = None
        if entity_types:
            explicit = [t.strip() for t in entity_types.split(",") if t.strip()]
            effective_types = explicit or None
        # If both domain and entity_types are given, intersect so the picker
        # only returns types valid in that domain.
        if domain_types is not None and effective_types is not None:
            effective_types = [t for t in effective_types if t in domain_types] or ["__none__"]
        elif domain_types is not None:
            effective_types = domain_types
        if effective_types:
            clauses.append("n.entity_type IN $types")
            params["types"] = effective_types
        where_clause = "WHERE " + " AND ".join(clauses)

        # Neo4j 5.x deprecated size((n)--()) — use COUNT{} subquery.
        cypher = f"""
        MATCH (n:Entity)
        {where_clause}
        WITH n, COUNT {{ (n)--() }} AS degree
        WHERE degree > 0
        RETURN n.id AS id, n.name AS name, n.entity_type AS type, degree
        ORDER BY degree DESC
        LIMIT $limit
        """
        results = await knowledge_graph.neo4j.execute_read(cypher, params)
        entities = [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "degree": r["degree"],
            }
            for r in results
        ]
        return {"entities": entities, "count": len(entities), "domain": domain}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching top entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class EntityDisplay(BaseModel):
    icon: str
    color: str
    label_attribute: str


@router.get("/display-config", response_model=dict[str, EntityDisplay])
async def get_graph_display_config(
    domain: str | None = None,
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> dict[str, EntityDisplay]:
    """
    Get display configuration for graph entities.

    Returns display properties (icon, color, label_attribute) for each entity type.
    """
    try:
        if domain:
            config = await domain_loader.get_config_by_id(domain)
            if not config:
                raise HTTPException(
                    status_code=404, detail=f"Domain not found: {domain}"
                )
        else:
            config = get_domain_config()

        # Generate display config for each entity type from domain config
        display_config = {}

        # Icon hints based on common entity names
        icon_hints = {
            "account": "building", "organization": "building", "company": "building",
            "project": "folder", "engagement": "folder",
            "person": "user", "candidate": "user", "contact": "user",
            "member": "user", "participant": "user",
            "team": "users", "department": "users", "cohort": "users",
            "role": "briefcase", "assessment": "clipboard",
            "competency": "star", "interview_session": "calendar",
            "focus_area": "target", "topic": "target",
        }

        # Color palette — enough for any domain
        palette = [
            "#4ecdc4", "#45b7d1", "#96ceb4", "#feca57", "#ff6b6b",
            "#a29bfe", "#fd79a8", "#6c5ce7", "#00b894", "#e17055",
            "#0984e3", "#d63031",
        ]

        for i, entity_id in enumerate(config.entities.keys()):
            display_config[entity_id] = {
                "icon": icon_hints.get(entity_id, "circle"),
                "color": palette[i % len(palette)],
                "label_attribute": "name",
            }

        return display_config

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting display config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_domain_graph(
    query: str = Query(..., description="Search query"),
    entity_types: str | None = Query(
        None, description="Comma-separated entity types to filter"
    ),
    max_results: int = Query(10, ge=1, le=50, description="Maximum results to return"),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> list[dict]:
    """
    Search for entities in the domain graph.

    Args:
        query: Search query string
        entity_types: Optional comma-separated list of entity types to filter
        max_results: Maximum number of results to return

    Returns:
        List of matching entities with their documents and relevance
    """
    try:
        config = get_domain_config()

        # Build the graph via unified adapter
        knowledge_graph = get_knowledge_graph()
        adapter = GraphVisualizationAdapter(knowledge_graph, config)
        graph_data = await adapter.build_visualization_data()

        # Parse entity types filter
        entity_type_filter = entity_types.split(",") if entity_types else None

        # Search logic
        query_lower = query.lower()
        matching_nodes = []

        for node in graph_data["nodes"]:
            # Check entity type filter
            if entity_type_filter and node.get("entity_type") not in entity_type_filter:
                continue

            # Search in node attributes
            node_attrs = node.get("attributes", {})
            node_name = node_attrs.get("name", "").lower()

            # Check for match in name
            if query_lower in node_name:
                matching_nodes.append(
                    {
                        "id": node.get("id"),
                        "name": node_attrs.get("name"),
                        "type": node.get("entity_type"),
                        "score": 1.0 if query_lower == node_name else 0.8,
                        "attributes": node_attrs,
                        "file_path": node.get("metadata", {}).get("file_path", ""),
                    }
                )
                continue

            # Check for partial word matches
            query_words = query_lower.split()
            name_words = node_name.split()

            if any(
                q_word in name_word
                for q_word in query_words
                for name_word in name_words
            ):
                matching_nodes.append(
                    {
                        "id": node.get("id"),
                        "name": node_attrs.get("name"),
                        "type": node.get("entity_type"),
                        "score": 0.6,
                        "attributes": node_attrs,
                        "file_path": node.get("metadata", {}).get("file_path", ""),
                    }
                )

        # Sort by score and limit results
        matching_nodes.sort(key=lambda x: x["score"], reverse=True)
        results = matching_nodes[:max_results]

        # Format results for chat tool compatibility
        formatted_results = []
        for result in results:
            formatted_results.append(
                {
                    "path": result["file_path"],
                    "score": result["score"],
                    "title": result["name"],
                    "snippet": f"Type: {result['type']}, Score: {result['score']:.2f}",
                    "entity": result,
                }
            )

        logger.info(
            f"[DOMAIN_GRAPH_SEARCH] Query: '{query}', Found: {len(formatted_results)} results"
        )
        return formatted_results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching domain graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_domain_graph(
    format: str = Query("json", regex="^(json|csv|graphml)$"),
    domain_loader: DomainConfigLoader = Depends(get_domain_config_loader),
) -> JSONResponse:
    """
    Export graph data with domain context.

    Args:
        format: Export format (json, csv, or graphml)

    Returns:
        File download response with graph data
    """
    try:
        config = get_domain_config()

        # Build graph via unified adapter
        knowledge_graph = get_knowledge_graph()
        adapter = GraphVisualizationAdapter(knowledge_graph, config)
        graph_data = await adapter.build_visualization_data(include_semantic_edges=True)

        # Prepare export data
        export_data = {
            "domain_config": config.dict(),
            "nodes": graph_data["nodes"],
            "edges": graph_data["edges"],
            "exported_at": datetime.utcnow().isoformat(),
        }

        # Generate filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"domain_graph_export_{config.id}_{timestamp}.{format}"

        # Return as downloadable file
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/json",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))
