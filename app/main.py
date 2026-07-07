import asyncio
import json
import os
import re
import sys
from typing import Any

from anthropic import APIConnectionError, APIStatusError, RateLimitError
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings

# Initialize metrics FIRST before any imports that might use them
# Import setup_metrics but don't call it yet - will be called in startup event
from .metrics import setup_metrics

# Import production telemetry manager (Issue #526)
from .services.telemetry_manager import initialize_telemetry

# OpenTelemetry instrumentation
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
# Router for routes defined directly in this module (health/knowledge/explorer/query).
# _configure() includes this router so the routes appear on the assembled app.
from fastapi import APIRouter as _APIRouter

from .git_ops import git_ops
from .models import (
    KnowledgeResponse,
    MetadataResponse,
    QueryRequest,
    QueryResponse,
)

# Import module registration system
from .modules import register_modules
from .services.auth import get_current_user
from .services.metadata import analyze_metadata
from .services.prompts import format_prompt, load_prompt_template

_main_router = _APIRouter()


def _configure(app: "FastAPI") -> None:
    """Wire all middleware, routers, event handlers, and mounts onto *app*.

    Preserves the exact statement order from the original module-level wiring.
    Called by create_app(); also called on the module-level singleton.
    """
    # Add CORS middleware — use env-based origins instead of wildcard
    import os as _os
    _cors_origins_str = _os.environ.get("CORS_ALLOWED_ORIGINS", "")
    _cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()] if _cors_origins_str else []
    if "*" in _cors_origins:
        import logging as _logging
        _logging.getLogger(__name__).warning("CORS_ALLOWED_ORIGINS contains '*' — stripping wildcard for security")
        _cors_origins = [o for o in _cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=bool(_cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add production hardening middleware (Issue #398)
    # Note: Middleware order matters - they execute in reverse order of addition
    from .core.middleware.performance_monitor import PerformanceMonitoringMiddleware
    from .core.middleware.rate_limiter import RateLimitingMiddleware
    from .core.middleware.request_validator import RequestValidationMiddleware

    # Performance monitoring (executes last, measures total time)
    app.add_middleware(PerformanceMonitoringMiddleware)

    # Request validation (validates input before processing)
    app.add_middleware(RequestValidationMiddleware)

    # Rate limiting (prevents abuse)
    app.add_middleware(RateLimitingMiddleware)

    # Add HTTP metrics middleware - will be added after metrics initialization in startup

    # Add tenant-context middleware (multi-tenancy core primitive — Phase 4.1).
    # Registered BEFORE AuthenticationMiddleware so it runs AFTER auth inbound
    # (Starlette executes middleware in reverse registration order), giving it
    # access to request.state.user for resolvers added by hosted (Phase 4.5).
    # configure_tenancy also installs the single-tenant default backend bundle into
    # the container factory. This call is the seam absorbed by the create_app() factory
    # (Phase 1c).
    from .core.tenancy.wiring import configure_tenancy  # noqa: E402

    configure_tenancy(app)

    # Add authentication middleware
    from .middleware.auth import AuthenticationMiddleware

    app.add_middleware(AuthenticationMiddleware)

    # HTTP request metrics (http_request_duration_seconds, http_requests_total,
    # http_requests_in_progress). Registered here at construction — middleware
    # cannot be added once the app has started — and added last so it is the
    # outermost layer, measuring total request time. Its instruments bind
    # lazily on the first request, after startup has initialized telemetry.
    from .middleware.metrics import HTTPMetricsMiddleware

    app.add_middleware(HTTPMetricsMiddleware)

    # Instrument FastAPI with OpenTelemetry if available
    if OTEL_AVAILABLE:
        try:
            FastAPIInstrumentor.instrument_app(app, excluded_urls="health$,healthz$,metrics$,favicon.ico$")
        except Exception as e:
            print(f"Warning: Failed to instrument FastAPI with OpenTelemetry: {e}")

    # Register all modules using the new modular system
    register_modules(app)

    # Removed temporary direct registration - using module system only

    # Register production monitoring routes (Issue #398)
    from .routes.health import router as health_router
    from .routes.production_monitoring import router as production_monitoring_router
    from .routes.telemetry_monitoring import router as telemetry_monitoring_router

    app.include_router(production_monitoring_router, tags=["monitoring"])
    app.include_router(health_router, tags=["health"])
    app.include_router(telemetry_monitoring_router, tags=["telemetry"])

    # Register knowledge explorer routes
    from .routes.knowledge_explorer import router as knowledge_explorer_router

    app.include_router(knowledge_explorer_router, tags=["knowledge-explorer"])

    # Register signal feed routes
    from .routes.signal_feed import router as signal_feed_router
    from .routes.signal_mutations import router as signal_mutations_router
    from .routes.type_registry import router as type_registry_router

    app.include_router(signal_feed_router, tags=["signals"])
    app.include_router(signal_mutations_router, tags=["signals"])
    app.include_router(type_registry_router, tags=["type-registry"])

    # Register decisions routes (Issue #954)
    from .routes.decisions import router as decisions_router

    app.include_router(decisions_router, tags=["decisions"])

    # Register captures routes (OB1 absorption Phase 1 — G4 capture loop)
    from .routes.captures import router as captures_router

    app.include_router(captures_router, tags=["captures"])

    # Register agent-memory + unified review queue routes (OB1 absorption Phase 2)
    from .routes.agent_memory import router as agent_memory_router
    from .routes.memories_review import router as memories_review_router

    app.include_router(agent_memory_router, tags=["agent-memory"])
    app.include_router(memories_review_router, tags=["memories"])

    # Register judge routes (OB1 absorption Phase 4 — judge extender)
    from .routes.judge import router as judge_router

    app.include_router(judge_router, tags=["judge"])

    # Register supersession candidates routes (R2.4 + R4.4)
    from .routes.supersession import router as supersession_router

    app.include_router(supersession_router, tags=["supersession"])

    # Register conflict candidates routes (Sprint 4, R3.5)
    from .routes.conflicts import router as conflicts_router

    app.include_router(conflicts_router, tags=["conflicts"])

    # Register profile import routes (Issue #825)
    from .routes.profile_import import router as profile_import_router

    app.include_router(profile_import_router, tags=["profile-import"])

    # Register demo routes conditionally (Epic #783)
    try:
        from .routes.demo import get_demo_router
        demo_router = get_demo_router()
        if demo_router:
            app.include_router(demo_router, tags=["demo"])
            from .routes.demo import preload_demo_entities
            preload_demo_entities(app)
            from .routes.demo import ChatConversation, list_fixtures, load_fixture
            app.state.demo_chat_conversations = {}
            for fixture_name in list_fixtures("chat"):
                try:
                    fixture_data = load_fixture("chat", fixture_name)
                    if fixture_data:
                        conv = ChatConversation(**fixture_data)
                        normalized_query = conv.query.lower().strip()
                        app.state.demo_chat_conversations[normalized_query] = conv.model_dump()
                except Exception as e:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(f"Failed to preload chat fixture {fixture_name}: {e}")
            import logging as _logging
            _logging.getLogger(__name__).info(f"Pre-loaded {len(app.state.demo_chat_conversations)} demo chat conversations at startup")
    except ImportError:
        pass  # demo route not available in community edition

    # Register routes defined directly in this module (health/knowledge/explorer/query)
    app.include_router(_main_router)

    # Register event handlers
    app.add_event_handler("startup", startup_event)
    app.add_event_handler("shutdown", shutdown_event)

    # Mount MCP server for Claude Code graph access (SSE transport)
    from .routes.mcp_server import starlette_app as mcp_app
    app.mount("/api/mcp", mcp_app)

    # Mount static files for the Next.js UI at the root path
    # With output: 'export', Next.js builds to the 'out' directory
    ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "out")
    if os.path.exists(ui_dir):
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

# Initialize shared services
from .services.claude_client import ClaudeClient  # noqa: E402 — after conditional UI mount
from .services.file_cache import FileCache  # noqa: E402

claude_client = ClaudeClient()
file_cache = FileCache()


async def startup_event():
    """Initialize git repository and optimization systems on startup."""

    # Initialize lifecycle manager first (Issue #398)
    from .core.lifecycle import get_lifecycle_manager
    lifecycle_manager = get_lifecycle_manager()

    await lifecycle_manager.startup()
    sys.stderr.write("Lifecycle manager initialized\n")

    # Initialize production telemetry system (Issue #526)
    initialize_telemetry()
    sys.stderr.write("Production telemetry initialized\n")

    # Initialize legacy metrics system for backward compatibility
    setup_metrics()
    sys.stderr.write("Legacy metrics initialized\n")

    # HTTPMetricsMiddleware is registered at construction in _configure(); its
    # instruments bind lazily on the first request, after this point.

    # Initialize Neo4j graph database
    try:
        from .neo4j_client import initialize_neo4j
        await initialize_neo4j()
        sys.stderr.write("Neo4j client initialized\n")

        # Initialize Neo4j schema from active domain config
        from .services.graph.neo4j_schema import initialize_schema_from_domain
        await initialize_schema_from_domain()
        sys.stderr.write("Neo4j schema initialized from domain config\n")

        # Ensure the type registry's uniqueness constraint is in place before
        # any mutation triggers a record_usage MERGE (#877).
        try:
            from .services.graph.type_registry import get_type_registry_service
            _tr = get_type_registry_service()
            if _tr is not None:
                await _tr.ensure_constraints()
                sys.stderr.write("Type registry constraints ensured\n")
        except Exception as tr_err:
            sys.stderr.write(
                f"Warning: Type registry constraint setup failed (non-fatal): {tr_err}\n"
            )
    except Exception as e:
        sys.stderr.write(f"Warning: Neo4j initialization failed: {e}\n")
        sys.stderr.write("Application starting without Neo4j — graph features degraded\n")

    try:
        # Initialize git repository
        await git_ops.initialize()
        sys.stderr.write("Git repository initialized successfully\n")

        # Ensure repo directory exists for persistent storage
        os.makedirs("/app/repo", exist_ok=True)
        sys.stderr.write("Repo directory ensured for persistent storage\n")

        # Load domain configuration (loaded at import time via active_domain.py)
        from .core.domain_config import get_domain_config
        from .services.entity_registry import EntityRegistry

        try:
            config = get_domain_config()
            EntityRegistry().register_domain(config)
            # Ensure Neo4j schema is created for the active domain
            try:
                from .services.graph.neo4j_schema import initialize_schema_from_domain as init_schema
                await init_schema(config)
            except Exception as schema_err:
                sys.stderr.write(f"Warning: Neo4j schema re-init after domain load failed: {schema_err}\n")
            sys.stderr.write(
                f"Domain '{config.id}' loaded and registered\n"
            )

            # Initialize Semantica knowledge layer
            try:
                from .core.dependencies import initialize_semantica
                sk = initialize_semantica()
                if sk:
                    sys.stderr.write("Semantica knowledge layer initialized\n")
                else:
                    sys.stderr.write("Warning: Semantica not available — using legacy knowledge graph\n")
            except Exception as sem_err:
                sys.stderr.write(f"Warning: Semantica initialization failed: {sem_err}\n")

            # Rebuild knowledge graph from scratch on startup (stateless deploy)
            if settings.NEO4J_REBUILD_ON_STARTUP:
                # 1) Always run the legacy Neo4j graph build synchronously.
                #    This populates .nodes/.edges caches that the visualization
                #    adapter reads. It's fast (~5-15s, no embeddings).
                try:
                    from .services.graph import get_knowledge_graph
                    kg = get_knowledge_graph()
                    if kg:
                        sys.stderr.write("Rebuilding knowledge graph (clean=False)...\n")
                        stats = await kg.build_graph(force_rebuild=True, clean=False)
                        sys.stderr.write(
                            f"Knowledge graph rebuilt: {stats.get('total_nodes', stats.get('nodes', 0))} nodes, "
                            f"{stats.get('total_edges', stats.get('edges', 0))} edges\n"
                        )
                    else:
                        sys.stderr.write("Warning: Knowledge graph not available for startup rebuild\n")
                except Exception as rebuild_err:
                    sys.stderr.write(f"Warning: Knowledge graph rebuild failed: {rebuild_err}\n")

                # 2) If Semantica is available, schedule its index build as an
                #    async task. This indexes entities into the vector store for
                #    hybrid search. Runs on the same event loop, not a separate thread.
                try:
                    from .services.graph.factory import get_semantica_knowledge
                    _sk = get_semantica_knowledge()
                    if _sk:
                        sys.stderr.write("Scheduling Semantica vector index build as async task...\n")

                        async def _bg_semantica_build():
                            try:
                                stats = await _sk.build_graph(force_rebuild=True, clean=False)
                                sys.stderr.write(
                                    f"Semantica build complete (background): "
                                    f"{stats.get('nodes', 0)} nodes, "
                                    f"{stats.get('edges', 0)} edges\n"
                                )
                            except Exception as e:
                                sys.stderr.write(f"Semantica background build failed: {e}\n")

                        asyncio.create_task(_bg_semantica_build())
                except Exception as sem_build_err:
                    sys.stderr.write(f"Warning: Semantica background build setup failed: {sem_build_err}\n")
            else:
                # Pre-warm the in-memory graph cache from Neo4j on startup.
                # Without this, the first user request to anything that calls
                # build_graph() (e.g. /api/entities/{id}/profile) pays the
                # smart-sync cost (~4s) on the cold path. Pre-warming moves
                # that cost out of the user's first interaction.
                try:
                    from .services.graph import get_knowledge_graph
                    kg = get_knowledge_graph()
                    if kg and hasattr(kg, "build_graph"):
                        sys.stderr.write("Pre-warming graph cache from Neo4j...\n")
                        stats = await kg.build_graph()
                        sys.stderr.write(
                            f"Graph cache pre-warmed: {stats.get('total_nodes', stats.get('nodes', 0))} nodes, "
                            f"{stats.get('total_edges', stats.get('edges', 0))} edges\n"
                        )
                except Exception as warm_err:
                    sys.stderr.write(f"Warning: Graph pre-warm failed: {warm_err}\n")

        except Exception as e:
            sys.stderr.write(f"Warning: Could not load default domain: {e}\n")

        # Import and initialize optimization services
        from .services.dependency_tracker import dependency_tracker

        # Initialize dependency tracker in background task
        asyncio.create_task(dependency_tracker.initialize())
        sys.stderr.write("Dependency tracker initialization started\n")

        # Warm up file cache with most frequently used files
        sys.stderr.write("File cache initialized\n")

        # Initialize database - Issue #360
        try:
            from .database import initialize_database

            await initialize_database()
            sys.stderr.write("Database initialized successfully\n")
        except Exception as e:
            sys.stderr.write(f"Warning: Database initialization failed: {str(e)}\n")
            # Continue without database - some features may be degraded
    except Exception as e:
        sys.stderr.write(f"Warning: Startup initialization failed: {str(e)}\n")
        sys.stderr.write("Application starting in degraded state\n")
        # Don't re-raise - allow app to start without full functionality


async def shutdown_event():
    """Clean up resources on shutdown."""

    # Trigger lifecycle manager shutdown (Issue #398)
    # This will run all registered shutdown handlers in priority order
    from .core.lifecycle import get_lifecycle_manager
    lifecycle_manager = get_lifecycle_manager()
    await lifecycle_manager.shutdown()
    sys.stderr.write("Lifecycle manager shutdown complete\n")

    # Additional cleanup for resources not managed by lifecycle manager
    # (Legacy code - gradually migrate to lifecycle handlers)

    # Close Neo4j connection
    try:
        from .neo4j_client import close_neo4j
        await close_neo4j()
        sys.stderr.write("Neo4j connection closed\n")
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to close Neo4j connection: {e}\n")

    # Close database connections - Issue #360
    try:
        from .database import close_database_connections
        await close_database_connections()
        sys.stderr.write("Database connections closed\n")
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to close database connections: {e}\n")


# Legacy health endpoint - removed in favor of production health checks (Issue #398)
# See /health and /health/ready endpoints from routes/health.py


class Neo4jHealthItem(BaseModel):
    status: str
    healthy: bool
    error: str | None = None


class Neo4jHealthResponse(BaseModel):
    neo4j: Neo4jHealthItem


@_main_router.get("/health/neo4j", response_model=Neo4jHealthResponse)
async def neo4j_health_check() -> Neo4jHealthResponse:
    """Check Neo4j health."""
    try:
        from .neo4j_client import get_neo4j_client
        client = get_neo4j_client()
        result = await client.health_check()
        return Neo4jHealthResponse(neo4j=Neo4jHealthItem(**{
            "status": result.get("status", "unknown"),
            "healthy": result.get("healthy", False),
            "error": result.get("error"),
        }))
    except Exception as e:
        return Neo4jHealthResponse(neo4j=Neo4jHealthItem(
            status="unavailable", healthy=False, error=str(e)
        ))


@_main_router.get("/health/database")
async def database_health_check() -> dict:
    """Check database health."""
    try:
        from .database import get_database_config, get_database_engine

        config = get_database_config()
        engine = get_database_engine(config)
        # Test database connection
        async with engine.begin() as conn:
            # Simple test query
            from sqlalchemy import text

            result = await conn.execute(text("SELECT 1"))
            await result.fetchone()
        return {
            "database": {
                "status": "healthy",
                "connection": "connected",
                "database_path": config.database_path,
            }
        }
    except Exception as e:
        return {
            "database": {"status": "unhealthy", "connection": "failed", "error": str(e)}
        }


@_main_router.get("/api/knowledge", response_model=KnowledgeResponse)
async def get_knowledge(
    paths: list[str] | None = Query(None),
    page: int = 0,
    limit: int = 100,
    sort_by: str = "path",
    sort_order: str = "asc",
    include_content: bool = True,
    user: dict = Depends(get_current_user),  # Add authentication requirement
):
    """
    Get content of markdown files from the repository with pagination.

    Args:
        paths: Optional list of specific file paths to read
        page: Page number starting from 0
        limit: Number of items per page (max 100)
        sort_by: Field to sort by ('path', 'modified_at', 'created_at', 'size')
        sort_order: Sort direction ('asc' or 'desc')
        include_content: Whether to include file content or just metadata
    """
    # Log user action with detailed context
    import logging


    logger = logging.getLogger(__name__)
    logger.info(f"KNOWLEDGE_REQUEST: User {user.get('email', 'unknown')} requesting knowledge files - limit={limit}, include_content={include_content}")

    try:
        # Use file cache for specific file requests to improve performance
        from .services.file_cache import file_cache

        # Handle invalid pagination params
        page = max(0, page)
        limit = min(max(1, limit), 100)  # Clamp between 1 and 100

        # Single file request - use file cache for better performance
        if paths and len(paths) == 1:
            file = await file_cache.get_file(paths[0])
            if file:
                # If include_content is False, remove content
                if not include_content:
                    file.content = ""
                return KnowledgeResponse(files=[file], total=1, page=0, limit=1)
            # If not in cache, fall through to normal handling

        # Multiple files or fallback case
        files = await git_ops.read_markdown_files(paths)

        # Sort files based on parameters
        if sort_by == "path":
            files.sort(key=lambda x: x.path, reverse=(sort_order == "desc"))
        elif sort_by == "modified_at":
            files.sort(key=lambda x: x.modified_at, reverse=(sort_order == "desc"))
        elif sort_by == "created_at":
            files.sort(key=lambda x: x.created_at, reverse=(sort_order == "desc"))
        elif sort_by == "size":
            files.sort(key=lambda x: len(x.content), reverse=(sort_order == "desc"))

        # Calculate total before pagination for metadata
        total_files = len(files)

        # Apply pagination
        start_idx = page * limit
        end_idx = start_idx + limit
        paginated_files = files[start_idx:end_idx]

        # If include_content is False, remove content to reduce payload size
        if not include_content:
            for file in paginated_files:
                file.content = ""

        return KnowledgeResponse(
            files=paginated_files, total=total_files, page=page, limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Explorer API Models
class EntityTypeSummary(BaseModel):
    """Summary of entities for a specific type."""
    count: int
    recent: list[dict[str, Any]] = Field(default_factory=list)


class ExplorerOverview(BaseModel):
    """Overview of all content in the knowledge base."""
    entities: dict[str, EntityTypeSummary]
    meetings: dict[str, Any]
    documents: dict[str, Any]


class ContentItem(BaseModel):
    """Single content item for explorer view."""
    path: str
    title: str
    type: str
    last_modified: str
    metadata: dict[str, Any] | None = None
    snippet: str | None = None


class TypeContents(BaseModel):
    """Contents for a specific type."""
    type: str
    items: list[ContentItem]
    total: int


@_main_router.get("/api/explorer/overview", response_model=ExplorerOverview)
async def get_explorer_overview(user: dict = Depends(get_current_user)):
    """
    Get overview of all content types in the knowledge base.

    Returns counts and recent items for each entity type,
    meetings, and documents.
    """
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing explorer overview")

    try:
        from .services.frontmatter import FrontmatterService

        # Get all markdown files
        all_files = await git_ops.read_markdown_files()

        # Initialize counters
        entity_types = ["person", "project", "team", "account", "organization"]
        entities_data = {et: {"count": 0, "recent": []} for et in entity_types}
        meetings_count = 0
        documents_count = 0
        meetings_recent = []
        documents_recent = []

        # Process files
        for file in all_files:
            file_path = file.path

            # Extract title from frontmatter or filename
            title = Path(file_path).stem
            try:
                if file.content:
                    frontmatter_data = FrontmatterService.extract_all(file.content)
                    if frontmatter_data and "title" in frontmatter_data:
                        title = frontmatter_data["title"]
            except Exception:
                pass  # Use filename if frontmatter extraction fails

            # Check if it's an entity
            if file_path.startswith("entities/"):
                # Extract entity type from path: entities/{type}/{file}
                parts = file_path.split("/")
                if len(parts) >= 3:
                    entity_type = parts[1]
                    if entity_type in entity_types:
                        entities_data[entity_type]["count"] += 1
                        # Add to recent list (limit to 5)
                        if len(entities_data[entity_type]["recent"]) < 5:
                            entities_data[entity_type]["recent"].append({
                                "path": file_path,
                                "title": title,
                                "last_modified": file.modified_at.isoformat()
                            })
            # Check if it's a meeting
            elif file_path.startswith("meetings/"):
                meetings_count += 1
                if len(meetings_recent) < 5:
                    meetings_recent.append({
                        "path": file_path,
                        "title": title,
                        "last_modified": file.modified_at.isoformat()
                    })
            # Check if it's a document
            elif file_path.startswith("documents/"):
                documents_count += 1
                if len(documents_recent) < 5:
                    documents_recent.append({
                        "path": file_path,
                        "title": title,
                        "last_modified": file.modified_at.isoformat()
                    })

        # Convert to EntityTypeSummary objects
        entities_response = {
            et: EntityTypeSummary(
                count=data["count"],
                recent=data["recent"]
            )
            for et, data in entities_data.items()
        }

        return ExplorerOverview(
            entities=entities_response,
            meetings={
                "count": meetings_count,
                "path": "meetings/",
                "recent": meetings_recent
            },
            documents={
                "count": documents_count,
                "path": "documents/",
                "recent": documents_recent
            }
        )

    except Exception as e:
        logger.error(f"Error in explorer overview: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@_main_router.get("/api/explorer/type/{content_type}", response_model=TypeContents)
async def get_explorer_type_contents(
    content_type: str,
    sort_by: str = Query("recent", regex="^(name|recent|oldest)$"),
    user: dict = Depends(get_current_user)
):
    """
    Get all content for a specific type.

    Args:
        content_type: Type to filter by (person, project, team, account, organization, meetings, documents)
        sort_by: Sort order - name, recent, or oldest
    """
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing type: {content_type}")

    try:
        from .services.frontmatter import FrontmatterService

        # Get all markdown files
        all_files = await git_ops.read_markdown_files()

        # Filter by type
        filtered_items = []
        entity_types = ["person", "project", "team", "account", "organization"]

        for file in all_files:
            file_path = file.path
            matches = False
            type_label = content_type

            # Match entity types
            if content_type in entity_types:
                if file_path.startswith(f"entities/{content_type}/"):
                    matches = True
            # Match meetings
            elif content_type == "meetings":
                if file_path.startswith("meetings/"):
                    matches = True
                    type_label = "meeting"
            # Match documents
            elif content_type == "documents":
                if file_path.startswith("documents/"):
                    matches = True
                    type_label = "document"

            if matches:
                # Extract title and metadata from frontmatter
                title = Path(file_path).stem
                metadata_dict = None
                try:
                    if file.content:
                        frontmatter_data = FrontmatterService.extract_all(file.content)
                        if frontmatter_data:
                            metadata_dict = frontmatter_data
                            if "title" in frontmatter_data:
                                title = frontmatter_data["title"]
                except Exception:
                    pass

                # Extract snippet from content (first 150 chars)
                snippet = None
                if file.content:
                    # Remove frontmatter if present
                    content_lines = file.content.split("\n")
                    content_start = 0
                    if content_lines and content_lines[0].strip() == "---":
                        # Find end of frontmatter
                        for i in range(1, len(content_lines)):
                            if content_lines[i].strip() == "---":
                                content_start = i + 1
                                break
                    main_content = "\n".join(content_lines[content_start:]).strip()
                    snippet = main_content[:150] + "..." if len(main_content) > 150 else main_content

                filtered_items.append(ContentItem(
                    path=file_path,
                    title=title,
                    type=type_label,
                    last_modified=file.modified_at.isoformat(),
                    metadata=metadata_dict,
                    snippet=snippet
                ))

        # Sort items
        if sort_by == "name":
            filtered_items.sort(key=lambda x: x.title.lower())
        elif sort_by == "recent":
            filtered_items.sort(key=lambda x: x.last_modified, reverse=True)
        elif sort_by == "oldest":
            filtered_items.sort(key=lambda x: x.last_modified)

        return TypeContents(
            type=content_type,
            items=filtered_items,
            total=len(filtered_items)
        )

    except Exception as e:
        logger.error(f"Error getting type contents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@_main_router.get("/api/documents/{path}/metadata", response_model=MetadataResponse)
async def get_document_metadata(path: str, user: dict = Depends(get_current_user)):
    """Get metadata for a specific document.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} accessing document metadata for: {path}"
    )

    metadata = await git_ops.get_document_metadata(path)
    if not metadata:
        raise HTTPException(status_code=404, detail="Metadata not found")
    return MetadataResponse(path=path, metadata=metadata)


class BatchMetadataRequest(BaseModel):
    paths: list[str]


class BatchMetadataResponse(BaseModel):
    items: list[MetadataResponse]
    missing: list[str] = Field(default_factory=list)


@_main_router.post("/api/documents/metadata/batch", response_model=BatchMetadataResponse)
async def batch_document_metadata(
    request: BatchMetadataRequest, user: dict = Depends(get_current_user)
):
    """Get metadata for multiple documents in a single request.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} accessing batch metadata for {len(request.paths)} documents"
    )

    results = []
    missing = []

    # Use file cache for better performance

    for path in request.paths:
        # Try to get from cache first
        metadata = await git_ops.get_document_metadata(path)
        if metadata:
            results.append(MetadataResponse(path=path, metadata=metadata))
        else:
            missing.append(path)

    return BatchMetadataResponse(items=results, missing=missing)


@_main_router.post("/api/documents/{path}/metadata")
async def update_document_metadata(
    path: str, metadata: dict[str, Any], user: dict = Depends(get_current_user)
):
    """Update metadata for a specific document.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} updating metadata for: {path}")

    success = await git_ops.update_document_metadata(path, metadata)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update metadata")
    return {"status": "success"}


@_main_router.post("/api/metadata/{path}", response_model=MetadataResponse)
async def metadata_endpoint(
    path: str, request: Request, user: dict = Depends(get_current_user)
):
    """Wrapper for analyze_metadata to maintain proper API routing.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} analyzing metadata for: {path}")

    return await analyze_metadata(path, request)


@_main_router.post("/api/query", response_model=QueryResponse)
async def query_knowledge(
    request: QueryRequest, user: dict = Depends(get_current_user)
):
    """Query the knowledge base using Claude.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} querying knowledge base: {request.question[:100]}..."
    )

    from app.agents.chat import ChatAgent

    # Check if we should use ChatAgent (when prompt_type is 'search' and no manual context)
    use_chat_agent = request.prompt_type == "search" and not request.context_files

    if use_chat_agent:
        try:
            # Create ChatAgent instance
            agent = ChatAgent()

            # Process query
            result = await agent.process_query(
                query=request.question, manual_context=request.context_files
            )

            # Check for errors
            if "error" in result:
                raise HTTPException(status_code=500, detail=result["error"])

            # Return response in expected format
            return QueryResponse(
                answer=result["answer"],
                model=settings.CLAUDE_SONNET_MODEL,
                prompt_tokens=0,  # Would need to track from actual API calls
                completion_tokens=0,
                confidence="high" if result.get("cited_documents") else "medium",
                sources=result.get("cited_documents", []),
                # New fields for enhanced API
                response=result["answer"],  # Alias for backward compatibility
                context_used=result.get("context_files", []),
                tool_calls=result.get("tool_calls", []),
            )

        except Exception:
            # Fall through to classic implementation
            pass

    # Classic implementation (backward compatibility)
    try:
        # Get relevant files
        files = await git_ops.read_markdown_files(request.context_files)
        if not files:
            raise HTTPException(status_code=400, detail="No markdown files found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File error: {str(e)}")

    try:
        # Load and format prompt template
        template = load_prompt_template(request.prompt_type)
        prompt = format_prompt(template, files, request.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prompt error: {str(e)}")

    try:
        # Query Claude through the shared client (async, records metrics
        # under operation="query")
        try:
            message = await claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_SONNET_MODEL,
                max_tokens=1024,
                operation="query",
            )

            # Get the response text
            response_text = (
                message.content[0].text if message and message.content else ""
            )

            # Default values
            answer = response_text
            confidence = None
            sources = None

            # Extract JSON object from response (handles both pure JSON and mixed text/JSON)
            # Look for the last JSON object in the response (in case there's explanatory text)
            json_matches = re.findall(
                r'\{[^{}]*"answer"[^{}]*"confidence"[^{}]*"sources"[^{}]*\}',
                response_text,
                re.DOTALL,
            )
            if not json_matches:
                # Try simpler pattern if the full pattern doesn't match
                json_matches = re.findall(
                    r'\{[^{}]*"answer"[^{}]*\}', response_text, re.DOTALL
                )

            if json_matches:
                # Use the last JSON match (most likely to be the actual response)
                json_str = json_matches[-1]
                try:
                    # Clean up the JSON string (remove any nested braces that might break parsing)
                    # This regex finds the outermost complete JSON object
                    clean_json_match = re.search(
                        r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", json_str, re.DOTALL
                    )
                    if clean_json_match:
                        json_str = clean_json_match.group(0)

                    parsed_response = json.loads(json_str)
                    if (
                        isinstance(parsed_response, dict)
                        and "answer" in parsed_response
                    ):
                        # Always use the JSON data when available
                        answer = parsed_response.get("answer", "")
                        confidence = parsed_response.get("confidence")
                        sources = parsed_response.get("sources", [])
                        if sources and not isinstance(sources, list):
                            sources = [sources]
                except json.JSONDecodeError as e:
                    # If parsing fails, log it and use the raw response
                    sys.stderr.write(
                        f"Failed to parse JSON response: {e}\nJSON string: {json_str[:200]}...\n"
                    )
                    pass

            return QueryResponse(
                answer=answer,
                model=settings.CLAUDE_SONNET_MODEL,
                prompt_tokens=message.usage.input_tokens,
                completion_tokens=message.usage.output_tokens,
                confidence=confidence,
                sources=sources,
                # New fields for API compatibility
                response=answer,  # Alias for backward compatibility
                context_used=request.context_files,  # Files that were actually used
                tool_calls=[],  # No tool calls in classic mode
            )

        except APIConnectionError as e:
            sys.stderr.write(f"Connection error: {str(e)}\n")
            raise HTTPException(
                status_code=503, detail="Failed to connect to Anthropic API"
            )
        except RateLimitError:
            sys.stderr.write("Rate limit exceeded\n")
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except APIStatusError as e:
            sys.stderr.write(f"API error: {str(e)}\n")
            raise HTTPException(status_code=e.status_code, detail=str(e))

    except Exception as e:
        import traceback

        sys.stderr.write("Full exception details:\n")
        sys.stderr.write(f"{traceback.format_exc()}\n")
        sys.stderr.flush()
        raise HTTPException(status_code=500, detail=str(e))


def create_app(*, extra_routers: list | None = None) -> "FastAPI":
    """Assemble the application (extension seam).

    Downstream deployments compose the app through this factory and pass
    their own routers via ``extra_routers`` — the core never imports them.
    Defaults reproduce today's app exactly.
    """
    _app = FastAPI(title="Git-Powered Knowledge API")
    _configure(_app)
    for r in extra_routers or []:
        _app.include_router(r)
    return _app


# Module-level singleton — `uvicorn app.main:app` and `from app.main import app` keep working.
app = create_app()
