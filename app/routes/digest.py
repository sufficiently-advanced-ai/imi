import glob
import json
import os
import sys
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..git_ops import git_ops
from ..models import DigestRequest, DigestResponse, File
from ..services.digest import DigestBrain
from ..services.weekly_digest import WEEKLY_DIGEST_DIR, export_weekly_digest


def _log_digest(
    operation: str, details: dict[str, Any], error: Exception = None
) -> None:
    """Log digest operations to stderr with structured format."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "component": "digest",
        "operation": operation,
        "status": "error" if error else "success",
        "details": details,
    }
    if error:
        log_entry["error"] = str(error)
    print(json.dumps(log_entry), file=sys.stderr)


router = APIRouter()


# ---------------------------------------------------------------------------
# Weekly digest endpoints — registered BEFORE the catch-all /digest/{date}
# ---------------------------------------------------------------------------


@router.post("/digest/weekly/export")
async def export_weekly_digest_endpoint():
    """Trigger a weekly digest export and return the result dict."""
    try:
        result = await export_weekly_digest()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/digest/weekly/latest", response_class=PlainTextResponse)
async def get_weekly_digest_latest():
    """Return the content of the most recent weekly digest file as text/markdown.

    Returns 404 with detail "No weekly digest yet" when no files exist.
    """
    digest_dir = os.path.join(git_ops.repo_path, WEEKLY_DIGEST_DIR)
    pattern = os.path.join(digest_dir, "weekly-*.md")
    files = glob.glob(pattern)

    if not files:
        raise HTTPException(status_code=404, detail="No weekly digest yet")

    # Sort lexicographically — ISO date names sort correctly
    latest = sorted(files)[-1]
    try:
        with open(latest, encoding="utf-8") as fh:
            content = fh.read()
        return PlainTextResponse(content, media_type="text/markdown")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Original digest endpoints
# ---------------------------------------------------------------------------


@router.post("/digest", response_model=DigestResponse)
async def create_digest(request: DigestRequest) -> DigestResponse:
    """Create a knowledge digest for the specified date."""
    operation = "create_digest"
    try:
        _log_digest(
            operation,
            {
                "date": request.date,
                "force_refresh": request.force_refresh,
                "status": "starting",
            },
        )

        brain = DigestBrain()
        response = await brain.process_digest(
            date=request.date, force_refresh=request.force_refresh
        )

        _log_digest(
            operation,
            {
                "date": request.date,
                "digest_file": response.digest_file,
                "processed_files": response.processed_files,
                "created": response.created,
                "status": "completed",
            },
        )

        return response

    except Exception as e:
        error = HTTPException(status_code=500, detail=str(e))
        _log_digest(operation, {"date": request.date, "status": "failed"}, error)
        raise error


@router.get("/digest/{date}", response_model=File | None)
async def get_digest(date: str) -> File | None:
    """Get a digest for the specified date if it exists.

    This endpoint checks both naming formats (digest-YYYYMMDD.md and digestYYYYMMDD.md)
    and returns the digest if found, or None if no digest exists for the date.
    """
    operation = "get_digest"
    try:
        _log_digest(operation, {"date": date, "status": "starting"})

        brain = DigestBrain()
        digest = await brain.get_digest_for_date(date)

        if digest:
            _log_digest(
                operation, {"date": date, "digest_file": digest.path, "status": "found"}
            )
        else:
            _log_digest(operation, {"date": date, "status": "not_found"})

        return digest

    except Exception as e:
        error = HTTPException(status_code=500, detail=str(e))
        _log_digest(operation, {"date": date, "status": "failed"}, error)
        raise error
