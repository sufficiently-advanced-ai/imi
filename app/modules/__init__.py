"""
imi Modules

Modular architecture with bounded contexts for better separation of concerns.
Each module contains routes, services, and models for a specific domain.
"""

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def register_modules(app: FastAPI) -> None:
    """
    Register all module routers with the FastAPI app.

    Args:
        app: FastAPI application instance
    """
    try:
        # Import module routers
        from .analysis import router as analysis_router
        from .core import router as core_router
        from .entities import router as entities_router
        from .ingestion import router as ingestion_router
        from .knowledge import router as knowledge_router

        # Register core routes first (auth, admin, etc.) - these have their own prefixes
        app.include_router(core_router, tags=["core"])

        # Register domain modules
        app.include_router(entities_router, tags=["entities"])
        app.include_router(analysis_router, tags=["analysis"])
        app.include_router(knowledge_router, tags=["knowledge"])
        app.include_router(ingestion_router, prefix="/api", tags=["ingestion"])

        logger.info("Successfully registered module routers")

    except Exception as e:
        logger.error(f"Failed to register module routers: {e}")
        raise
