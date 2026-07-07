"""Entity Integration & Export API routes - Issue #60"""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel

from ..models import EntityType, ExportResponse, ImportResponse


class ExternalImportRequest(BaseModel):
    format: str = "json"
    entities: dict | None = None
    csv_content: str | None = None
    entity_type: str | None = None
    duplicate_strategy: str = "skip"
    validate_before_import: bool = True
    continue_on_error: bool = True
    notify_webhooks: bool = False
from ..domain.entities.services import get_entity_repository  # noqa: E402 — after model definitions it depends on

router = APIRouter(prefix="/api/entities", tags=["entity-integration"])


def _parse_csv_entities(
    csv_content: str, entity_type: EntityType
) -> list[dict[str, Any]]:
    """Parse entities from CSV content"""
    entities = []
    reader = csv.DictReader(io.StringIO(csv_content))

    for row in reader:
        entity = {"canonical_name": row.get("canonical_name", "").strip()}

        # Parse aliases (pipe-separated)
        if "aliases" in row and row["aliases"]:
            entity["aliases"] = [a.strip() for a in row["aliases"].split("|")]

        # Add type-specific fields
        if entity_type == EntityType.PERSON:
            if "email" in row:
                entity["email"] = row["email"].strip()
            if "title" in row:
                entity["titles"] = [row["title"].strip()]
            if "department" in row:
                entity["departments"] = [row["department"].strip()]

        elif entity_type == EntityType.PROJECT:
            if "status" in row:
                entity["status"] = row["status"].strip()
            if "teams" in row:
                entity["teams"] = [t.strip() for t in row["teams"].split("|")]

        elif entity_type == EntityType.TEAM:
            if "department" in row:
                entity["department"] = row["department"].strip()
            if "lead" in row:
                entity["lead"] = row["lead"].strip()

        entities.append(entity)

    return entities


def _validate_import_entity(
    entity: dict[str, Any], entity_type: str
) -> list[dict[str, Any]]:
    """Validate entity data before import"""
    errors = []

    # Check required fields
    if not entity.get("canonical_name"):
        errors.append(
            {"field": "canonical_name", "message": "Canonical name is required"}
        )

    # Type-specific validation
    if entity_type == "person":
        email = entity.get("email")
        if email and "@" not in email:
            errors.append(
                {"field": "email", "message": f"Invalid email format: {email}"}
            )

    return errors


async def _send_webhook(webhook_data: dict[str, Any]):
    """Send webhook notification for entity events"""
    # TODO: Implement actual webhook sending
    pass


async def _publish_event(event: dict[str, Any]):
    """Publish event for entity changes"""
    # TODO: Implement actual event publishing
    pass


