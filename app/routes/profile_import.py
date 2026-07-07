"""Profile import API routes — import person entities from LinkedIn text."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services.auth import get_current_user
from ..services.profile_importer import ProfileImporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities", tags=["profile-import"])


# ──────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────


class ProfileImportRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Person's full name")
    profile_text: str = Field(
        ..., min_length=10, max_length=50000, description="Pasted LinkedIn profile text"
    )
    source: str = Field(default="linkedin", description="Profile source (e.g. linkedin)")


class ProfileImportResponse(BaseModel):
    status: str  # "created" or "updated"
    entity_id: str
    name: str
    title: str | None = None
    company: str | None = None
    file_path: str


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────


@router.post("/import-profile", response_model=ProfileImportResponse)
async def import_profile(
    request: ProfileImportRequest,
    _user=Depends(get_current_user),
):
    """Import a person entity from pasted LinkedIn profile text.

    Extracts structured data via Claude, creates an entity markdown file,
    adds the entity to the knowledge graph, and clears relevant caches.
    If the entity already exists it will be updated (merged) rather than overwritten.
    """
    try:
        importer = ProfileImporter()
        result = await importer.import_profile(
            name=request.name.strip(),
            profile_text=request.profile_text,
            source=request.source,
        )
        return ProfileImportResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.error(f"[PROFILE_IMPORT] Import failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[PROFILE_IMPORT] Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Profile import failed")
