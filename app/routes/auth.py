"""Authentication routes for the community edition (demo/none modes)."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services.auth import SESSION_COOKIE, get_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_secure(request: Request) -> bool:
    """Determine if we're in a secure (HTTPS) context, proxy-aware."""
    xf_proto = request.headers.get("x-forwarded-proto", "")
    xf_proto_first = xf_proto.split(",")[0].strip().lower()
    return request.url.scheme == "https" or xf_proto_first == "https"


@router.get("/status")
async def auth_status():
    """Check the authentication configuration.

    Returns:
        JSON with configuration status and available features
    """
    return {
        "configured": True,
        "demo_mode": True,
        "mode": settings.AUTH_MODE,
        "features": {
            "session_management": settings.AUTH_MODE == "demo",
            "user_profiles": False,
        },
    }


@router.get("/login")
async def login(request: Request):
    """Establish a demo session and redirect to the app.

    In AUTH_MODE=none this is a no-op redirect (every request already
    resolves to the demo user). In AUTH_MODE=demo it sets the session
    cookie that the middleware requires.
    """
    response = RedirectResponse(url="/", status_code=302)

    if settings.AUTH_MODE == "demo":
        response.set_cookie(
            key=SESSION_COOKIE,
            value="demo_user",
            httponly=True,
            samesite="lax",
            secure=_is_secure(request),
            path="/",
            max_age=86400,  # 24 hours
        )
        logger.info("Demo session cookie set")

    return response


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Log out the current user."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value="",
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
        max_age=0,  # Expire immediately
    )

    return {"message": "Logged out successfully"}


@router.get("/me")
async def get_current_user(request: Request):
    """Get the current authenticated user's information."""
    auth_service = get_auth_service()

    auth_result = await auth_service.get_user_from_session(request)

    if not auth_result.user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return auth_result.user
