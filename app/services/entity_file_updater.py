"""
Service for updating entity markdown files with meeting information.

Handles parsing, updating, and rebuilding entity files while preserving
existing content and maintaining proper format.
"""

import logging
import re
from datetime import datetime
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Valid entity types
VALID_ENTITY_TYPES = {"person", "project", "team"}

# Maximum sizes for accumulating sections
MAX_ACHIEVEMENTS = 10
MAX_RELATIONSHIPS = 50
MAX_BLOCKERS = 20
MAX_COLLABORATIONS = 30


class EntityFileUpdater:
    """Updates entity markdown files with meeting-derived information."""

    def __init__(self, max_meeting_refs: int = 50):
        """Initialize the updater with configuration."""
        self.max_meeting_refs = max_meeting_refs

    def detect_entity_type_from_id(self, entity_id: str) -> str | None:
        """Detect entity type from entity ID prefix.

        Args:
            entity_id: Entity identifier (e.g., 'person-john-doe', 'project-alpha', 'team-engineering')

        Returns:
            Entity type if detected, None otherwise
        """
        if entity_id.startswith("project-"):
            return "project"
        elif entity_id.startswith("team-"):
            return "team"
        elif entity_id.startswith("person-"):
            return "person"
        return None

    def validate_entity_type(self, entity_type: str) -> str:
        """Validate and normalize entity type.

        Args:
            entity_type: The entity type to validate

        Returns:
            Valid entity type, defaults to 'person' if invalid
        """
        if entity_type not in VALID_ENTITY_TYPES:
            logger.warning(
                f"Unknown entity type: {entity_type}, defaulting to 'person'"
            )
            return "person"
        return entity_type

    def parse_entity_sections(
        self, content: str, entity_type: str | None = None
    ) -> dict[str, Any]:
        """Parse entity markdown content into structured sections.

        Args:
            content: The markdown content to parse
            entity_type: Optional entity type ('person', 'project', 'team') to optimize parsing

        Returns:
            Dictionary of parsed sections
        """
        # Detect entity type from frontmatter if not provided
        if not entity_type:
            frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if frontmatter_match:
                try:
                    fm = yaml.safe_load(frontmatter_match.group(1))
                    entity_type = fm.get("type", "person")
                except yaml.YAMLError:
                    entity_type = "person"

        # Initialize sections based on entity type
        if entity_type == "project":
            sections = {
                "frontmatter": {},
                "current_status": "",
                "meeting_participation": [],
                "active_milestones": [],
                "dependencies_blockers": "",
                "team_updates": "",
                "raw_sections": {},
            }
        elif entity_type == "team":
            sections = {
                "frontmatter": {},
                "current_focus": "",
                "meeting_participation": [],
                "active_goals": [],
                "team_achievements": "",
                "cross_team_collaborations": "",
                "raw_sections": {},
            }
        else:  # person
            sections = {
                "frontmatter": {},
                "current_role": "",
                "meeting_participation": [],
                "active_commitments": [],
                "relationships": [],
                "raw_sections": {},
            }

        # Extract frontmatter
        frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if frontmatter_match:
            try:
                sections["frontmatter"] = yaml.safe_load(frontmatter_match.group(1))
            except yaml.YAMLError as e:
                logger.error(f"Error parsing frontmatter: {e}")
                raise Exception(f"Failed to parse frontmatter: {e}")

        # Extract sections
        section_pattern = r"## ([^\n]+)\n(.*?)(?=\n## |$)"
        matches = re.findall(section_pattern, content, re.DOTALL)

        for section_name, section_content in matches:
            sections["raw_sections"][section_name] = section_content.strip()

            if (
                section_name == "Current Role & Responsibilities"
                and "current_role" in sections
            ):
                sections["current_role"] = section_content.strip()
            elif section_name == "Current Status" and "current_status" in sections:
                sections["current_status"] = section_content.strip()
            elif section_name == "Current Focus" and "current_focus" in sections:
                sections["current_focus"] = section_content.strip()

            elif section_name == "Meeting Participation":
                # Parse meeting subsections
                meeting_pattern = r"### ([^\n]+)\n(.*?)(?=\n### |$)"
                meeting_matches = re.findall(
                    meeting_pattern, section_content, re.DOTALL
                )
                for meeting_title, meeting_content in meeting_matches:
                    sections["meeting_participation"].append(
                        {"title": meeting_title, "content": meeting_content.strip()}
                    )

            elif (
                section_name == "Active Commitments"
                and "active_commitments" in sections
            ):
                # Parse commitments - support both checkbox and simple list formats
                commitment_lines = section_content.strip().split("\n")
                for line in commitment_lines:
                    if line.strip().startswith("- "):
                        # Try checkbox format first (e.g., "- [ ] text (Due: date)")
                        completed = "[✓]" in line
                        text_match = re.search(
                            r"- \[[ ✓]\] (.+?) \(Due: ([^)]+)\)", line
                        )
                        if text_match:
                            sections["active_commitments"].append(
                                {
                                    "text": text_match.group(1),
                                    "due_date": text_match.group(2),
                                    "status": "completed" if completed else "pending",
                                }
                            )
                        else:
                            # Fallback to simple format (e.g., "- text (Due: date)")
                            simple_match = re.search(r"- (.+?) \(Due: ([^)]+)\)", line)
                            if simple_match:
                                sections["active_commitments"].append(
                                    {
                                        "text": simple_match.group(1),
                                        "due_date": simple_match.group(2),
                                        "status": "pending",
                                    }
                                )

            elif (
                section_name == "Active Milestones" and "active_milestones" in sections
            ):
                # Parse milestones - support both checkbox and simple list formats
                milestone_lines = section_content.strip().split("\n")
                for line in milestone_lines:
                    if line.strip().startswith("- "):
                        # Try checkbox format first
                        completed = "[✓]" in line
                        text_match = re.search(
                            r"- \[[ ✓]\] (.+?) \(Due: ([^)]+)\)", line
                        )
                        if text_match:
                            sections["active_milestones"].append(
                                {
                                    "text": text_match.group(1),
                                    "due_date": text_match.group(2),
                                    "status": "completed" if completed else "pending",
                                }
                            )
                        else:
                            # Fallback to simple format
                            simple_match = re.search(r"- (.+?) \(Due: ([^)]+)\)", line)
                            if simple_match:
                                sections["active_milestones"].append(
                                    {
                                        "text": simple_match.group(1),
                                        "due_date": simple_match.group(2),
                                        "status": "pending",
                                    }
                                )

            elif section_name == "Active Goals" and "active_goals" in sections:
                # Parse goals - support both checkbox and simple list formats
                goal_lines = section_content.strip().split("\n")
                for line in goal_lines:
                    if line.strip().startswith("- "):
                        # Try checkbox format first
                        completed = "[✓]" in line
                        text_match = re.search(
                            r"- \[[ ✓]\] (.+?) \(Due: ([^)]+)\)", line
                        )
                        if text_match:
                            sections["active_goals"].append(
                                {
                                    "text": text_match.group(1),
                                    "due_date": text_match.group(2),
                                    "status": "completed" if completed else "pending",
                                }
                            )
                        else:
                            # Fallback to simple format
                            simple_match = re.search(r"- (.+?) \(Due: ([^)]+)\)", line)
                            if simple_match:
                                sections["active_goals"].append(
                                    {
                                        "text": simple_match.group(1),
                                        "due_date": simple_match.group(2),
                                        "status": "pending",
                                    }
                                )

            elif section_name == "Relationships" and "relationships" in sections:
                # Parse relationships
                relationship_lines = section_content.strip().split("\n")
                for line in relationship_lines:
                    if line.strip().startswith("- "):
                        # Remove the '- ' prefix
                        rel_text = line.strip()[2:]

                        # Parse different formats:
                        # - "Works closely with: Rachel Green (Legal Counsel)"
                        # - "Reports to: Senior Partners"
                        # - "Rachel Green (Legal Counsel)"

                        if ":" in rel_text:
                            # Format with relationship type prefix
                            parts = rel_text.split(":", 1)
                            if len(parts) == 2:
                                rel_entity = parts[1].strip()
                                sections["relationships"].append(rel_entity)
                        else:
                            # Direct format
                            sections["relationships"].append(rel_text)

            elif (
                section_name == "Dependencies & Blockers"
                and "dependencies_blockers" in sections
            ):
                sections["dependencies_blockers"] = section_content.strip()

            elif section_name == "Team Updates" and "team_updates" in sections:
                sections["team_updates"] = section_content.strip()

            elif (
                section_name == "Team Achievements" and "team_achievements" in sections
            ):
                sections["team_achievements"] = section_content.strip()

            elif (
                section_name == "Cross-Team Collaborations"
                and "cross_team_collaborations" in sections
            ):
                sections["cross_team_collaborations"] = section_content.strip()

        return sections

    def format_meeting_section(self, meeting_data: dict[str, Any]) -> str:
        """Format meeting data into a markdown section."""
        lines = []

        # Meeting title and date
        title = meeting_data.get("meeting_title", "Meeting")
        date = meeting_data.get("date", datetime.now().strftime("%Y-%m-%d"))
        lines.append(f"### {title} ({date})")

        # Role
        if meeting_data.get("role"):
            lines.append(f"**Role**: {meeting_data['role']}")

        # Key contributions
        if meeting_data.get("key_contributions"):
            lines.append("**Key Contributions**:")
            for contribution in meeting_data["key_contributions"]:
                lines.append(f"- {contribution}")

        # Commitments made
        if meeting_data.get("commitments"):
            lines.append("\n**Commitments Made**:")
            for commitment in meeting_data["commitments"]:
                text = commitment.get("text") or commitment.get("commitment")
                due_date = commitment.get("due_date", "TBD")
                lines.append(f"- {text} (Due: {due_date})")

        # Decisions participated in
        if meeting_data.get("decisions"):
            lines.append("\n**Decisions Participated In**:")
            for decision in meeting_data["decisions"]:
                decision_text = decision.get("decision")
                lines.append(f"- {decision_text}")

        # Key insights
        if meeting_data.get("insights"):
            lines.append("\n**Key Insights**:")
            for insight in meeting_data["insights"]:
                if isinstance(insight, str):
                    lines.append(f"- {insight}")
                else:
                    lines.append(f"- {insight.get('insight', insight)}")

        return "\n".join(lines)

    def format_project_meeting_section(self, meeting_data: dict[str, Any]) -> str:
        """Format meeting data into a markdown section for project entities."""
        lines = []

        # Meeting title and date
        title = meeting_data.get("meeting_title", "Meeting")
        date = meeting_data.get("date", datetime.now().strftime("%Y-%m-%d"))
        lines.append(f"### {title} ({date})")

        # Status update
        if meeting_data.get("status_update"):
            lines.append(f"**Status Update**: {meeting_data['status_update']}")

        # Milestones
        if meeting_data.get("milestones"):
            lines.append("\n**Milestones**:")
            for milestone in meeting_data["milestones"]:
                text = milestone.get("text")
                due_date = milestone.get("due_date", "TBD")
                status = milestone.get("status", "pending")

                status_text = ""
                if status == "completed":
                    status_text = " - COMPLETED"
                    if completion_date := milestone.get("completion_date"):
                        status_text += f" ({completion_date})"
                elif status == "in_progress":
                    status_text = " - IN PROGRESS"
                    if progress := milestone.get("progress"):
                        status_text += f" ({progress})"

                lines.append(f"- {text} (Due: {due_date}){status_text}")

        # Blockers
        if meeting_data.get("blockers"):
            lines.append("\n**Blockers Identified**:")
            for blocker in meeting_data["blockers"]:
                lines.append(f"- {blocker}")

        # Decisions
        if meeting_data.get("decisions"):
            lines.append("\n**Decisions Made**:")
            for decision in meeting_data["decisions"]:
                lines.append(f"- {decision.get('decision')}")

        # Team updates
        if meeting_data.get("team_updates"):
            lines.append("\n**Team Updates**:")
            for team, update in meeting_data["team_updates"].items():
                lines.append(f"- {team}: {update}")

        # Timeline changes
        if meeting_data.get("timeline_changes"):
            lines.append("\n**Timeline Update**:")
            tc = meeting_data["timeline_changes"]
            if tc.get("original_end") and tc.get("projected_end"):
                lines.append(
                    f"Projected completion: {tc['projected_end']} (originally {tc['original_end']})"
                )
            if tc.get("reason"):
                lines.append(f"Reason: {tc['reason']}")

        return "\n".join(lines)

    def format_team_meeting_section(self, meeting_data: dict[str, Any]) -> str:
        """Format meeting data into a markdown section for team entities."""
        lines = []

        # Meeting title and date
        title = meeting_data.get("meeting_title", "Meeting")
        date = meeting_data.get("date", datetime.now().strftime("%Y-%m-%d"))
        lines.append(f"### {title} ({date})")

        # Achievements
        if meeting_data.get("achievements"):
            lines.append("**Achievements**:")
            for achievement in meeting_data["achievements"]:
                lines.append(f"- {achievement}")

        # Goals
        if meeting_data.get("goals"):
            lines.append("\n**Goals Set**:")
            for goal in meeting_data["goals"]:
                text = goal.get("text")
                due_date = goal.get("due_date", "TBD")
                assigned = goal.get("assigned_to", "")

                goal_text = f"- {text} (Due: {due_date})"
                if assigned:
                    goal_text += f" - Assigned to: {assigned}"
                lines.append(goal_text)

        # Team commitments
        if meeting_data.get("team_commitments"):
            lines.append("\n**Team Commitments**:")
            for commitment in meeting_data["team_commitments"]:
                lines.append(f"- {commitment}")

        # Performance metrics
        if meeting_data.get("performance_metrics"):
            lines.append("\n**Performance Metrics**:")
            for metric, value in meeting_data["performance_metrics"].items():
                formatted_metric = metric.replace("_", " ").title()
                lines.append(f"- {formatted_metric}: {value}")

        # Cross-team collaborations
        if meeting_data.get("collaborations"):
            lines.append("\n**Cross-Team Collaborations**:")
            for collab in meeting_data["collaborations"]:
                lines.append(f"- {collab}")

        return "\n".join(lines)

    def _merge_items_with_deduplication(
        self,
        existing: list[dict[str, Any]],
        new: list[dict[str, Any]],
        text_field: str = "text",
        additional_fields: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generic method to merge items with deduplication based on text field.

        Args:
            existing: List of existing items
            new: List of new items to merge
            text_field: Field name to use for deduplication
            additional_fields: Additional fields to include in merged items

        Returns:
            Merged list with duplicates removed
        """
        # Create a set of existing texts for deduplication
        existing_texts = set()
        for item in existing:
            if text := item.get(text_field):
                existing_texts.add(text)

        merged = existing.copy()

        for item in new:
            text = item.get(text_field) or item.get(
                "commitment"
            )  # Handle legacy 'commitment' field
            if text and text not in existing_texts:
                merged_item = {
                    "text": text,
                    "due_date": item.get("due_date", "TBD"),
                    "status": item.get("status", "pending"),
                }

                # Add any additional fields
                if additional_fields:
                    for field, default in additional_fields.items():
                        if field in item:
                            merged_item[field] = item[field]
                        elif default is not None:
                            merged_item[field] = default

                merged.append(merged_item)
                existing_texts.add(text)

        return merged

    def merge_commitments(
        self, existing: list[dict[str, Any]], new: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge new commitments with existing ones, avoiding duplicates."""
        return self._merge_items_with_deduplication(existing, new)

    def merge_milestones(
        self, existing: list[dict[str, Any]], new: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge new milestones with existing ones, avoiding duplicates."""
        additional_fields = {"progress": None, "completion_date": None}
        return self._merge_items_with_deduplication(
            existing, new, additional_fields=additional_fields
        )

    def merge_goals(
        self, existing: list[dict[str, Any]], new: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge new goals with existing ones, avoiding duplicates."""
        additional_fields = {"assigned_to": None}
        return self._merge_items_with_deduplication(
            existing, new, additional_fields=additional_fields
        )

    def update_relationships(
        self, existing: dict[str, str], new: list[str]
    ) -> dict[str, str]:
        """Update relationships dictionary with new relationships."""
        updated = existing.copy()

        for relationship in new:
            # Parse relationship format "Name (Role)" or just "Name"
            match = re.match(r"^(.+?)(?:\s*\((.+?)\))?$", relationship)
            if match:
                name = match.group(1).strip()
                role = match.group(2).strip() if match.group(2) else ""

                # Only add if not already present or update role
                if name not in updated or (role and not updated[name]):
                    updated[name] = role

        return updated

    def update_entity_with_meeting(
        self,
        sections: dict[str, Any],
        meeting_data: dict[str, Any],
        entity_type: str | None = None,
    ) -> str:
        """Update entity sections with meeting data and rebuild file."""
        # Detect entity type from frontmatter or meeting data
        if entity_type is None:
            entity_type = meeting_data.get("entity_type") or sections[
                "frontmatter"
            ].get("type", "person")

        # Validate entity type
        entity_type = self.validate_entity_type(entity_type)

        logger.info(
            f"Updating {entity_type} entity from meeting {meeting_data.get('meeting_id', 'unknown')}"
        )

        # Update frontmatter
        if "meetings" not in sections["frontmatter"]:
            sections["frontmatter"]["meetings"] = []

        sections["frontmatter"]["meetings"].append(meeting_data["meeting_id"])
        sections["frontmatter"]["meetings"] = self.limit_meeting_references(
            sections["frontmatter"]["meetings"]
        )
        sections["frontmatter"]["last_updated"] = datetime.now().isoformat() + "Z"

        # Add meeting section based on entity type
        if entity_type == "project":
            new_meeting_section = self.format_project_meeting_section(meeting_data)
        elif entity_type == "team":
            new_meeting_section = self.format_team_meeting_section(meeting_data)
        else:
            new_meeting_section = self.format_meeting_section(meeting_data)

        sections["meeting_participation"].insert(0, {"content": new_meeting_section})

        # Handle entity-specific updates
        if entity_type == "project":
            # Merge milestones
            if "active_milestones" in sections:
                sections["active_milestones"] = self.merge_milestones(
                    sections["active_milestones"], meeting_data.get("milestones", [])
                )

            # Update status
            if meeting_data.get("status_update") and "current_status" in sections:
                sections["current_status"] = meeting_data["status_update"]

            # Update blockers with size limit
            if meeting_data.get("blockers") and "dependencies_blockers" in sections:
                existing_blockers = (
                    sections["dependencies_blockers"].split("\n")
                    if sections["dependencies_blockers"]
                    else []
                )
                new_blockers = ["- " + b for b in meeting_data["blockers"]]
                all_blockers = list(set(existing_blockers + new_blockers))
                # Limit to MAX_BLOCKERS most recent
                if len(all_blockers) > MAX_BLOCKERS:
                    all_blockers = all_blockers[-MAX_BLOCKERS:]
                    logger.info(f"Limiting blockers to {MAX_BLOCKERS} items")
                sections["dependencies_blockers"] = "\n".join(all_blockers)

            # Update team updates
            if meeting_data.get("team_updates") and "team_updates" in sections:
                sections["team_updates"] = self._format_team_updates(
                    meeting_data["team_updates"]
                )

        elif entity_type == "team":
            # Merge goals
            if "active_goals" in sections:
                sections["active_goals"] = self.merge_goals(
                    sections["active_goals"], meeting_data.get("goals", [])
                )

            # Update achievements with size limit
            if meeting_data.get("achievements") and "team_achievements" in sections:
                existing = (
                    sections["team_achievements"].split("\n")
                    if sections["team_achievements"]
                    else []
                )
                new_achievements = ["- " + a for a in meeting_data["achievements"]]
                all_achievements = existing + new_achievements
                sections["team_achievements"] = "\n".join(
                    all_achievements[-MAX_ACHIEVEMENTS:]
                )  # Keep last MAX_ACHIEVEMENTS

            # Update collaborations with size limit
            if (
                meeting_data.get("collaborations")
                and "cross_team_collaborations" in sections
            ):
                existing = (
                    sections["cross_team_collaborations"].split("\n")
                    if sections["cross_team_collaborations"]
                    else []
                )
                new_collabs = ["- " + c for c in meeting_data["collaborations"]]
                all_collabs = list(set(existing + new_collabs))
                # Limit to MAX_COLLABORATIONS
                if len(all_collabs) > MAX_COLLABORATIONS:
                    all_collabs = all_collabs[-MAX_COLLABORATIONS:]
                    logger.info(
                        f"Limiting collaborations to {MAX_COLLABORATIONS} items"
                    )
                sections["cross_team_collaborations"] = "\n".join(all_collabs)

        else:  # person
            # Merge commitments
            sections["active_commitments"] = self.merge_commitments(
                sections["active_commitments"], meeting_data.get("commitments", [])
            )

            # Update relationships
            if meeting_data.get("relationships"):
                # Convert existing relationships to dict
                existing_rels = {}
                for rel in sections["relationships"]:
                    match = re.match(r"^(.+?)(?:\s*\((.+?)\))?$", rel)
                    if match:
                        name = match.group(1).strip()
                        role = match.group(2).strip() if match.group(2) else ""
                        existing_rels[name] = role

                # Update with new relationships
                updated_rels = self.update_relationships(
                    existing_rels, meeting_data["relationships"]
                )

                # Convert back to list format
                sections["relationships"] = [
                    f"{name} ({role})" if role else name
                    for name, role in updated_rels.items()
                ]

                # Limit relationships to prevent unbounded growth
                if len(sections["relationships"]) > MAX_RELATIONSHIPS:
                    sections["relationships"] = sections["relationships"][
                        :MAX_RELATIONSHIPS
                    ]
                    logger.info(f"Limiting relationships to {MAX_RELATIONSHIPS} items")

        # Rebuild the file
        return self.rebuild_entity_file(sections, entity_type)

    def rebuild_entity_file(
        self, sections: dict[str, Any], entity_type: str = "person"
    ) -> str:
        """Rebuild entity file from sections based on entity type."""
        lines = []

        # Frontmatter
        lines.append("---")
        for key, value in sections["frontmatter"].items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append("")

        # Entity name as title
        entity_name = sections["frontmatter"].get("name", "Entity")
        lines.append(f"# {entity_name}")
        lines.append("")

        # Entity-specific sections based on type
        if entity_type == "project":
            # Current Status section
            if sections.get("current_status"):
                lines.append("## Current Status")
                lines.append(sections["current_status"])
                lines.append("")

            # Recent Developments section
            if sections.get("recent_developments"):
                lines.append("## Recent Developments")
                for item in sections["recent_developments"]:
                    lines.append(f"- {item}")
                lines.append("")

            # Active Milestones section
            if sections.get("active_milestones"):
                lines.append("## Active Milestones")
                milestones_text = self.format_active_milestones(
                    sections["active_milestones"]
                )
                lines.append(milestones_text)
                lines.append("")

            # Blockers section
            if sections.get("blockers"):
                lines.append("## Blockers")
                for blocker in sections["blockers"]:
                    lines.append(f"- {blocker}")
                lines.append("")

            # Meeting Updates section
            if sections.get("meeting_participation"):
                lines.append("## Meeting Updates")
                lines.append("")
                for meeting in sections["meeting_participation"]:
                    if isinstance(meeting, dict) and "content" in meeting:
                        lines.append(meeting["content"])
                    else:
                        lines.append(str(meeting))
                    lines.append("")

        elif entity_type == "team":
            # Overview section
            if sections.get("overview"):
                lines.append("## Overview")
                lines.append(sections["overview"])
                lines.append("")

            # Recent Updates section
            if sections.get("recent_updates"):
                lines.append("## Recent Updates")
                for update in sections["recent_updates"]:
                    lines.append(f"- {update}")
                lines.append("")

            # Active Goals section
            if sections.get("active_goals"):
                lines.append("## Active Goals")
                goals_text = self.format_active_goals(sections["active_goals"])
                lines.append(goals_text)
                lines.append("")

            # Achievements section
            if sections.get("achievements"):
                lines.append("## Achievements")
                for achievement in sections["achievements"]:
                    lines.append(f"- {achievement}")
                lines.append("")

            # Meeting Notes section
            if sections.get("meeting_participation"):
                lines.append("## Meeting Notes")
                lines.append("")
                for meeting in sections["meeting_participation"]:
                    if isinstance(meeting, dict) and "content" in meeting:
                        lines.append(meeting["content"])
                    else:
                        lines.append(str(meeting))
                    lines.append("")

            # Collaborations section
            if sections.get("collaborations"):
                lines.append("## Collaborations")
                for collab in sections["collaborations"]:
                    lines.append(f"- {collab}")
                lines.append("")

        else:  # Default to person entity structure
            # Current role section
            if sections.get("current_role"):
                lines.append("## Current Role & Responsibilities")
                lines.append(sections["current_role"])
                lines.append("")

            # Meeting participation section
            if sections.get("meeting_participation"):
                lines.append("## Meeting Participation")
                lines.append("")
                for meeting in sections["meeting_participation"]:
                    if isinstance(meeting, dict) and "content" in meeting:
                        lines.append(meeting["content"])
                    else:
                        lines.append(str(meeting))
                    lines.append("")

            # Active commitments section
            if sections.get("active_commitments"):
                lines.append("## Active Commitments")
                commitments_text = self.format_active_commitments(
                    sections["active_commitments"]
                )
                lines.append(commitments_text)
                lines.append("")

            # Relationships section
            if sections.get("relationships"):
                lines.append("## Relationships")
                for relationship in sections["relationships"]:
                    lines.append(f"- {relationship}")
                lines.append("")

        # Add any other raw sections that weren't parsed
        for section_name, content in sections.get("raw_sections", {}).items():
            if section_name not in [
                "Current Role & Responsibilities",
                "Meeting Participation",
                "Active Commitments",
                "Relationships",
            ]:
                lines.append(f"## {section_name}")
                lines.append(content)
                lines.append("")

        return "\n".join(lines)

    def format_active_commitments(self, commitments: list[dict[str, Any]]) -> str:
        """Format commitments list as markdown."""
        lines = []

        # Sort by due date and status
        sorted_commitments = sorted(
            commitments,
            key=lambda c: (
                0 if c["status"] == "pending" else 1,
                c.get("due_date", "9999-12-31"),
            ),
        )

        for commitment in sorted_commitments:
            checkbox = "[✓]" if commitment["status"] == "completed" else "[ ]"
            text = commitment["text"]
            due_date = commitment.get("due_date", "TBD")

            line = f"- {checkbox} {text} (Due: {due_date})"
            if commitment["status"] == "completed":
                line += " - COMPLETED"

            lines.append(line)

        return "\n".join(lines) if lines else "- No active commitments"

    def format_active_milestones(self, milestones: list[dict[str, Any]]) -> str:
        """Format milestones list as markdown."""
        lines = []

        # Sort by due date and status
        sorted_milestones = sorted(
            milestones,
            key=lambda m: (
                0
                if m["status"] == "pending"
                else 1
                if m["status"] == "in_progress"
                else 2,
                m.get("due_date", "9999-12-31"),
            ),
        )

        for milestone in sorted_milestones:
            checkbox = "[✓]" if milestone["status"] == "completed" else "[ ]"
            text = milestone["text"]
            due_date = milestone.get("due_date", "TBD")

            line = f"- {checkbox} {text} (Due: {due_date})"
            if milestone["status"] == "completed":
                line += " - COMPLETED"
            elif milestone["status"] == "in_progress":
                line += " - IN PROGRESS"
                if milestone.get("progress"):
                    line += f" ({milestone['progress']})"

            lines.append(line)

        return "\n".join(lines) if lines else "- No active milestones"

    def format_active_goals(self, goals: list[dict[str, Any]]) -> str:
        """Format goals list as markdown."""
        lines = []

        # Sort by due date and status
        sorted_goals = sorted(
            goals,
            key=lambda g: (
                0 if g["status"] == "pending" else 1,
                g.get("due_date", "9999-12-31"),
            ),
        )

        for goal in sorted_goals:
            checkbox = "[✓]" if goal["status"] == "completed" else "[ ]"
            text = goal["text"]
            due_date = goal.get("due_date", "TBD")

            line = f"- {checkbox} {text} (Due: {due_date})"
            if goal["status"] == "completed":
                line += " - COMPLETED"
            if goal.get("assigned_to"):
                line += f" [Assigned: {goal['assigned_to']}]"

            lines.append(line)

        return "\n".join(lines) if lines else "- No active goals"

    def _format_team_updates(self, team_updates: dict[str, str]) -> str:
        """Format team updates dictionary into a readable string."""
        lines = []
        for team, update in team_updates.items():
            lines.append(f"- {team}: {update}")
        return "\n".join(lines)

    def _get_excluded_sections(self, entity_type: str) -> list[str]:
        """Get list of sections to exclude for each entity type."""
        if entity_type == "project":
            return [
                "Current Status",
                "Meeting Participation",
                "Active Milestones",
                "Dependencies & Blockers",
                "Team Updates",
            ]
        elif entity_type == "team":
            return [
                "Current Focus",
                "Meeting Participation",
                "Active Goals",
                "Team Achievements",
                "Cross-Team Collaborations",
            ]
        else:  # person
            return [
                "Current Role & Responsibilities",
                "Meeting Participation",
                "Active Commitments",
                "Relationships",
            ]

    def limit_meeting_references(self, meetings: list[str]) -> list[str]:
        """Limit the number of meeting references to prevent unbounded growth."""
        if len(meetings) <= self.max_meeting_refs:
            return meetings

        # Keep the most recent meetings
        return meetings[-self.max_meeting_refs :]

    def create_new_entity_file(
        self, entity_data: dict[str, Any], meeting_data: dict[str, Any]
    ) -> str:
        """Create a new entity file from scratch."""
        entity_type = entity_data.get("type", "person")

        # Initialize sections based on entity type
        if entity_type == "project":
            sections = {
                "frontmatter": {
                    "name": entity_data["name"],
                    "type": "project",
                    "status": entity_data.get("status", "active"),
                    "timeline": entity_data.get("timeline", ""),
                    "key_people": entity_data.get("key_people", []),
                    "teams": entity_data.get("teams", []),
                    "meetings": [meeting_data["meeting_id"]],
                    "last_updated": datetime.now().isoformat() + "Z",
                },
                "current_status": entity_data.get("current_status", ""),
                "meeting_participation": [
                    {"content": self.format_project_meeting_section(meeting_data)}
                ],
                "active_milestones": meeting_data.get("milestones", []),
                "dependencies_blockers": "",
                "team_updates": "",
                "raw_sections": {},
            }
        elif entity_type == "team":
            sections = {
                "frontmatter": {
                    "name": entity_data["name"],
                    "type": "team",
                    "department": entity_data.get("department", ""),
                    "lead": entity_data.get("lead", ""),
                    "members": entity_data.get("members", []),
                    "meetings": [meeting_data["meeting_id"]],
                    "last_updated": datetime.now().isoformat() + "Z",
                },
                "current_focus": entity_data.get("current_focus", ""),
                "meeting_participation": [
                    {"content": self.format_team_meeting_section(meeting_data)}
                ],
                "active_goals": meeting_data.get("goals", []),
                "team_achievements": "",
                "cross_team_collaborations": "",
                "raw_sections": {},
            }
        else:  # person
            sections = {
                "frontmatter": {
                    "name": entity_data["name"],
                    "type": "person",
                    "meetings": [meeting_data["meeting_id"]],
                    "last_updated": datetime.now().isoformat() + "Z",
                },
                "current_role": entity_data.get("role", ""),
                "meeting_participation": [
                    {"content": self.format_meeting_section(meeting_data)}
                ],
                "active_commitments": meeting_data.get("commitments", []),
                "relationships": meeting_data.get("relationships", []),
                "raw_sections": {},
            }

        return self.rebuild_entity_file(sections)

    def validate_entity_file_format(
        self, content: str, entity_type: str | None = None
    ) -> bool:
        """Validate that entity file has proper format."""
        # Check for frontmatter
        if not re.match(r"^---\n.*?\n---\n", content, re.DOTALL):
            return False

        # Detect entity type if not provided
        if not entity_type:
            sections = self.parse_entity_sections(content)
            entity_type = sections["frontmatter"].get("type", "person")

        # Check for required sections based on entity type
        if entity_type == "project":
            required_sections = [
                "# ",  # Title
                "## Current Status",
                "## Meeting Participation",
            ]
        elif entity_type == "team":
            required_sections = [
                "# ",  # Title
                "## Current Focus",
                "## Meeting Participation",
            ]
        else:  # person
            required_sections = [
                "# ",  # Title
                "## Current Role & Responsibilities",
                "## Meeting Participation",
            ]

        return all(
            section in content for section in required_sections[:2]
        )  # At least title and one main section

    def extract_entity_metadata(self, content: str) -> dict[str, Any]:
        """Extract metadata summary from entity file."""
        sections = self.parse_entity_sections(content)

        return {
            "name": sections["frontmatter"].get("name"),
            "type": sections["frontmatter"].get("type"),
            "meetings_count": len(sections["frontmatter"].get("meetings", [])),
            "active_commitments_count": sum(
                1 for c in sections["active_commitments"] if c["status"] == "pending"
            ),
            "relationships_count": len(sections["relationships"]),
        }

    def detect_entity_type(self, meeting_data: dict[str, Any]) -> str:
        """Detect entity type from meeting data."""
        # Explicit entity type takes precedence
        if entity_type := meeting_data.get("entity_type"):
            return entity_type

        # Check for entity-specific fields
        if "milestones" in meeting_data or "status_update" in meeting_data:
            return "project"
        elif (
            "goals" in meeting_data
            or "achievements" in meeting_data
            or "team_commitments" in meeting_data
        ):
            return "team"
        else:
            # Default to person
            return "person"
