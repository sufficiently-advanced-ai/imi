"""Bulk Operations API routes - Issue #60"""

import asyncio
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from ..domain.entities.services import get_entity_repository
from ..models import (
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    BulkMergeOperation,
    BulkMergeResponse,
    BulkValidationRequest,
    BulkValidationResponse,
)
from ..services.enrichment_job_manager import get_job_manager
from ..services.entity_enrichment import get_entity_enrichment_service
from ..services.entity_webhook_service import get_webhook_service

router = APIRouter(prefix="/api/entities/bulk", tags=["entity-bulk"])


class BulkMergeRequest(BaseModel):
    """Request for bulk merge operations"""

    operations: list[BulkMergeOperation]
    transaction_mode: bool = False


async def _validate_entity(entity_id: str, rules: list[str]) -> dict[str, Any]:
    """Validate a single entity against rules"""
    registry = get_entity_repository()
    entity = registry.get_canonical_entity(entity_id)

    if not entity:
        return {
            "entity_id": entity_id,
            "valid": False,
            "issues": [
                {
                    "field": "entity",
                    "issue_type": "not_found",
                    "message": "Entity not found",
                }
            ],
            "quality_score": 0.0,
        }

    issues = []
    quality_score = 1.0

    # Completeness check
    if "completeness" in rules:
        if hasattr(entity, "email") and not entity.email:
            issues.append(
                {
                    "field": "email",
                    "issue_type": "missing",
                    "message": "Email is missing",
                }
            )
            quality_score -= 0.1

        if hasattr(entity, "titles") and len(entity.titles) == 0:
            issues.append(
                {
                    "field": "titles",
                    "issue_type": "empty",
                    "message": "No titles specified",
                }
            )
            quality_score -= 0.1

    # Consistency check
    if "consistency" in rules:
        # Check for inconsistent data patterns
        if (
            hasattr(entity, "canonical_name")
            and entity.canonical_name in entity.aliases
        ):
            issues.append(
                {
                    "field": "aliases",
                    "issue_type": "inconsistent",
                    "message": "Canonical name appears in aliases",
                }
            )
            quality_score -= 0.15

    # Uniqueness check
    if "uniqueness" in rules:
        # Check if aliases are unique across entities
        for alias in entity.aliases:
            other_entity = registry.get_canonical_entity(alias)
            if other_entity and other_entity.id != entity.id:
                issues.append(
                    {
                        "field": "aliases",
                        "issue_type": "duplicate",
                        "message": f"Alias '{alias}' conflicts with entity {other_entity.id}",
                    }
                )
                quality_score -= 0.2

    return {
        "entity_id": entity_id,
        "valid": len(issues) == 0,
        "issues": issues,
        "quality_score": max(quality_score, 0.0),
    }


@router.post("/merge", response_model=BulkMergeResponse)
async def bulk_merge_entities(request: BulkMergeRequest = Body(...)):
    """Perform multiple entity merges in bulk"""
    if len(request.operations) == 0:
        raise HTTPException(status_code=422, detail="No merge operations provided")

    if len(request.operations) > 100:
        raise HTTPException(status_code=422, detail="Too many operations (max 100)")

    registry = get_entity_repository()
    results = []
    successful = 0
    failed = 0

    # If transaction mode, validate all operations first
    if request.transaction_mode:
        for op in request.operations:
            source = registry.get_canonical_entity(op.source_entity_id)
            target = registry.get_canonical_entity(op.target_entity_id)

            if not source or not target:
                # Rollback - don't perform any merges
                return BulkMergeResponse(
                    total_operations=len(request.operations),
                    successful=0,
                    failed=len(request.operations),
                    results=[
                        {
                            "source_entity_id": op.source_entity_id,
                            "target_entity_id": op.target_entity_id,
                            "success": False,
                            "error": "Transaction failed: One or more entities not found",
                        }
                        for op in request.operations
                    ],
                    transaction_mode=True,
                )

    # Process each merge operation
    for op in request.operations:
        try:
            merged_id = registry.merge_entities(
                op.source_entity_id,
                op.target_entity_id,
                canonical_name=op.keep_canonical_name,
            )

            results.append(
                {
                    "source_entity_id": op.source_entity_id,
                    "target_entity_id": op.target_entity_id,
                    "merged_entity_id": merged_id,
                    "success": True,
                }
            )
            successful += 1

        except Exception as e:
            results.append(
                {
                    "source_entity_id": op.source_entity_id,
                    "target_entity_id": op.target_entity_id,
                    "success": False,
                    "error": str(e),
                }
            )
            failed += 1

            # In transaction mode, stop on first failure
            if request.transaction_mode:
                break

    # Publish bulk operation event
    if successful > 0:
        webhook_service = get_webhook_service()
        successful_ops = [r for r in results if r["success"]]
        await webhook_service.publish_bulk_event(
            "merge",
            [op["source_id"] for op in successful_ops],
            {
                "total_merged": successful,
                "total_attempted": len(request.operations),
                "failed": failed,
            },
        )

    return BulkMergeResponse(
        total_operations=len(request.operations),
        successful=successful,
        failed=failed,
        results=results,
        transaction_mode=request.transaction_mode,
    )


