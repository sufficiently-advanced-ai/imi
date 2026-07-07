"""
Analysis Module

Handles all AI analysis functionality including:
- Agent tools and operations
- Memory management
- Insights generation
- Analysis services

This module encapsulates all AI/analysis-related routes and services.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Create the main router for the analysis module
router = APIRouter()

# Import and include all analysis-related routes
try:
    # Import existing route modules
    from ...routes.agent_tools import router as agent_tools_router
    from ...routes.analysis import router as analysis_router
    from ...routes.insights import router as insights_router
    from ...routes.memory import router as memory_router

    # Include all routers without prefix (they have their own paths)
    router.include_router(agent_tools_router)
    router.include_router(analysis_router)
    router.include_router(memory_router)
    router.include_router(insights_router)

    logger.info("Successfully loaded analysis module routers")

except Exception as e:
    logger.error(f"Failed to load analysis module routers: {e}")
    raise
