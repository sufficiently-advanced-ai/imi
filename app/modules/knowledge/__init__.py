"""
Knowledge Module

Handles all knowledge management functionality including:
- Domain graph and visualization
- Domain configuration management
- Domain configuration packages
- Knowledge search and retrieval
- Command processing and chat

This module encapsulates all knowledge-related routes and services.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Create the main router for the knowledge module
router = APIRouter()

# Import and include all knowledge-related routes
try:
    # Import existing route modules
    from ...routes.command import router as command_router
    from ...routes.domain_config import router as domain_config_router
    from ...routes.domain_graph import router as domain_graph_router
    from ...routes.domain_graph_enhancements import router as domain_graph_enhancements_router
    from ...routes.domain_packages import router as domain_packages_router
    from ...routes.streaming_chat import router as streaming_chat_router

    # Include all routers without prefix (they have their own paths)
    router.include_router(domain_graph_router)
    router.include_router(domain_graph_enhancements_router)
    router.include_router(domain_config_router)
    router.include_router(domain_packages_router)
    router.include_router(streaming_chat_router)
    router.include_router(command_router)

    logger.info("Successfully loaded knowledge module routers")

except Exception as e:
    logger.error(f"Failed to load knowledge module routers: {e}")
    raise
