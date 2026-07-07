"""
Ingestion Module

Handles general-purpose content ingestion:
- Accept content from any source (Fireflies, Otter, Slack, email, documents)
- Classify content type via LLM or source hint
- Extract entities, decisions, relationships
- Write to Neo4j knowledge graph
- Async pipeline with phase-by-phase status tracking
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Create the main router for the ingestion module
router = APIRouter()

try:
    from ...routes.ingest import router as ingest_router
    from ...routes.ingest_zapier import router as ingest_zapier_router

    # GitHub webhook ingestion is core (routes/webhook.py is part of the
    # ingestion funnel) and its startup hook starts the shared task queue
    # that POST /api/ingest depends on — it must always load, so it lives
    # here rather than in an optional integrations module.
    from ...routes.webhook import router as webhook_router
    router.include_router(ingest_router)
    router.include_router(ingest_zapier_router)
    router.include_router(webhook_router)
    logger.info("Successfully loaded ingestion module router")
except Exception as e:
    logger.error(f"Failed to load ingestion module router: {e}")
    raise
