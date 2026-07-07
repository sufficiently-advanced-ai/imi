"""
Domain Configuration CLI - Issue #162

Command-line interface for managing domain configurations.
Provides commands for listing, validating, switching, and managing domains.
"""

from datetime import datetime
from pathlib import Path

from ..core.domain_config.domain_config_service import DomainConfigService


# Define basic exceptions for CLI usage
class ConfigurationError(Exception):
    pass

class ValidationError(Exception):
    pass


class DomainConfigCLI:
    """Command-line interface for domain configuration management."""

    def __init__(self, config_dir: Path | None = None):
        """
        Initialize CLI with configuration directory.

        Args:
            config_dir: Directory containing domain configurations
        """
        if config_dir is None:
            # Default to config/domains relative to project root
            project_root = Path(__file__).parent.parent.parent
            config_dir = project_root / "config" / "domains"

        self.config_dir = Path(config_dir)
        self.manager = DomainConfigService(self.config_dir)

    def list_domains(self) -> str:
        """
        List all available domain configurations.

        Returns:
            Formatted string with domain list
        """
        try:
            domains = self.manager.list_available_domains()

            if not domains:
                return "No domain configurations found."

            output = ["Available Domains:", "=" * 50]

            for domain in sorted(domains, key=lambda d: d["id"]):
                status = (
                    "●" if self.manager.get_active_domain_id() == domain["id"] else "○"
                )
                last_modified = datetime.fromtimestamp(
                    domain["last_modified"]
                ).strftime("%Y-%m-%d %H:%M")

                output.append(f"{status} {domain['id']}")
                output.append(f"  Name: {domain['name']}")
                output.append(f"  Version: {domain['version']}")
                output.append(f"  File: {Path(domain['file']).name}")
                output.append(f"  Modified: {last_modified}")
                output.append("")

            # Add active domain info
            active_domain = self.manager.get_active_domain_id()
            if active_domain:
                output.append(f"Active Domain: {active_domain}")
            else:
                output.append("No active domain set")

            return "\n".join(output)

        except Exception as e:
            return f"Error listing domains: {e}"

    def validate_domain(self, domain_id: str) -> str:
        """
        Validate a domain configuration.

        Args:
            domain_id: ID of domain to validate

        Returns:
            Validation result message
        """
        try:
            # Load the domain to trigger validation
            config = self.manager.get_domain_config(domain_id)

            output = [
                f"Domain Validation: {domain_id}",
                "=" * 50,
                "✓ Configuration loaded successfully",
                f"✓ Domain ID: {config.id}",
                f"✓ Name: {config.name}",
                f"✓ Version: {config.version}",
                f"✓ Entities: {len(config.entities)}",
                f"✓ Intelligence Patterns: {len(config.intelligence_patterns)}",
                f"✓ Success Metrics: {len(config.success_metrics)}",
                "",
                "✓ All validations passed",
            ]

            return "\n".join(output)

        except ConfigurationError as e:
            return f"❌ Configuration Error: {e}"
        except ValidationError as e:
            return f"❌ Validation Error: {e}"
        except Exception as e:
            return f"❌ Unexpected Error: {e}"

    def set_active_domain(self, domain_id: str) -> str:
        """
        Set the active domain configuration.

        Args:
            domain_id: ID of domain to activate

        Returns:
            Status message
        """
        try:
            previous_domain = self.manager.get_active_domain_id()
            self.manager.set_active_domain(domain_id)

            output = [f"Domain Activated: {domain_id}", "=" * 50]

            if previous_domain:
                output.append(f"Previous domain: {previous_domain}")

            config = self.manager.get_active_domain()
            output.extend(
                [
                    f"Active domain: {config.name} (v{config.version})",
                    f"Entities: {len(config.entities)}",
                    f"Intelligence patterns: {len(config.intelligence_patterns)}",
                    f"Success metrics: {len(config.success_metrics)}",
                    "",
                    "✓ Domain activation successful",
                ]
            )

            return "\n".join(output)

        except Exception as e:
            return f"❌ Failed to set active domain: {e}"

    def show_domain_info(self, domain_id: str, detailed: bool = False) -> str:
        """
        Show detailed information about a domain.

        Args:
            domain_id: ID of domain to show
            detailed: Whether to show detailed entity/pattern info

        Returns:
            Formatted domain information
        """
        try:
            config = self.manager.get_domain_config(domain_id)

            output = [
                f"Domain Information: {domain_id}",
                "=" * 50,
                f"Name: {config.name}",
                f"Version: {config.version}",
                f"Entities: {len(config.entities)}",
                f"Intelligence Patterns: {len(config.intelligence_patterns)}",
                f"Success Metrics: {len(config.success_metrics)}",
                "",
            ]

            if detailed:
                # Show entities
                if config.entities:
                    output.append("Entities:")
                    for entity_id, entity in config.entities.items():
                        attrs = len(entity.get("attributes", []))
                        rels = len(entity.get("relationships", []))
                        output.append(
                            f"  • {entity_id}: {attrs} attributes, {rels} relationships"
                        )
                    output.append("")

                # Show intelligence patterns
                if config.intelligence_patterns:
                    output.append("Intelligence Patterns:")
                    for pattern_id, pattern in config.intelligence_patterns.items():
                        priority = pattern.get("priority", "medium")
                        triggers = len(pattern.get("triggers", []))
                        output.append(
                            f"  • {pattern_id}: {priority} priority, {triggers} triggers"
                        )
                    output.append("")

                # Show success metrics
                if config.success_metrics:
                    output.append("Success Metrics:")
                    for metric_id, metric in config.success_metrics.items():
                        metric_type = metric.get("type", "unknown")
                        target = metric.get("target", "N/A")
                        output.append(
                            f"  • {metric_id}: {metric_type}, target: {target}"
                        )
                    output.append("")

            # Show cache and manager stats
            stats = self.manager.get_manager_stats()
            output.extend(
                [
                    "Manager Status:",
                    f"  Cache hits: {stats['cache_stats']['hits']}",
                    f"  Cache hit rate: {stats['cache_stats']['hit_rate']:.1%}",
                    f"  Available domains: {stats['available_domains']}",
                ]
            )

            return "\n".join(output)

        except Exception as e:
            return f"❌ Error showing domain info: {e}"

    def export_domain(
        self, domain_id: str, output_file: str, format: str = "yaml"
    ) -> str:
        """
        Export domain configuration to file.

        Args:
            domain_id: ID of domain to export
            output_file: Path to output file
            format: Export format ('yaml' or 'json')

        Returns:
            Status message
        """
        try:
            config_string = self.manager.export_domain_config(domain_id, format)

            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(config_string)

            return f"✓ Domain '{domain_id}' exported to {output_path}"

        except Exception as e:
            return f"❌ Export failed: {e}"

    def import_domain(self, input_file: str, format: str = "auto") -> str:
        """
        Import domain configuration from file.

        Args:
            input_file: Path to input file
            format: Import format ('yaml', 'json', or 'auto')

        Returns:
            Status message
        """
        try:
            input_path = Path(input_file)

            if not input_path.exists():
                return f"❌ Input file not found: {input_path}"

            # Auto-detect format
            if format == "auto":
                if input_path.suffix.lower() in [".yaml", ".yml"]:
                    format = "yaml"
                elif input_path.suffix.lower() == ".json":
                    format = "json"
                else:
                    return f"❌ Cannot auto-detect format for {input_path.suffix}"

            with open(input_path, encoding="utf-8") as f:
                config_string = f.read()

            config = self.manager.import_domain_config(config_string, format)

            return f"✓ Domain '{config.id}' imported successfully from {input_path}"

        except Exception as e:
            return f"❌ Import failed: {e}"

    def show_cache_stats(self) -> str:
        """
        Show cache statistics and information.

        Returns:
            Formatted cache statistics
        """
        try:
            cache_info = self.manager.cache.get_cache_info()

            output = ["Cache Statistics", "=" * 50]

            stats = cache_info["stats"]
            output.extend(
                [
                    f"Hits: {stats['hits']}",
                    f"Misses: {stats['misses']}",
                    f"Hit Rate: {stats['hit_rate']:.1%}",
                    f"Evictions: {stats['evictions']}",
                    f"Invalidations: {stats['invalidations']}",
                    f"Current Size: {stats['current_size']}/{stats['max_size']}",
                    f"TTL: {stats['ttl_seconds']} seconds",
                    "",
                ]
            )

            if cache_info["entries"]:
                output.append("Cached Entries:")
                for entry in cache_info["entries"]:
                    status = "EXPIRED" if entry["is_expired"] else "VALID"
                    output.append(
                        f"  • {entry['domain_id']}: {status}, "
                        f"accessed {entry['access_count']} times, "
                        f"expires in {entry['time_until_expiry']:.0f}s"
                    )
            else:
                output.append("No entries in cache")

            return "\n".join(output)

        except Exception as e:
            return f"❌ Error getting cache stats: {e}"

    def clear_cache(self) -> str:
        """
        Clear the domain configuration cache.

        Returns:
            Status message
        """
        try:
            self.manager.cache.clear()
            return "✓ Cache cleared successfully"
        except Exception as e:
            return f"❌ Error clearing cache: {e}"

    def backup_domain(self, domain_id: str) -> str:
        """
        Create backup of domain configuration.

        Args:
            domain_id: ID of domain to backup

        Returns:
            Status message
        """
        try:
            backup_path = self.manager.create_backup(domain_id)
            return f"✓ Backup created: {backup_path}"
        except Exception as e:
            return f"❌ Backup failed: {e}"

    def show_schema_info(self) -> str:
        """
        Show domain configuration schema information.

        Returns:
            Formatted schema information
        """
        try:
            schema_info = self.manager.loader.get_schema_validation_info()

            output = [
                "Domain Configuration Schema",
                "=" * 50,
                "",
                "Required Fields:",
                *[f"  • {field}" for field in schema_info["required_fields"]],
                "",
                "Valid Attribute Types:",
                *[
                    f"  • {attr_type}"
                    for attr_type in schema_info["valid_attribute_types"]
                ],
                "",
                "Valid Priorities:",
                *[f"  • {priority}" for priority in schema_info["valid_priorities"]],
                "",
                "Valid Cardinalities:",
                *[
                    f"  • {cardinality}"
                    for cardinality in schema_info["valid_cardinalities"]
                ],
                "",
                "Valid Metric Types:",
                *[
                    f"  • {metric_type}"
                    for metric_type in schema_info["valid_metric_types"]
                ],
            ]

            return "\n".join(output)

        except Exception as e:
            return f"❌ Error getting schema info: {e}"
