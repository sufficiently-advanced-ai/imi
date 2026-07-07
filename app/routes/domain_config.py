"""
Domain configuration API endpoints.

These endpoints provide access to domain configurations, allowing
clients to retrieve current domain settings and list available domains.

Domain is loaded once at startup from the ACTIVE_DOMAIN env var.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core.domain_config import get_domain_config
from app.core.domain_config.domain_config_service import DomainConfigService
from app.model_schemas.domain_config import DomainConfiguration
from app.services.domain_config import (
    get_domain_config_loader,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/domain", tags=["domain"])


@router.get("/config", response_model=DomainConfiguration)
@router.get("/config/", response_model=DomainConfiguration, include_in_schema=False)
async def get_current_domain_config() -> DomainConfiguration:
    """
    Retrieve the current active domain configuration.

    Returns:
        DomainConfiguration: The active domain configuration
    """
    return get_domain_config()


@router.get("/domains")
@router.get("/domains/", include_in_schema=False)
async def list_available_domains(
    domain_loader: DomainConfigService = Depends(get_domain_config_loader),
) -> list[dict[str, Any]]:
    """
    List all available domain configurations.

    Returns:
        List of domain information with id, name, and description
    """
    try:
        # Load all domain configurations from the config directory
        from pathlib import Path
        config_dir = Path("config/domains")
        domains = []
        active_config = get_domain_config()

        if config_dir.exists():
            configs = await domain_loader.load_from_directory(config_dir)
            for config in configs:
                domains.append(
                    {
                        "id": config.id,
                        "name": config.name,
                        "description": getattr(config, 'description', f"Domain configuration for {config.name}"),
                        "active": config.id == active_config.id,
                    }
                )

        return domains
    except Exception as e:
        logger.error(f"Error listing domains: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class DomainSwitchRequest(BaseModel):
    domain_id: str


@router.post("/switch")
@router.post("/switch/", include_in_schema=False)
async def switch_domain(
    request: DomainSwitchRequest,
) -> DomainConfiguration:
    """
    Return the active domain configuration.

    Domain switching is a no-op in single-tenant mode — each container
    runs exactly one domain, set via the ACTIVE_DOMAIN env var.
    The frontend calls this on page load; we just return the active config.
    """
    config = get_domain_config()
    if request.domain_id != config.id:
        logger.warning(
            f"Domain switch requested to '{request.domain_id}' but active domain "
            f"is '{config.id}'. Ignoring — set ACTIVE_DOMAIN env var to change."
        )
        raise HTTPException(
            status_code=409,
            detail=f"Cannot switch to '{request.domain_id}'. Active domain is '{config.id}'. Set ACTIVE_DOMAIN env var to change.",
        )
    return config


@router.get("/display-config")
@router.get("/display-config/", include_in_schema=False)
async def get_domain_display_config() -> dict[str, dict[str, str]]:
    """
    Get display configuration for domain entities.

    Returns display properties (icon, color) for each entity type in the current domain.
    """
    try:
        config = get_domain_config()

        # Generate display config for each entity type
        display_config = {}

        # Default display configurations by entity type
        default_configs = {
            "account": {"icon": "building", "color": "#4F46E5"},
            "project": {"icon": "folder", "color": "#10B981"},
            "person": {"icon": "user", "color": "#8B5CF6"},
            "team": {"icon": "users", "color": "#F59E0B"},
            "contact": {"icon": "user", "color": "#F59E0B"},
            "company": {"icon": "building", "color": "#6366F1"},
        }

        for entity_id in config.entities:
            if entity_id in default_configs:
                display_config[entity_id] = default_configs[entity_id]
            else:
                # Generate a color based on entity ID
                color_options = [
                    "#4F46E5",
                    "#10B981",
                    "#8B5CF6",
                    "#F59E0B",
                    "#EF4444",
                    "#6366F1",
                ]
                color_index = hash(entity_id) % len(color_options)

                display_config[entity_id] = {
                    "icon": "circle",
                    "color": color_options[color_index],
                }

        return display_config

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting display config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/deployment-info")
@router.get("/deployment-info/", include_in_schema=False)
async def get_deployment_info() -> dict:
    """
    Return deployment mode and feature flags for frontend configuration.
    """
    return {
        "demo_mode": settings.DEMO_MODE,
    }
