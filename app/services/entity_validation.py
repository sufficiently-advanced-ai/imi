"""
Entity Validation Service - Issue #244.

This service provides comprehensive validation for entity data
against domain schemas, including type validation, required fields,
enum constraints, and relationship validation.
"""

import logging
from typing import Any

from dateutil import parser as date_parser

from app.core.dependencies import get_entity_repository
from app.domain.entities.services import EntityRepository

from ..model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)


class EntityValidationService:
    """Service for validating entity data against domain schemas."""

    def __init__(
        self,
        domain_config: DomainConfiguration | None = None,
        entity_registry: EntityRepository | None = None,
    ):
        """
        Initialize the validation service.

        Args:
            domain_config: Domain configuration to use
            entity_registry: Entity registry instance
        """
        self.domain_config = domain_config
        self.entity_registry = entity_registry or get_entity_repository()

        # Load domain config into registry if provided
        if domain_config:
            self.entity_registry.load_domain_config(domain_config)

    async def validate_entity_data(self, entity_data: dict[str, Any]) -> dict[str, Any]:
        """
        Validate complete entity data including attributes and relationships.

        Args:
            entity_data: Entity data to validate

        Returns:
            Validation result with is_valid flag and error details
        """
        try:
            entity_type = entity_data.get("entity_type")
            attributes = entity_data.get("attributes", {})
            relationships = entity_data.get("relationships", {})

            errors = []

            # Basic structure validation
            if not entity_type:
                errors.append(
                    {"field": "entity_type", "message": "entity_type is required"}
                )
                return {"is_valid": False, "errors": errors}

            # Validate against entity schema
            is_valid, validation_errors = self.entity_registry.validate_entity(
                entity_type, attributes
            )
            if not is_valid:
                for error_msg in validation_errors:
                    # Parse error message to extract field name
                    if "'" in error_msg:
                        parts = error_msg.split("'")
                        field_name = parts[1] if len(parts) > 1 else "unknown"
                    else:
                        field_name = "unknown"

                    errors.append({"field": field_name, "message": error_msg})

            # Validate relationships if present
            if relationships:
                relationship_errors = await self._validate_relationships(
                    entity_type, relationships
                )
                errors.extend(relationship_errors)

            return {"is_valid": len(errors) == 0, "errors": errors}

        except Exception as e:
            logger.error(f"Error validating entity data: {e}")
            return {
                "is_valid": False,
                "errors": [
                    {"field": "general", "message": f"Validation error: {str(e)}"}
                ],
            }

    async def validate_attribute_types(
        self, entity_type: str, attributes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Validate attribute types against schema.

        Args:
            entity_type: The entity type
            attributes: Attributes to validate

        Returns:
            List of validation errors
        """
        errors = []

        entity_schema = self.entity_registry.get_entity_schema(entity_type)
        if not entity_schema:
            errors.append(
                {
                    "field": "entity_type",
                    "message": f"Unknown entity type: {entity_type}",
                }
            )
            return errors

        for attr_name, value in attributes.items():
            attr_schema = entity_schema.attributes_dict.get(attr_name)
            if not attr_schema:
                # Skip unknown attributes (allow extra fields)
                continue

            if value is None and not attr_schema.required:
                # Skip optional null values
                continue

            # Type-specific validation
            try:
                if attr_schema.type == "string":
                    if not isinstance(value, str):
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be a string, got {type(value).__name__}",
                            }
                        )

                elif attr_schema.type == "number":
                    if not isinstance(value, int | float):
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be a number, got {type(value).__name__}",
                            }
                        )

                elif attr_schema.type == "boolean":
                    if not isinstance(value, bool):
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be a boolean, got {type(value).__name__}",
                            }
                        )

                elif attr_schema.type == "date":
                    if isinstance(value, str):
                        try:
                            date_parser.parse(value).date()
                        except (ValueError, TypeError):
                            errors.append(
                                {
                                    "field": attr_name,
                                    "message": f"Field '{attr_name}' must be a valid date string",
                                }
                            )
                    else:
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be a date string",
                            }
                        )

                elif attr_schema.type == "datetime":
                    if isinstance(value, str):
                        try:
                            date_parser.parse(value)
                        except (ValueError, TypeError):
                            errors.append(
                                {
                                    "field": attr_name,
                                    "message": f"Field '{attr_name}' must be a valid datetime string",
                                }
                            )
                    else:
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be a datetime string",
                            }
                        )

                elif attr_schema.type == "enum":
                    if attr_schema.enum and value not in attr_schema.enum:
                        valid_values = ", ".join(attr_schema.enum)
                        errors.append(
                            {
                                "field": attr_name,
                                "message": f"Field '{attr_name}' must be one of: {valid_values}. Got: {value}",
                            }
                        )

            except Exception as e:
                errors.append(
                    {
                        "field": attr_name,
                        "message": f"Validation error for field '{attr_name}': {str(e)}",
                    }
                )

        return errors

    async def validate_required_fields(
        self, entity_type: str, attributes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Validate that all required fields are present.

        Args:
            entity_type: The entity type
            attributes: Attributes to validate

        Returns:
            List of validation errors for missing required fields
        """
        errors = []

        entity_schema = self.entity_registry.get_entity_schema(entity_type)
        if not entity_schema:
            return errors

        for attr_name, attr_schema in entity_schema.attributes_dict.items():
            if attr_schema.required and attr_name not in attributes:
                errors.append(
                    {
                        "field": attr_name,
                        "message": f"Required field '{attr_name}' is missing",
                    }
                )

        return errors

    async def validate_enum_values(
        self, entity_type: str, attributes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Validate enum field values against allowed values.

        Args:
            entity_type: The entity type
            attributes: Attributes to validate

        Returns:
            List of validation errors for invalid enum values
        """
        errors = []

        entity_schema = self.entity_registry.get_entity_schema(entity_type)
        if not entity_schema:
            return errors

        for attr_name, value in attributes.items():
            attr_schema = entity_schema.attributes_dict.get(attr_name)
            if not attr_schema or attr_schema.type != "enum":
                continue

            if value is not None and attr_schema.enum and value not in attr_schema.enum:
                valid_values = ", ".join(attr_schema.enum)
                errors.append(
                    {
                        "field": attr_name,
                        "message": f"Invalid enum value for field '{attr_name}'. Expected one of: {valid_values}. Got: {value}",
                    }
                )

        return errors

    async def _validate_relationships(
        self, entity_type: str, relationships: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Validate entity relationships against schema.

        Args:
            entity_type: The source entity type
            relationships: Relationships to validate

        Returns:
            List of validation errors
        """
        errors = []

        entity_schema = self.entity_registry.get_entity_schema(entity_type)
        if not entity_schema:
            return errors

        for rel_name, rel_value in relationships.items():
            rel_schema = entity_schema.relationships_dict.get(rel_name)
            if not rel_schema:
                # Allow unknown relationships (they may be custom)
                continue

            # Validate cardinality constraints
            if rel_schema.cardinality in ["one-to-one", "many-to-one"]:
                # Should be single value
                if isinstance(rel_value, list):
                    errors.append(
                        {
                            "field": rel_name,
                            "message": f"Relationship '{rel_name}' has cardinality '{rel_schema.cardinality}' but multiple values provided",
                        }
                    )
            elif rel_schema.cardinality in ["one-to-many", "many-to-many"]:
                # Should be array
                if not isinstance(rel_value, list):
                    errors.append(
                        {
                            "field": rel_name,
                            "message": f"Relationship '{rel_name}' has cardinality '{rel_schema.cardinality}' but single value provided",
                        }
                    )

        return errors

    def validate_entity_id(self, entity_id: str) -> bool:
        """
        Validate entity ID format.

        Args:
            entity_id: Entity ID to validate

        Returns:
            True if valid, False otherwise
        """
        if not entity_id or not isinstance(entity_id, str):
            return False

        # Basic format validation (entity_type-uuid_part)
        parts = entity_id.split("-", 1)
        if len(parts) != 2:
            return False

        entity_type, uuid_part = parts

        # Validate entity type exists
        if entity_type not in self.entity_registry.get_entity_types():
            return False

        # Validate UUID part - more flexible validation
        # Could be a standard UUID (36 chars), shortened UUID (8+ chars), or custom ID
        import re

        # Check if it's a valid UUID format (with or without hyphens)
        uuid_pattern = (
            r"^[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}$"
        )
        short_uuid_pattern = r"^[a-f0-9]{8,}$"
        custom_id_pattern = r"^[\w-]+$"

        uuid_lower = uuid_part.lower()
        if not (
            re.match(uuid_pattern, uuid_lower)
            or re.match(short_uuid_pattern, uuid_lower)
            or re.match(custom_id_pattern, uuid_part)
        ):
            return False

        return True

    def validate_date_range(self, start_date: str, end_date: str) -> list[str]:
        """
        Validate date range.

        Args:
            start_date: Start date string
            end_date: End date string

        Returns:
            List of validation errors
        """
        errors = []

        try:
            start_dt = date_parser.parse(start_date).date()
            end_dt = date_parser.parse(end_date).date()

            if start_dt > end_dt:
                errors.append("Start date must be before or equal to end date")

        except (ValueError, TypeError) as e:
            errors.append(f"Invalid date format: {str(e)}")

        return errors

    def validate_numeric_range(self, min_value: float, max_value: float) -> list[str]:
        """
        Validate numeric range.

        Args:
            min_value: Minimum value
            max_value: Maximum value

        Returns:
            List of validation errors
        """
        errors = []

        if not isinstance(min_value, int | float) or not isinstance(
            max_value, int | float
        ):
            errors.append("Range values must be numbers")
        elif min_value > max_value:
            errors.append("Minimum value must be less than or equal to maximum value")

        return errors
