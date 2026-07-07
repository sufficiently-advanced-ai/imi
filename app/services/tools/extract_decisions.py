"""
Extract Decisions Tool - Identify decisions and decision-making processes in content.

Finds explicit and implicit decisions, tracks decision makers, rationale,
alternatives considered, and implementation timelines.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from .yaml_utils import extract_yaml_block, parse_yaml_list

logger = logging.getLogger(__name__)


class ExtractDecisionsTool(AgentTool):
    """Tool for extracting decisions and decision-making processes from content."""

    @property
    def name(self) -> str:
        return "extract_decisions"

    @property
    def description(self) -> str:
        return "Identify decisions, decision makers, rationale, alternatives, and implementation timelines"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to analyze for decisions",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional file path for context",
                    "default": None,
                },
                "reference_date": {
                    "type": "string",
                    "description": "Reference date for decision timelines (YYYY-MM-DD)",
                    "default": None,
                },
                "decision_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["strategic", "tactical", "operational", "policy"],
                    "description": "Types of decisions to focus on",
                },
            },
            "required": ["content"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "decision": {"type": "string"},
                            "decision_maker": {"type": "string"},
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                            "alternatives_considered": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "decision_date": {"type": "string"},
                            "implementation_timeline": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "proposed",
                                    "decided",
                                    "implemented",
                                    "reversed",
                                ],
                            },
                            "type": {
                                "type": "string",
                                "enum": [
                                    "strategic",
                                    "tactical",
                                    "operational",
                                    "policy",
                                    "resource",
                                    "process",
                                ],
                            },
                            "impact": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "stakeholders": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "success_criteria": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "total_decisions": {"type": "integer"},
                        "by_status": {"type": "object"},
                        "by_type": {"type": "object"},
                        "by_impact": {"type": "object"},
                        "by_decision_maker": {"type": "object"},
                        "pending_implementation": {"type": "integer"},
                        "high_impact_decisions": {"type": "integer"},
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute decision extraction on the provided content."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            content = inputs["content"]
            file_path = inputs.get("file_path")
            reference_date = inputs.get("reference_date")
            decision_types = inputs.get(
                "decision_types", ["strategic", "tactical", "operational", "policy"]
            )

            # Set reference date for decision timelines
            if reference_date:
                ref_date = datetime.strptime(reference_date, "%Y-%m-%d")
            else:
                ref_date = datetime.now()

            # Build analysis prompt
            prompt = self._build_decision_prompt(content, decision_types, ref_date)

            messages = [{"role": "user", "content": prompt}]
            response_dict = await self.claude_client.generate_message(
                messages,
                model=settings.CLAUDE_HAIKU_MODEL,
                operation="decision_extraction",
            )

            # Extract content from response
            if hasattr(response_dict, "content"):
                content_obj = response_dict.content
                if isinstance(content_obj, list) and len(content_obj) > 0:
                    response = (
                        content_obj[0].text
                        if hasattr(content_obj[0], "text")
                        else str(content_obj[0])
                    )
                else:
                    response = str(content_obj)
            elif isinstance(response_dict, dict) and "content" in response_dict:
                response = response_dict["content"]
            else:
                response = str(response_dict)

            # Parse YAML response — handle various Claude response formats
            yaml_content = extract_yaml_block(response)
            decisions = parse_yaml_list(yaml_content, "decisions")

            # Process and enhance decisions
            processed_decisions = []
            for i, decision in enumerate(decisions):
                decision_id = decision.get("id", f"decision-{i+1}")

                # Parse and normalize decision dates
                decision_date = decision.get("decision_date", "")
                normalized_decision_date = self._normalize_date(decision_date, ref_date)

                # Determine status based on content and date
                status = decision.get("status", "decided")

                processed_decision = {
                    "id": decision_id,
                    "decision": decision.get("decision", ""),
                    "decision_maker": decision.get("decision_maker", "unknown"),
                    "description": decision.get("description", ""),
                    "rationale": decision.get("rationale", ""),
                    "alternatives_considered": decision.get(
                        "alternatives_considered", []
                    ),
                    "decision_date": decision_date,
                    "normalized_decision_date": normalized_decision_date.isoformat()
                    if normalized_decision_date
                    else None,
                    "implementation_timeline": decision.get(
                        "implementation_timeline", ""
                    ),
                    "status": status,
                    "type": decision.get("type", "operational"),
                    "impact": decision.get("impact", "medium"),
                    "stakeholders": decision.get("stakeholders", []),
                    "dependencies": decision.get("dependencies", []),
                    "success_criteria": decision.get("success_criteria", ""),
                    "context": decision.get("context", ""),
                }

                processed_decisions.append(processed_decision)

            # Generate enhanced summary
            summary = self._generate_summary(processed_decisions)

            execution_time_ms = int((time.time() - start_time) * 1000)

            result = ToolResult(
                success=True,
                data={"decisions": processed_decisions, "summary": summary},
                execution_time_ms=execution_time_ms,
                metadata={
                    "file_path": file_path,
                    "reference_date": ref_date.isoformat(),
                    "decision_types": decision_types,
                    "total_found": len(decisions),
                },
            )

            self._finish_execution(execution, result)
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=False,
                data={},
                execution_time_ms=execution_time_ms,
                error=str(e),
            )

            self._finish_execution(execution, result)
            return result

    def _build_decision_prompt(
        self, content: str, decision_types: list[str], reference_date: datetime
    ) -> str:
        """Build the decision analysis prompt."""

        prompt = f"""Extract decisions from this content. A decision is a WORK-RELEVANT choice that was explicitly stated or unambiguously agreed in the conversation, with ALL of: (a) a named decision-maker or a group that clearly agreed, (b) commitment language ("we will", "agreed to", "approved", "going with"), and (c) a consequence for the project, client, or business.

