"""
Entity Migration Service for transitioning to domain-aware entities.

This service helps migrate from hardcoded entity types to dynamic,
domain-driven entity management.
"""

import logging
import os
import shutil
from datetime import datetime
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class EntityMigrationService:
    """Service for migrating legacy entities to domain-aware structure."""

    def __init__(self, target_domain_id: str):
        """
        Initialize migration service.

        Args:
            target_domain_id: Domain configuration to migrate to
        """
        self.target_domain_id = target_domain_id
        from app.domain.entities.services import EntityService
        self.entity_brain = EntityService(domain_id=target_domain_id)

        # Legacy directory mapping
        self.legacy_dirs = {"people": "person", "projects": "project", "teams": "team"}

        # Track migration progress
        self.migration_log = []

    async def analyze_migration_requirements(self) -> dict[str, Any]:
        """
        Analyze what needs to be migrated.

        Returns:
            Analysis report with entity counts and compatibility
        """
        report = {
            "legacy_entities": {},
            "target_domain": self.target_domain_id,
            "target_entity_types": self.entity_brain.entity_registry.get_entity_types(),
            "compatibility": {},
            "recommendations": [],
        }

        # Count legacy entities
        for legacy_dir, _legacy_type in self.legacy_dirs.items():
            dir_path = f"entities/{legacy_dir}"
            if os.path.exists(dir_path):
                files = [f for f in os.listdir(dir_path) if f.endswith(".md")]
                report["legacy_entities"][legacy_dir] = len(files)
            else:
                report["legacy_entities"][legacy_dir] = 0

        # Check compatibility
        for _legacy_dir, legacy_type in self.legacy_dirs.items():
            if legacy_type in report["target_entity_types"]:
                report["compatibility"][legacy_type] = "direct"
            else:
                # Check if there's a similar type
                similar = self._find_similar_entity_type(legacy_type)
                if similar:
                    report["compatibility"][legacy_type] = f"map_to:{similar}"
                else:
                    report["compatibility"][legacy_type] = "no_match"

        # Generate recommendations
        if all(c == "direct" for c in report["compatibility"].values()):
            report["recommendations"].append(
                "All legacy types have direct matches. Migration is straightforward."
            )
        else:
            report["recommendations"].append(
                "Some legacy types need mapping. Review compatibility section."
            )

        total_entities = sum(report["legacy_entities"].values())
        report["total_entities"] = total_entities

        return report

    def _find_similar_entity_type(self, legacy_type: str) -> str | None:
        """Find a similar entity type in the target domain."""
        # Map common legacy types to domain-specific types
        type_mappings = {
            "person": ["consultant", "employee", "contact", "member"],
            "project": ["engagement", "initiative", "program", "workstream"],
            "team": ["department", "group", "unit", "division"],
        }

        current_types = self.entity_brain.entity_registry.get_entity_types()

        if legacy_type in type_mappings:
            for candidate in type_mappings[legacy_type]:
                if candidate in current_types:
                    return candidate

        return None

    async def create_migration_plan(self) -> dict[str, Any]:
        """
        Create a detailed migration plan.

        Returns:
            Migration plan with mappings and transformations
        """
        analysis = await self.analyze_migration_requirements()

        plan = {
            "created_at": datetime.utcnow().isoformat(),
            "source": "legacy_entities",
            "target_domain": self.target_domain_id,
            "mappings": {},
            "attribute_mappings": {},
            "steps": [],
        }

        # Create type mappings
        for _legacy_dir, legacy_type in self.legacy_dirs.items():
            compat = analysis["compatibility"].get(legacy_type, "no_match")

            if compat == "direct":
                target_type = legacy_type
            elif compat.startswith("map_to:"):
                target_type = compat.split(":")[1]
            else:
                target_type = None

            if target_type:
                plan["mappings"][legacy_type] = target_type

                # Create attribute mappings
                attr_map = self._create_attribute_mapping(legacy_type, target_type)
                plan["attribute_mappings"][legacy_type] = attr_map

        # Create migration steps
        plan["steps"] = [
            {
                "step": 1,
                "action": "backup",
                "description": "Create backup of existing entities",
            },
            {
                "step": 2,
                "action": "validate",
                "description": "Validate all entities can be migrated",
            },
            {
                "step": 3,
                "action": "migrate",
                "description": "Migrate entities to new structure",
            },
            {
                "step": 4,
                "action": "verify",
                "description": "Verify all entities migrated successfully",
            },
            {
                "step": 5,
                "action": "cleanup",
                "description": "Remove legacy directories (optional)",
            },
        ]

        return plan

    def _create_attribute_mapping(
        self, source_type: str, target_type: str
    ) -> dict[str, str]:
        """Create attribute mapping between source and target types."""
        # Get target schema
        target_schema = self.entity_brain.entity_registry.get_entity_schema(target_type)
        if not target_schema:
            return {}

        # Common attribute mappings
        mappings = {
            # Legacy person -> consultant
            "person": {
                "name": "name",
                "email": "email",
                "phone": "phone",
                "title": "role",
                "department": "department",
                "team": "team",
            },
            # Legacy project -> engagement
            "project": {
                "title": "title",
                "name": "title",
                "description": "description",
                "status": "phase",
                "start_date": "start_date",
                "end_date": "end_date",
                "team": "team",
                "lead": "lead",
            },
            # Legacy team -> department
            "team": {
                "name": "name",
                "description": "description",
                "lead": "head",
                "members": "members",
                "department": "division",
            },
        }

        return mappings.get(source_type, {})

    async def execute_migration(
        self, dry_run: bool = True, backup: bool = True, cleanup: bool = False
    ) -> dict[str, Any]:
        """
        Execute the migration plan.

        Args:
            dry_run: If True, simulate migration without changes
            backup: If True, create backup before migration
            cleanup: If True, remove legacy directories after migration

        Returns:
            Migration results
        """
        results = {
            "started_at": datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "entities_processed": 0,
            "entities_migrated": 0,
            "entities_skipped": 0,
            "errors": [],
            "log": [],
        }

        # Create migration plan
        plan = await self.create_migration_plan()

        # Step 1: Backup
        if backup and not dry_run:
            backup_path = self._create_backup()
            results["backup_path"] = backup_path
            results["log"].append(f"Created backup at {backup_path}")

        # Step 2: Migrate entities
        for legacy_type, target_type in plan["mappings"].items():
            legacy_dir = None
            for dir_name, type_name in self.legacy_dirs.items():
                if type_name == legacy_type:
                    legacy_dir = dir_name
                    break

            if not legacy_dir:
                continue

            dir_path = f"entities/{legacy_dir}"
            if not os.path.exists(dir_path):
                continue

            # Process each entity file
            for filename in os.listdir(dir_path):
                if not filename.endswith(".md"):
                    continue

                results["entities_processed"] += 1

                try:
                    file_path = os.path.join(dir_path, filename)
                    success = await self._migrate_entity_file(
                        file_path,
                        legacy_type,
                        target_type,
                        plan["attribute_mappings"].get(legacy_type, {}),
                        dry_run,
                    )

                    if success:
                        results["entities_migrated"] += 1
                        results["log"].append(f"Migrated {file_path}")
                    else:
                        results["entities_skipped"] += 1
                        results["log"].append(f"Skipped {file_path}")

                except Exception as e:
                    results["errors"].append(f"Error migrating {filename}: {str(e)}")
                    logger.error(f"Migration error for {filename}: {e}")

        # Step 3: Cleanup
        if cleanup and not dry_run and results["entities_migrated"] > 0:
            for legacy_dir in self.legacy_dirs.keys():
                dir_path = f"entities/{legacy_dir}"
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path)
                    results["log"].append(f"Removed legacy directory {dir_path}")

        results["completed_at"] = datetime.utcnow().isoformat()
        return results

    async def _migrate_entity_file(
        self,
        file_path: str,
        source_type: str,
        target_type: str,
        attribute_mapping: dict[str, str],
        dry_run: bool,
    ) -> bool:
        """Migrate a single entity file."""
        try:
            # Read source file
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Extract metadata
            metadata, body = self._extract_frontmatter_and_content(content)
            if not metadata:
                logger.warning(f"No metadata in {file_path}")
                return False

            # Map attributes
            new_attributes = {}
            target_schema = self.entity_brain.entity_registry.get_entity_schema(
                target_type
            )

            if target_schema:
                # Map known attributes
                for old_attr, new_attr in attribute_mapping.items():
                    if old_attr in metadata and new_attr in target_schema.attributes:
                        new_attributes[new_attr] = metadata[old_attr]

                # Fill required attributes
                for attr_id, attr_schema in target_schema.attributes.items():
                    if attr_schema.required and attr_id not in new_attributes:
                        # Try to find value in metadata
                        if attr_id in metadata:
                            new_attributes[attr_id] = metadata[attr_id]
                        else:
                            # Set default
                            if attr_schema.type.value == "string":
                                new_attributes[attr_id] = metadata.get(
                                    "name", "Unknown"
                                )
                            elif (
                                attr_schema.type.value == "enum"
                                and attr_schema.enum_values
                            ):
                                new_attributes[attr_id] = attr_schema.enum_values[0]

            # Extract entity ID
            entity_id = os.path.basename(file_path).replace(".md", "")

            # Create new entity (if not dry run)
            if not dry_run:
                new_path = await self.entity_brain.create_entity_file(
                    entity_type=target_type,
                    entity_id=entity_id,
                    attributes=new_attributes,
                    references=metadata.get("references", []),
                )

                return new_path is not None
            else:
                # Validate that entity can be created
                is_valid, errors = self.entity_brain.entity_registry.validate_entity(
                    target_type, new_attributes
                )
                return is_valid

        except Exception as e:
            logger.error(f"Error migrating {file_path}: {e}")
            return False

    def _extract_frontmatter_and_content(
        self, content: str
    ) -> tuple[dict | None, str]:
        """Extract YAML frontmatter and content from markdown."""
        if not content.startswith("---\n"):
            return None, content

        end_idx = content.find("\n---\n", 4)
        if end_idx == -1:
            return None, content

        try:
            yaml_content = content[4:end_idx]
            metadata = yaml.safe_load(yaml_content)
            body_content = content[end_idx + 5 :]
            return metadata, body_content
        except yaml.YAMLError:
            return None, content

    def _create_backup(self) -> str:
        """Create backup of entities directory."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"entities_backup_{timestamp}"

        if os.path.exists("entities"):
            shutil.copytree("entities", backup_dir)

        return backup_dir

    async def generate_migration_report(self) -> str:
        """Generate a detailed migration report."""
        analysis = await self.analyze_migration_requirements()
        plan = await self.create_migration_plan()

        report = f"""# Entity Migration Report

Generated: {datetime.utcnow().isoformat()}

## Target Domain
- Domain ID: {self.target_domain_id}
- Entity Types: {', '.join(analysis['target_entity_types'])}

## Legacy Entities
"""

        for legacy_dir, count in analysis["legacy_entities"].items():
            report += f"- {legacy_dir}: {count} entities\n"

        report += f"\nTotal: {analysis['total_entities']} entities\n"

        report += "\n## Compatibility Analysis\n"
        for legacy_type, compat in analysis["compatibility"].items():
            if compat == "direct":
                report += f"- {legacy_type}: ✓ Direct match\n"
            elif compat.startswith("map_to:"):
                target = compat.split(":")[1]
                report += f"- {legacy_type}: → Maps to '{target}'\n"
            else:
                report += f"- {legacy_type}: ✗ No match\n"

        report += "\n## Migration Plan\n"
        report += "### Type Mappings\n"
        for source, target in plan["mappings"].items():
            report += f"- {source} → {target}\n"

        report += "\n### Migration Steps\n"
        for step in plan["steps"]:
            report += f"{step['step']}. {step['description']}\n"

        report += "\n## Recommendations\n"
        for rec in analysis["recommendations"]:
            report += f"- {rec}\n"

        return report
