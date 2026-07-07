"""
API routes for entity migration to domain-aware structure.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..core.dependencies import get_domain_config_service
from ..services.entity_migration import EntityMigrationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entity-migration", tags=["entity-migration"])


class MigrationRequest(BaseModel):
    """Request model for entity migration."""

    target_domain_id: str
    dry_run: bool = True
    backup: bool = True
    cleanup: bool = False


class MigrationAnalysisResponse(BaseModel):
    """Response model for migration analysis."""

    legacy_entities: dict[str, int]
    target_domain: str
    target_entity_types: list[str]
    compatibility: dict[str, str]
    total_entities: int
    recommendations: list[str]


class MigrationPlanResponse(BaseModel):
    """Response model for migration plan."""

    created_at: str
    source: str
    target_domain: str
    mappings: dict[str, str]
    attribute_mappings: dict[str, dict[str, str]]
    steps: list[dict[str, Any]]


class MigrationResultResponse(BaseModel):
    """Response model for migration results."""

    started_at: str
    completed_at: str | None
    dry_run: bool
    entities_processed: int
    entities_migrated: int
    entities_skipped: int
    errors: list[str]
    log: list[str]
    backup_path: str | None = None


@router.get("/analyze/{domain_id}", response_model=MigrationAnalysisResponse)
async def analyze_migration(domain_id: str) -> MigrationAnalysisResponse:
    """
    Analyze entity migration requirements for a target domain.

    Args:
        domain_id: Target domain configuration ID

    Returns:
        Migration analysis report
    """
    try:
        # Verify domain exists
        loader = get_domain_config_service()
        domain_config = loader.load_domain(domain_id)
        if not domain_config:
            raise HTTPException(
                status_code=404, detail=f"Domain '{domain_id}' not found"
            )

        # Create migration service
        migration_service = EntityMigrationService(domain_id)

        # Analyze requirements
        analysis = await migration_service.analyze_migration_requirements()

        return MigrationAnalysisResponse(**analysis)

    except Exception as e:
        logger.error(f"Error analyzing migration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan/{domain_id}", response_model=MigrationPlanResponse)
async def create_migration_plan(domain_id: str) -> MigrationPlanResponse:
    """
    Create a detailed migration plan for a target domain.

    Args:
        domain_id: Target domain configuration ID

    Returns:
        Migration plan with mappings and steps
    """
    try:
        # Verify domain exists
        loader = get_domain_config_service()
        domain_config = loader.load_domain(domain_id)
        if not domain_config:
            raise HTTPException(
                status_code=404, detail=f"Domain '{domain_id}' not found"
            )

        # Create migration service
        migration_service = EntityMigrationService(domain_id)

        # Create plan
        plan = await migration_service.create_migration_plan()

        return MigrationPlanResponse(**plan)

    except Exception as e:
        logger.error(f"Error creating migration plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute", response_model=MigrationResultResponse)
async def execute_migration(
    request: MigrationRequest, background_tasks: BackgroundTasks
) -> MigrationResultResponse:
    """
    Execute entity migration to domain-aware structure.

    Args:
        request: Migration configuration

    Returns:
        Migration results
    """
    try:
        # Verify domain exists
        loader = get_domain_config_service()
        domain_config = loader.load_domain(request.target_domain_id)
        if not domain_config:
            raise HTTPException(
                status_code=404, detail=f"Domain '{request.target_domain_id}' not found"
            )

        # Create migration service
        migration_service = EntityMigrationService(request.target_domain_id)

        # Execute migration
        results = await migration_service.execute_migration(
            dry_run=request.dry_run, backup=request.backup, cleanup=request.cleanup
        )

        return MigrationResultResponse(**results)

    except Exception as e:
        logger.error(f"Error executing migration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/{domain_id}")
async def generate_migration_report(domain_id: str) -> dict[str, str]:
    """
    Generate a detailed migration report for a target domain.

    Args:
        domain_id: Target domain configuration ID

    Returns:
        Markdown-formatted migration report
    """
    try:
        # Verify domain exists
        loader = get_domain_config_service()
        domain_config = loader.load_domain(domain_id)
        if not domain_config:
            raise HTTPException(
                status_code=404, detail=f"Domain '{domain_id}' not found"
            )

        # Create migration service
        migration_service = EntityMigrationService(domain_id)

        # Generate report
        report = await migration_service.generate_migration_report()

        return {"domain_id": domain_id, "report": report, "format": "markdown"}

    except Exception as e:
        logger.error(f"Error generating migration report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/domains")
async def list_available_domains() -> dict[str, list[str]]:
    """
    List available domain configurations for migration.

    Returns:
        List of domain IDs
    """
    try:
        loader = get_domain_config_service()
        domains = loader.list_domains()

        return {"domains": domains, "count": len(domains)}

    except Exception as e:
        logger.error(f"Error listing domains: {e}")
        raise HTTPException(status_code=500, detail=str(e))