@router.post("/validate", response_model=BulkValidationResponse)
async def bulk_validate_entities(request: BulkValidationRequest = Body(...)):
    """Validate multiple entities against rules"""
    start_time = time.time()

    # Validate entities concurrently
    validation_tasks = [
        _validate_entity(entity_id, request.validation_rules)
        for entity_id in request.entity_ids
    ]

    validation_results = await asyncio.gather(*validation_tasks)

    # Calculate performance metrics if requested
    performance_metrics = None
    if (
        hasattr(request, "include_performance_metrics")
        and request.include_performance_metrics
    ):
        total_time_ms = (time.time() - start_time) * 1000
        performance_metrics = {
            "total_time_ms": total_time_ms,
            "avg_time_per_entity_ms": total_time_ms / len(request.entity_ids)
            if request.entity_ids
            else 0,
            "entities_per_second": len(request.entity_ids) / (total_time_ms / 1000)
            if total_time_ms > 0
            else 0,
        }

    return BulkValidationResponse(
        total_validated=len(validation_results),
        validation_results=validation_results,
        performance_metrics=performance_metrics,
    )


@router.post("/enrich", response_model=BulkEnrichmentResponse)
async def bulk_enrich_entities(request: BulkEnrichmentRequest = Body(...)):
    """Enrich multiple entities with external data"""
    try:
        enrichment_service = get_entity_enrichment_service()

        # For large batches, use async processing
        if len(request.entity_ids) > 50 and request.enrichment_options.async_processing:
            # Create async job
            job_manager = get_job_manager()

            job_options = {
                "sources": request.enrichment_options.sources,
                "fields": request.enrichment_options.fields,
                "confidence_threshold": request.enrichment_options.confidence_threshold,
                "track_sources": request.enrichment_options.track_sources,
            }

            job_id = await job_manager.create_job(request.entity_ids, job_options)

            return BulkEnrichmentResponse(
                total_enriched=0,
                enrichment_results=[],
                job_id=job_id,
                status="processing",
                status_url=f"/api/entities/bulk/enrich/status/{job_id}",
            )

        # Process synchronously for smaller batches
        enrichment_results = []
        total_enriched = 0

        for entity_id in request.entity_ids:
            try:
                # Enrich entity
                enrichment_data = await enrichment_service.enrich_entity(
                    entity_id,
                    sources=request.enrichment_options.sources,
                    fields=request.enrichment_options.fields,
                    confidence_threshold=request.enrichment_options.confidence_threshold,
                )

                result = {
                    "entity_id": entity_id,
                    "success": True,
                    "enriched_fields": enrichment_data.get("new_fields", []),
                    "confidence_boost": enrichment_data.get("confidence_boost", 0),
                }

                if request.enrichment_options.track_sources:
                    result["enrichment_sources"] = {
                        source: {"timestamp": datetime.utcnow().isoformat()}
                        for source in request.enrichment_options.sources
                    }

                enrichment_results.append(result)
                total_enriched += 1

            except Exception as e:
                enrichment_results.append(
                    {"entity_id": entity_id, "success": False, "error": str(e)}
                )

        return BulkEnrichmentResponse(
            total_enriched=total_enriched,
            enrichment_results=enrichment_results,
            status="completed",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk enrichment failed: {str(e)}")


@router.get("/enrich/status/{job_id}")
async def get_enrichment_job_status(job_id: str):
    """Get status of async enrichment job"""
    job_manager = get_job_manager()

    job_status = await job_manager.get_job_status(job_id)

    if not job_status:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return job_status


@router.get("/enrich/jobs")
async def list_enrichment_jobs(
    limit: int = Query(default=20, le=100, description="Maximum jobs to return"),
):
    """List all enrichment jobs"""
    job_manager = get_job_manager()

    all_jobs = job_manager.get_all_jobs()

    return {"jobs": all_jobs[:limit], "total": len(all_jobs)}


@router.post("/enrich/jobs/{job_id}/cancel")
async def cancel_enrichment_job(job_id: str):
    """Cancel a running enrichment job"""
    job_manager = get_job_manager()

    cancelled = await job_manager.cancel_job(job_id)

    if not cancelled:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} cannot be cancelled (not found or already completed)",
        )

    return {"success": True, "job_id": job_id, "message": "Job cancelled successfully"}
