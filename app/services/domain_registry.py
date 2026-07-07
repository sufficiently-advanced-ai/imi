"""Domain Registry for managing registered domains."""

import json
from pathlib import Path
from typing import Any


class DomainRegistry:
    """Registry for managing domain installations."""

    def __init__(self):
        """Initialize the domain registry."""
        self._domains: dict[str, dict[str, Any]] = {}
        self._registry_file = Path("/tmp/domain_registry.json")
        self._load_registry()

    def _load_registry(self):
        """Load registry from persistent storage."""
        if self._registry_file.exists():
            try:
                with open(self._registry_file) as f:
                    self._domains = json.load(f)
            except Exception:
                self._domains = {}

    def _save_registry(self):
        """Save registry to persistent storage."""
        try:
            with open(self._registry_file, "w") as f:
                json.dump(self._domains, f, indent=2)
        except Exception:
            pass

    def register_domain(self, domain_name: str, domain_path: Path):
        """Register a new domain."""
        if domain_name in self._domains:
            raise ValueError(f"Domain '{domain_name}' is already registered")

        self._domains[domain_name] = {
            "path": str(domain_path),
            "registered_at": None,  # Would use datetime here
        }
        self._save_registry()

    def unregister_domain(self, domain_name: str):
        """Unregister a domain."""
        if domain_name not in self._domains:
            raise ValueError(f"Domain '{domain_name}' is not registered")

        del self._domains[domain_name]
        self._save_registry()

    def get_domain(self, domain_name: str) -> dict[str, Any] | None:
        """Get domain information."""
        return self._domains.get(domain_name)

    def list_domains(self) -> dict[str, dict[str, Any]]:
        """List all registered domains."""
        return self._domains.copy()


# Global registry instance
domain_registry = DomainRegistry()