@router.post("/import", response_model=ImportResponse)
async def import_entities(request: ExternalImportRequest = Body(...)):
    """Import entities from external source"""
    registry = get_entity_repository()

    imported = {"people": 0, "projects": 0, "teams": 0}
    total_imported = 0
    merged = 0
    failed = 0
    errors = []
    validation_errors = []

    # Parse entities based on format
    entities_to_import = {"people": [], "projects": [], "teams": []}

    if request.format == "json":
        if not request.entities:
            raise HTTPException(
                status_code=422, detail="No entities provided for JSON import"
            )
        entities_to_import = request.entities

    elif request.format == "csv":
        if not request.csv_content or not request.entity_type:
            raise HTTPException(
                status_code=422,
                detail="CSV content and entity_type required for CSV import",
            )

        # Parse CSV
        parsed_entities = _parse_csv_entities(request.csv_content, request.entity_type)

        # Map to correct collection
        if request.entity_type == EntityType.PERSON:
            entities_to_import["people"] = parsed_entities
        elif request.entity_type == EntityType.PROJECT:
            entities_to_import["projects"] = parsed_entities
        elif request.entity_type == EntityType.TEAM:
            entities_to_import["teams"] = parsed_entities

    # Process each entity type
    for entity_type, entities in entities_to_import.items():
        for entity_data in entities:
            try:
                # Validate if requested
                if request.validate_before_import:
                    validation_errors_for_entity = _validate_import_entity(
                        entity_data, entity_type[:-1]
                    )
                    if validation_errors_for_entity:
                        validation_errors.append(
                            {
                                "entity": entity_data.get("canonical_name", "Unknown"),
                                "errors": validation_errors_for_entity,
                            }
                        )
                        if not request.continue_on_error:
                            break
                        continue

                # Check for duplicates
                existing = registry.get_canonical_entity(
                    entity_data.get("canonical_name", "")
                )

                if existing:
                    if request.duplicate_strategy == "skip":
                        continue
                    elif request.duplicate_strategy == "merge":
                        # Update existing entity
                        # TODO: Implement actual merge logic
                        merged += 1
                        continue

                # Register new entity
                if entity_type == "people":
                    registry.register_person(
                        canonical_name=entity_data["canonical_name"],
                        aliases=entity_data.get("aliases", []),
                        email=entity_data.get("email"),
                        titles=entity_data.get("titles", []),
                        departments=entity_data.get("departments", []),
                    )
                    imported["people"] += 1

                elif entity_type == "projects":
                    registry.register_project(
                        canonical_name=entity_data["canonical_name"],
                        aliases=entity_data.get("aliases", []),
                        status=entity_data.get("status", "active"),
                        teams=entity_data.get("teams", []),
                    )
                    imported["projects"] += 1

                elif entity_type == "teams":
                    registry.register_team(
                        canonical_name=entity_data["canonical_name"],
                        aliases=entity_data.get("aliases", []),
                        department=entity_data.get("department"),
                        lead=entity_data.get("lead"),
                    )
                    imported["teams"] += 1

                total_imported += 1

                # Send webhook if requested
                if request.notify_webhooks:
                    await _send_webhook(
                        {
                            "event_type": "entity.created",
                            "entity_type": entity_type[:-1],
                            "entity_name": entity_data["canonical_name"],
                        }
                    )

            except Exception as e:
                failed += 1
                errors.append(
                    {
                        "entity": entity_data.get("canonical_name", "Unknown"),
                        "error": str(e),
                    }
                )
                if not request.continue_on_error:
                    break

    success = failed == 0 and len(validation_errors) == 0

    return ImportResponse(
        success=success,
        imported=imported,
        total_imported=total_imported,
        merged=merged,
        failed=failed,
        errors=errors,
        validation_errors=validation_errors,
    )


