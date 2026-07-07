"""
Entities Module

Handles all entity-related functionality including:
- Entity CRUD operations
- Entity management and search
- Entity enrichment and migration
- Entity webhooks and integration
- Bulk entity operations

This module encapsulates all entity-related routes and services.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Create the main router for the entities module
router = APIRouter()

# Import and include all entity-related routes
try:
    # Import existing route modules
    from ...routes.entity_bulk import router as entity_bulk_router
    from ...routes.entity_crud import router as entity_crud_router
    from ...routes.entity_enrichment import router as entity_enrichment_router
    from ...routes.entity_integration import router as entity_integration_router
    from ...routes.entity_management import router as entity_management_router
    from ...routes.entity_migration import router as entity_migration_router
    from ...routes.entity_profile import router as entity_profile_router
    from ...routes.entity_registry_reset import router as entity_registry_reset_router
    from ...routes.entity_reset import router as entity_reset_router
    from ...routes.entity_search import router as entity_search_router
    from ...routes.entity_suggestions import router as entity_suggestions_router
    from ...routes.entity_webhooks import router as entity_webhooks_router

    # Include all routers without prefix (they have their own paths)
    router.include_router(entity_crud_router)
    router.include_router(entity_management_router)
    router.include_router(entity_search_router)
    router.include_router(entity_bulk_router)
    router.include_router(entity_enrichment_router)
    router.include_router(entity_integration_router)
    router.include_router(entity_migration_router)
    router.include_router(entity_profile_router)
    router.include_router(entity_registry_reset_router)
    router.include_router(entity_reset_router)
    router.include_router(entity_suggestions_router)
    router.include_router(entity_webhooks_router)

    logger.info("Successfully loaded entities module routers")

except Exception as e:
    logger.error(f"Failed to load entities module routers: {e}")
    raise
