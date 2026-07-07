"""Service for persisting meeting analysis results"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.workflows.base import WorkflowResult

logger = logging.getLogger(__name__)


class AnalysisStorage:
    """Handles storage and retrieval of meeting analysis results"""

    def __init__(self, base_path: str = "repo"):
        self.base_path = Path(base_path)

    async def ensure_directories(self) -> None:
        """Create necessary directory structure"""
        directories = [
            "analysis/meetings",
            "analysis/commitments",
            "analysis/decisions",
            "analysis/patterns",
        ]

        for dir_path in directories:
            full_path = self.base_path / dir_path
            full_path.mkdir(parents=True, exist_ok=True)

    async def store_meeting_analysis(
        self, meeting_id: str, result: WorkflowResult
    ) -> None:
        """Store complete meeting analysis results"""
        # Validate meeting ID format to prevent path traversal
        import re

        if not re.match(r"^meeting-\d{8}-\d+$", meeting_id):
            raise ValueError(f"Invalid meeting_id format: {meeting_id}")

        await self.ensure_directories()

        # Extract data from workflow result
        analysis_data = {
            "meeting_id": meeting_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "workflow_id": result.workflow_id,
            "quality_score": result.metadata.get("quality_score", 0),
            "commitments": result.result.get("commitments", []),
            "decisions": result.result.get("decisions", []),
            "patterns": result.result.get("patterns", []),
            "entities": result.result.get("entities", {}),
            "insights": result.result.get("insights", []),
            "risks": result.result.get("risks", []),
            "tool_metrics": result.metadata.get("tool_metrics", {}),
        }

        # Store JSON analysis
        json_path = (
            self.base_path / "analysis" / "meetings" / f"{meeting_id}-analysis.json"
        )
        try:
            with open(json_path, "w") as f:
                json.dump(analysis_data, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to write analysis JSON for {meeting_id}: {e}")
            raise

        # Create markdown summary
        summary_path = (
            self.base_path / "analysis" / "meetings" / f"{meeting_id}-summary.md"
        )
        summary_content = self._create_markdown_summary(meeting_id, analysis_data)
        try:
            with open(summary_path, "w") as f:
                f.write(summary_content)
        except OSError as e:
            logger.error(f"Failed to write analysis summary for {meeting_id}: {e}")
            # Try to clean up JSON file since we're in inconsistent state
            try:
                json_path.unlink()
            except Exception:
                pass
            raise

        # Append to monthly logs
        await self._append_monthly_commitments(meeting_id, analysis_data["commitments"])
        await self._append_monthly_decisions(meeting_id, analysis_data["decisions"])
        await self._append_monthly_patterns(meeting_id, analysis_data["patterns"])

        logger.info(f"Stored analysis for {meeting_id}")

    def _create_markdown_summary(self, meeting_id: str, data: dict[str, Any]) -> str:
        """Create human-readable markdown summary"""
        lines = [
            f"# Meeting Analysis Summary: {meeting_id}",
            f"\nGenerated: {data['timestamp']}",
            f"Quality Score: {data['quality_score']:.2f}",
            "",
        ]

        # Commitments section
        if data["commitments"]:
            lines.append("## Commitments")
            for commitment in data["commitments"]:
                lines.append(f"\n### {commitment.get('text', 'Unnamed commitment')}")
                lines.append(f"- Owner: {commitment.get('owner', 'Unassigned')}")
                if "due_date" in commitment:
                    lines.append(f"- Due: {commitment['due_date']}")
                if "priority" in commitment:
                    lines.append(f"- Priority: {commitment['priority']}")
                lines.append("")

        # Decisions section
        if data["decisions"]:
            lines.append("## Decisions")
            for decision in data["decisions"]:
                lines.append(f"\n### {decision.get('text', 'Unnamed decision')}")
                if "rationale" in decision:
                    lines.append(f"- Rationale: {decision['rationale']}")
                if "impact" in decision:
                    lines.append(f"- Impact: {decision['impact']}")
                if "participants" in decision:
                    lines.append(
                        f"- Participants: {', '.join(decision['participants'])}"
                    )
                lines.append("")

        # Patterns section
        if data["patterns"]:
            lines.append("## Detected Patterns")
            for pattern in data["patterns"]:
                lines.append(f"\n### {pattern.get('pattern', 'Unnamed pattern')}")
                if "confidence" in pattern:
                    lines.append(f"- Confidence: {pattern['confidence']:.0%}")
                if "evidence" in pattern:
                    lines.append("- Evidence:")
                    for evidence in pattern["evidence"]:
                        lines.append(f"  - {evidence}")
                lines.append("")

        # Insights section
        if data["insights"]:
            lines.append("## Key Insights")
            for insight in data["insights"]:
                lines.append(f"- {insight.get('insight', 'Unnamed insight')}")
                if "confidence" in insight:
                    lines.append(f"  - Confidence: {insight['confidence']:.0%}")

        return "\n".join(lines)

    async def _append_monthly_commitments(
        self, meeting_id: str, commitments: list[dict]
    ) -> None:
        """Append commitments to monthly log"""
        if not commitments:
            return

        # Determine month from meeting_id (format: meeting-YYYYMMDD-N)
        try:
            date_str = meeting_id.split("-")[1]
            year = date_str[:4]
            month = date_str[4:6]
            month_file = f"{year}-{month}-commitments.md"
        except (IndexError, ValueError) as e:
            logger.warning(f"Cannot parse date from meeting_id {meeting_id}: {e}")
            # Use current date as fallback
            now = datetime.utcnow()
            month_file = f"{now.year}-{now.month:02d}-commitments.md"

        file_path = self.base_path / "analysis" / "commitments" / month_file

        # Read existing content or create header
        if file_path.exists():
            with open(file_path) as f:
                content = f.read()
        else:
            month_name = datetime(int(year), int(month), 1).strftime("%B")
            content = f"# Commitments - {month_name} {year}\n\n"

        # Append new commitments
        content += f"## {meeting_id}\n"
        content += f"*Added: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n\n"

        for commitment in commitments:
            content += f"- **{commitment.get('text', 'Unnamed commitment')}**\n"
            content += f"  - Owner: {commitment.get('owner', 'Unassigned')}\n"
            if "due_date" in commitment:
                content += f"  - Due: {commitment['due_date']}\n"
            if "priority" in commitment:
                content += f"  - Priority: {commitment['priority']}\n"
            content += f"  - Status: {commitment.get('status', 'pending')}\n"
            content += "\n"

        # Write back
        with open(file_path, "w") as f:
            f.write(content)

    async def _append_monthly_decisions(
        self, meeting_id: str, decisions: list[dict]
    ) -> None:
        """Append decisions to monthly log"""
        if not decisions:
            return

        try:
            date_str = meeting_id.split("-")[1]
            year = date_str[:4]
            month = date_str[4:6]
            month_file = f"{year}-{month}-decisions.md"
        except (IndexError, ValueError) as e:
            logger.warning(f"Cannot parse date from meeting_id {meeting_id}: {e}")
            now = datetime.utcnow()
            month_file = f"{now.year}-{now.month:02d}-decisions.md"

        file_path = self.base_path / "analysis" / "decisions" / month_file

        if file_path.exists():
            with open(file_path) as f:
                content = f.read()
        else:
            month_name = datetime(int(year), int(month), 1).strftime("%B")
            content = f"# Decisions - {month_name} {year}\n\n"

        content += f"## {meeting_id}\n"
        content += f"*Added: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n\n"

        for decision in decisions:
            content += f"### {decision.get('text', 'Unnamed decision')}\n"
            if "rationale" in decision:
                content += f"**Rationale:** {decision['rationale']}\n"
            if "impact" in decision:
                content += f"**Impact:** {decision['impact']}\n"
            if "participants" in decision:
                content += f"**Participants:** {', '.join(decision['participants'])}\n"
            content += "\n"

        with open(file_path, "w") as f:
            f.write(content)

    async def _append_monthly_patterns(
        self, meeting_id: str, patterns: list[dict]
    ) -> None:
        """Append patterns to monthly log"""
        if not patterns:
            return

        try:
            date_str = meeting_id.split("-")[1]
            year = date_str[:4]
            month = date_str[4:6]
            month_file = f"{year}-{month}-patterns.md"
        except (IndexError, ValueError) as e:
            logger.warning(f"Cannot parse date from meeting_id {meeting_id}: {e}")
            now = datetime.utcnow()
            month_file = f"{now.year}-{now.month:02d}-patterns.md"

        file_path = self.base_path / "analysis" / "patterns" / month_file

        if file_path.exists():
            with open(file_path) as f:
                content = f.read()
        else:
            month_name = datetime(int(year), int(month), 1).strftime("%B")
            content = f"# Patterns - {month_name} {year}\n\n"

        content += f"## {meeting_id}\n"
        content += f"*Added: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n\n"

        for pattern in patterns:
            content += f"### {pattern.get('pattern', 'Unnamed pattern')}\n"
            if "confidence" in pattern:
                content += f"**Confidence:** {pattern['confidence']:.0%}\n"
            if "evidence" in pattern:
                content += "**Evidence:**\n"
                for evidence in pattern["evidence"]:
                    content += f"- {evidence}\n"
            content += "\n"

        with open(file_path, "w") as f:
            f.write(content)

    async def get_meeting_analysis(self, meeting_id: str) -> dict[str, Any] | None:
        """Retrieve stored analysis for a meeting"""
        json_path = (
            self.base_path / "analysis" / "meetings" / f"{meeting_id}-analysis.json"
        )

        if not json_path.exists():
            return None

        with open(json_path) as f:
            return json.load(f)

    async def get_pattern_evolution(
        self, pattern_name: str, month: str
    ) -> list[dict[str, Any]]:
        """Track how a pattern evolves over time"""
        evolution = []

        # Parse all meeting analyses for the month
        meetings_dir = self.base_path / "analysis" / "meetings"
        if not meetings_dir.exists():
            return evolution

        for json_file in meetings_dir.glob(
            f"meeting-{month.replace('-', '')}*-analysis.json"
        ):
            with open(json_file) as f:
                data = json.load(f)

            # Find matching patterns
            for pattern in data.get("patterns", []):
                if pattern.get("pattern") == pattern_name:
                    evolution.append(
                        {
                            "meeting_id": data["meeting_id"],
                            "timestamp": data["timestamp"],
                            "confidence": pattern.get("confidence", 0),
                            "evidence": pattern.get("evidence", []),
                        }
                    )

        # Sort by timestamp
        evolution.sort(key=lambda x: x["timestamp"])

        return evolution
