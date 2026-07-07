"""
Static file serving for bot display assets - Issue #437

NOTE: extension seam — not registered by the community app (`_configure`), but
downstream deployments include this router from their own module wiring. Keep
the module even if it has no in-repo importers.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/static", tags=["static"])

# Path to static files directory
STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/images/{filename}")
async def serve_image(filename: str) -> Response:
    """Serve static images for bot display."""

    # Security: Only allow specific filenames to prevent directory traversal
    ALLOWED_IMAGES = ["imi-logo.jpg", "imi-logo.png", "logo.png", "default.jpg"]

    if filename not in ALLOWED_IMAGES:
        raise HTTPException(status_code=404, detail="Image not found")

    images_dir = STATIC_DIR / "images"
    file_path = images_dir / filename

    # For imi-logo.jpg, return a placeholder image if file doesn't exist as actual image
    if filename == "imi-logo.jpg" and not file_path.exists():
        # Create a simple 1x1 pixel transparent PNG as placeholder
        # Raw bytes for a 1x1 transparent PNG
        placeholder_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x00\x00\x00\x00IEND\xaeB`\x82'

        return Response(
            content=placeholder_data,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff"
            }
        )

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    # Determine content type based on extension
    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".svg": "image/svg+xml"
    }

    ext = file_path.suffix.lower()
    content_type = content_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=file_path,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff"
        }
    )


@router.get("/templates/{filename}")
async def serve_template(filename: str) -> Response:
    """Serve static HTML templates."""

    # Security: Only allow specific template files
    ALLOWED_TEMPLATES = [
        "agent-display-bot.html",
        "agent-display-v2.html",
        "recording-paused.html",
        "video-mode.html"
    ]

    if filename not in ALLOWED_TEMPLATES:
        raise HTTPException(status_code=404, detail="Template not found")

    templates_dir = STATIC_DIR / "templates"
    file_path = templates_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Template not found")

    return FileResponse(
        path=file_path,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff"
        }
    )
