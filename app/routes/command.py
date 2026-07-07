"""Command center API routes for system configuration and status."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth import get_current_user
from app.services.config_manager import config_manager

router = APIRouter(prefix="/api/command", tags=["command"])


class ConfigUpdateRequest(BaseModel):
    """Request model for config updates."""

    claude: dict[str, Any] | None = None
    github: dict[str, Any] | None = None


class ConnectionTestRequest(BaseModel):
    """Request model for connection testing."""

    service: str


@router.get("/config")
async def get_config(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Get the current system configuration.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing system configuration")
    config = config_manager.get_config()

    # Mask sensitive values — show only configured/not configured status
    _secret_keys = {
        ("claude", "api_key"),
        ("github", "token"),
        ("github", "webhook_secret"),
    }
    for section, key in _secret_keys:
        if section in config and config[section] and key in config[section]:
            value = config[section][key]
            config[section][key] = "configured" if value else "not configured"

    return config


@router.post("/config")
async def update_config(
    request: ConfigUpdateRequest, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Update system configuration.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} updating system configuration")
    config_data = request.model_dump(exclude_none=True)

    # Strip masked/sentinel values that shouldn't overwrite real secrets
    sentinel_values = {"configured", "not configured", "not_configured"}
    for _service_name, service_config in config_data.items():
        for key, value in list(service_config.items()):
            if isinstance(value, str) and (
                value.startswith("****") or "..." in value or value.lower() in sentinel_values
            ):
                service_config.pop(key)

    config_manager.update_config(config_data)

    # Mask sensitive values in response
    return await get_config()


@router.get("/status")
async def get_status(
    user: dict = Depends(get_current_user),
) -> dict[str, dict[str, Any]]:
    """Test and return the status of all connections.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} checking system status")

    return await config_manager.test_all_connections()


@router.post("/test-connection")
async def test_connection(
    request: ConnectionTestRequest, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Test a specific service connection.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} testing connection to {request.service}"
    )
    if request.service not in ["claude", "github"]:
        raise HTTPException(
            status_code=400, detail=f"Unknown service: {request.service}"
        )

    return await config_manager.test_connection(request.service)
