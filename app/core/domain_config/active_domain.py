"""
Singleton domain configuration loader.

Loads the active domain config synchronously at import time from the
ACTIVE_DOMAIN env var (or falls back to the first file in config/domains/).

Survives uvicorn hot-reload because Python re-executes the module on reload.
"""

import logging
import os
from pathlib import Path

import yaml

from app.model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)

_DOMAINS_DIRS = [Path("config/domains"), Path("domains")]


def _load_domain_config() -> DomainConfiguration:
    """Load domain configuration synchronously. Fails fast if nothing found."""
    domain_id = os.environ.get("ACTIVE_DOMAIN")

    if domain_id:
        for domains_dir in _DOMAINS_DIRS:
            for ext in (".yaml", ".yml"):
                candidate = domains_dir / f"{domain_id}{ext}"
                if candidate.exists():
                    return _parse_yaml(candidate)
        raise RuntimeError(
            f"ACTIVE_DOMAIN='{domain_id}' but no config file found in "
            f"{' or '.join(str(d) for d in _DOMAINS_DIRS)}"
        )

    # Fall back to first YAML file in domain directories
    for domains_dir in _DOMAINS_DIRS:
        if domains_dir.exists():
            for f in sorted(domains_dir.iterdir()):
                if f.suffix in (".yaml", ".yml") and f.is_file():
                    logger.info(f"ACTIVE_DOMAIN not set, falling back to {f.name}")
                    return _parse_yaml(f)

    raise RuntimeError(
        "No domain configuration found. Set ACTIVE_DOMAIN env var or "
        f"add a YAML file to {_DOMAINS_DIRS[0]}/"
    )


def _parse_yaml(path: Path) -> DomainConfiguration:
    """Parse a domain YAML file into a DomainConfiguration."""
    data = yaml.safe_load(path.read_text())
    if "domain" in data:
        data = data["domain"]
    config = DomainConfiguration(**data)
    logger.info(f"Loaded domain config '{config.id}' from {path}")
    return config


# Loaded once at import time; re-executed on hot-reload.
_ACTIVE_DOMAIN: DomainConfiguration = _load_domain_config()


def _resolve_active_domain() -> DomainConfiguration:
    """Return the single import-time-loaded active domain configuration.

    Reached through the single-tenant container's ``domain_config`` property.
    Kept separate from ``get_domain_config`` so the tenant-scoped accessor can
    delegate without recursing.
    """
    return _ACTIVE_DOMAIN


def get_domain_config() -> DomainConfiguration:
    """Return the active domain configuration for the current tenant.

    Tenant-scoped accessor (Phase 4.1): in single-tenant mode the container
    returns the import-time-loaded ``_ACTIVE_DOMAIN`` (via
    ``_resolve_active_domain``), so behavior is unchanged. Never returns None.
    """
    from app.core.tenancy.context import current_tenant

    return current_tenant().domain_config