@router.get("/export")
async def export_entities(
    export_format: str = Query(
        default="json", description="Export format: json, csv, xlsx"
    ),
    entity_types: list[str] | None = Query(
        default=None, description="Entity types to export"
    ),
    include_relationships: bool = Query(
        default=False, description="Include relationship data"
    ),
    filter_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    filter_department: str | None = Query(default=None),
):
    """Export entities in various formats"""
    registry = get_entity_repository()

    # Get all entities
    all_entities = registry.get_all_entities()

    # Filter by entity types
    if entity_types:
        filtered_entities = {}
        for entity_type_str in entity_types:
            try:
                entity_type = EntityType(entity_type_str)
                if entity_type == EntityType.PERSON:
                    filtered_entities["people"] = all_entities.get("people", {})
                elif entity_type == EntityType.PROJECT:
                    filtered_entities["projects"] = all_entities.get("projects", {})
                elif entity_type == EntityType.TEAM:
                    filtered_entities["teams"] = all_entities.get("teams", {})
            except ValueError:
                # Skip invalid entity types
                continue
    else:
        filtered_entities = all_entities

    # Apply filters
    export_data = {"people": {}, "projects": {}, "teams": {}}
    filters_applied = []

    for entity_type, entities in filtered_entities.items():
        for entity_id, entity in entities.items():
            # Confidence filter
            if filter_confidence and entity.confidence < filter_confidence:
                continue

            # Department filter (for people and teams)
            if filter_department:
                if (
                    hasattr(entity, "departments")
                    and filter_department not in entity.departments
                ):
                    continue
                if (
                    hasattr(entity, "department")
                    and entity.department != filter_department
                ):
                    continue

            export_data[entity_type][entity_id] = entity

    if filter_confidence:
        filters_applied.append(f"confidence >= {filter_confidence}")
    if filter_department:
        filters_applied.append(f"department = {filter_department}")

    # Count total entities
    total_entities = sum(len(entities) for entities in export_data.values())

    # Format response based on export format
    if export_format == "json":
        # Convert entities to dict format
        export_entities = {}
        for entity_type, entities in export_data.items():
            export_entities[entity_type] = {
                entity_id: entity.model_dump() for entity_id, entity in entities.items()
            }

        return ExportResponse(
            format="json",
            entities=export_entities,
            export_metadata={
                "timestamp": datetime.utcnow().isoformat(),
                "total_entities": total_entities,
                "filters_applied": filters_applied,
            },
        )

    elif export_format == "csv":
        # Create CSV for each entity type (return people by default)
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(
            [
                "entity_id",
                "canonical_name",
                "aliases",
                "confidence",
                "email",
                "titles",
                "departments",
                "created_at",
                "last_seen",
            ]
        )

        # Write people data
        for entity_id, person in export_data.get("people", {}).items():
            writer.writerow(
                [
                    entity_id,
                    person.canonical_name,
                    "|".join(person.aliases),
                    person.confidence,
                    person.email or "",
                    "|".join(person.titles) if hasattr(person, "titles") else "",
                    "|".join(person.departments)
                    if hasattr(person, "departments")
                    else "",
                    person.created_at.isoformat(),
                    person.last_seen.isoformat(),
                ]
            )

        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=entities_export.csv"},
        )

    elif export_format == "xlsx":
        # Create Excel workbook
        wb = Workbook()

        # Remove default sheet
        wb.remove(wb.active)

        # Create sheets for each entity type
        for entity_type, entities in export_data.items():
            if not entities:
                continue

            ws = wb.create_sheet(title=entity_type.capitalize())

            # Write headers based on entity type
            if entity_type == "people":
                headers = [
                    "ID",
                    "Name",
                    "Aliases",
                    "Email",
                    "Titles",
                    "Departments",
                    "Confidence",
                ]
                ws.append(headers)

                for entity_id, person in entities.items():
                    ws.append(
                        [
                            entity_id,
                            person.canonical_name,
                            ", ".join(person.aliases),
                            person.email or "",
                            ", ".join(person.titles)
                            if hasattr(person, "titles")
                            else "",
                            ", ".join(person.departments)
                            if hasattr(person, "departments")
                            else "",
                            person.confidence,
                        ]
                    )

            elif entity_type == "projects":
                headers = ["ID", "Name", "Aliases", "Status", "Teams", "Confidence"]
                ws.append(headers)

                for entity_id, project in entities.items():
                    ws.append(
                        [
                            entity_id,
                            project.canonical_name,
                            ", ".join(project.aliases),
                            project.status,
                            ", ".join(project.teams),
                            project.confidence,
                        ]
                    )

            elif entity_type == "teams":
                headers = [
                    "ID",
                    "Name",
                    "Aliases",
                    "Department",
                    "Lead",
                    "Members",
                    "Confidence",
                ]
                ws.append(headers)

                for entity_id, team in entities.items():
                    ws.append(
                        [
                            entity_id,
                            team.canonical_name,
                            ", ".join(team.aliases),
                            team.department or "",
                            team.lead or "",
                            len(team.members),
                            team.confidence,
                        ]
                    )

        # Save to bytes buffer
        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)

        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=entities_export.xlsx"
            },
        )

    else:
        raise HTTPException(
            status_code=400, detail=f"Unsupported export format: {export_format}"
        )
