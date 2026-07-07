"""
Core System Module

Handles core system functionality including:
- Authentication and user management
- System administration
- File operations (upload, diff)
- Metrics and monitoring
- System workflows

This module encapsulates system-level routes and services.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Create the main router for the core module
router = APIRouter()

# Import and include all core system routes
try:
    # Import routers individually with error handling for optional dependencies
    routers_to_include = []

    # Essential authentication router
    try:
        from ...routes.auth import router as auth_router
        routers_to_include.append(("auth", auth_router))
    except Exception as e:
        logger.warning(f"Failed to load auth router: {e}")

    # User management router (may depend on database)
    try:
        from ...routes.users import router as users_router
        routers_to_include.append(("users", users_router))
    except Exception as e:
        logger.warning(f"Failed to load users router (likely missing database dependency): {e}")

    # Core system routers
    core_routes = [
        ("admin", "...routes.admin"),
        ("sse_status", "...routes.sse_status"),
        ("folders", "...routes.folders"),
        ("upload", "...routes.upload"),
        ("diff", "...routes.diff"),
        ("digest", "...routes.digest"),
        ("metrics", "...routes.metrics"),
        ("prompt_templates", "...routes.prompt_templates"),
        ("workflows", "...routes.workflows"),
        ("registry", "...routes.registry"),
        ("health", "...routes.health"),
    ]

    for route_name, route_module in core_routes:
        try:
            if route_module == "...routes.admin":
                from ...routes.admin import router as route_router
            elif route_module == "...routes.sse_status":
                from ...routes.sse_status import router as route_router
            elif route_module == "...routes.folders":
                from ...routes.folders import router as route_router
            elif route_module == "...routes.upload":
                from ...routes.upload import router as route_router
            elif route_module == "...routes.diff":
                from ...routes.diff import router as route_router
            elif route_module == "...routes.digest":
                from ...routes.digest import router as route_router
            elif route_module == "...routes.metrics":
                from ...routes.metrics import router as route_router
            elif route_module == "...routes.prompt_templates":
                from ...routes.prompt_templates import router as route_router
            elif route_module == "...routes.workflows":
                from ...routes.workflows import router as route_router
            elif route_module == "...routes.registry":
                from ...routes.registry import router as route_router
            elif route_module == "...routes.health":
                from ...routes.health import router as route_router

            routers_to_include.append((route_name, route_router))
        except Exception as e:
            logger.warning(f"Failed to load {route_name} router: {e}")

    # Include all successfully loaded routers
    for route_name, route_router in routers_to_include:
        router.include_router(route_router)
        logger.debug(f"Included {route_name} router")

    logger.info(f"Successfully loaded {len(routers_to_include)} core system module routers")

except Exception as e:
    logger.error(f"Failed to load core system module routers: {e}")
    raise