<content>
{content}
</content>

Focus on: {', '.join(decision_types)} decisions.
Reference date: {reference_date.strftime('%Y-%m-%d')}

Return ONLY a YAML block — no preamble, no commentary. Use this exact format:

```yaml
decisions:
  - id: "decision-1"
    decision: "What was decided"
    decision_maker: "Who decided"
    rationale: "Why"
    status: "decided"
    type: "tactical"
    impact: "high"
    stakeholders:
      - "Affected person or team"
```

Rules:
- decision: a short, clear statement of the choice made (one sentence)
- decision_maker: name of person or group
- rationale: brief reason (one sentence, or "Not stated" if unclear)
- status: one of proposed, decided, implemented, reversed
- type: one of strategic, tactical, operational, policy, resource, process
- impact: one of high, medium, low
- stakeholders: list of names/teams affected
- These are NOT decisions — do not extract them:
  * unagreed opinions ("I think we should...") with no agreement
  * hypotheticals and parked ideas ("we could...", "maybe at some point", "park it")
  * personal or banter commitments (appearance, hobbies, lunch plans)
  * restatements of decisions made in earlier meetings
  * scheduling trivia (agreeing on a meeting time)
- Informal agreement still counts when real: "sounds good, let's do that" after a concrete proposal IS a decision
- Always quote all string values in the YAML
- If no decisions found, return: decisions: []"""

        return prompt

    def _normalize_date(
        self, date_str: str, reference_date: datetime
    ) -> datetime | None:
        """Normalize various date formats to a standard datetime."""
        if not date_str:
            return None

        date_str = date_str.lower().strip()

        # Handle specific date formats
        date_formats = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"]

        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Handle relative dates
        if "today" in date_str:
            return reference_date
        elif "yesterday" in date_str:
            return reference_date - timedelta(days=1)
        elif "last week" in date_str:
            return reference_date - timedelta(weeks=1)
        elif "this week" in date_str:
            return reference_date
        elif "next week" in date_str:
            return reference_date + timedelta(weeks=1)
        elif "last month" in date_str:
            return reference_date - timedelta(days=30)
        elif "this month" in date_str:
            return reference_date
        elif "next month" in date_str:
            return reference_date + timedelta(days=30)

        return None

    def _generate_summary(self, decisions: list[dict]) -> dict[str, Any]:
        """Generate comprehensive summary of decisions."""
        total = len(decisions)

        # Count by status
        by_status = {}
        for decision in decisions:
            status = decision.get("status", "decided")
            by_status[status] = by_status.get(status, 0) + 1

        # Count by type
        by_type = {}
        for decision in decisions:
            decision_type = decision.get("type", "operational")
            by_type[decision_type] = by_type.get(decision_type, 0) + 1

        # Count by impact
        by_impact = {}
        for decision in decisions:
            impact = decision.get("impact", "medium")
            by_impact[impact] = by_impact.get(impact, 0) + 1

        # Count by decision maker
        by_decision_maker = {}
        for decision in decisions:
            maker = decision.get("decision_maker", "unknown")
            if maker != "unknown":
                by_decision_maker[maker] = by_decision_maker.get(maker, 0) + 1

        # Count pending implementation
        pending_implementation = len(
            [d for d in decisions if d.get("status") in ["decided", "proposed"]]
        )

        # Count high impact decisions
        high_impact_decisions = len([d for d in decisions if d.get("impact") == "high"])

        return {
            "total_decisions": total,
            "by_status": by_status,
            "by_type": by_type,
            "by_impact": by_impact,
            "by_decision_maker": by_decision_maker,
            "pending_implementation": pending_implementation,
            "high_impact_decisions": high_impact_decisions,
        }
