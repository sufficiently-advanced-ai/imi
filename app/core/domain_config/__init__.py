"""Domain configuration core module."""

from .active_domain import get_domain_config
from .domain_config_service import DomainConfigService

__all__ = ["DomainConfigService", "get_domain_config"]
