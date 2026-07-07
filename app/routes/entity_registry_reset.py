"""API routes for entity registry reset with rebuild support - Issue #83"""

import logging

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.domain.entities.services import get_entity_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities/registry", tags=["entity-registry"])


class ResetRequest(BaseModel):
    """Request model for registry reset"""

    confirm: str = Field(
        ..., description="Confirmation string - must be 'RESET-REGISTRY'"
    )
    rebuild: bool = Field(
        False, description="Whether to rebuild registry from documents after reset"
    )


class ResetResponse(BaseModel):
    """Response model for registry reset"""

    status: str
    cleared: dict[str, int]
    rebuilt: dict[str, int] | None = None
    duration_ms: int
    message: str | None = None


@router.post("/reset", response_model=ResetResponse)
async def reset_registry(request: ResetRequest = Body(...)):
    """
    Reset the entity registry with optional rebuild.

    This endpoint provides a controlled way to reset the entity registry in
    development and demo environments. It requires explicit confirmation and
    optionally rebuilds the registry from document metadata.

    Args:
        request: ResetRequest with confirmation and rebuild flag

    Returns:
        ResetResponse with operation statistics

    Raises:
        400: Invalid confirmation
        500: Reset operation failed
    """
    # Validate confirmation
    if request.confirm != "RESET-REGISTRY":
        raise HTTPException(
            status_code=400,
            detail="Invalid confirmation. Must provide confirm='RESET-REGISTRY'",
        )

    try:
        # Get registry instance
        registry = get_entity_repository()

        # Perform reset
        result = registry.reset(rebuild=request.rebuild)

        # Build response
        response = ResetResponse(
            status="success",
            cleared=result["cleared"],
            duration_ms=result["duration_ms"],
        )

        if request.rebuild and "rebuilt" in result:
            response.rebuilt = result["rebuilt"]

        return response

    except Exception as e:
        logger.error(f"Registry reset failed: {str(e)}", exc_info=True)

        # Return error response
        raise HTTPException(status_code=500, detail=f"Registry reset failed: {str(e)}")


@router.get("/reset")
async def method_not_allowed():
    """Endpoint to handle GET requests with proper error"""
    raise HTTPException(
        status_code=405, detail="Method not allowed. Use POST to reset registry."
    )


@router.put("/reset")
async def method_not_allowed_put():
    """Endpoint to handle PUT requests with proper error"""
    raise HTTPException(
        status_code=405, detail="Method not allowed. Use POST to reset registry."
    )


@router.delete("/reset")
async def method_not_allowed_delete():
    """Endpoint to handle DELETE requests with proper error"""
    raise HTTPException(
        status_code=405, detail="Method not allowed. Use POST to reset registry."
    )
