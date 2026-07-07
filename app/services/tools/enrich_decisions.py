"""
Enrich Decisions Tool — Add contextual depth to extracted decisions.

Takes core decision facts (from extract_decisions) and enriches them with
alternatives considered, dependencies, implementation timeline, success
criteria, and context. Runs in the ENRICH phase, after EXTRACT.
"""

import logging
import time
from typing import Any

import yaml

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from .yaml_utils import extract_yaml_block, parse_yaml_list

logger = logging.getLogger(__name__)


class EnrichDecisionsTool(AgentTool):
    """Enrich extracted decisions with contextual depth via LLM."""

    @property
    def name(self) -> str:
        return "enrich_decisions"

    @property
    def description(self) -> str:
        return "Add alternatives, dependencies, timeline, and context to extracted decisions"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Original content the decisions were extracted from",
                },
                "decisions": {
                    "type": "array",
                    "description": "Decisions already extracted (core fields only)",
                    "items": {"type": "object"},
                },
            },
            "required": ["content", "decisions"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "enriched_decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "alternatives_considered": {"type": "array", "items": {"type": "string"}},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                            "implementation_timeline": {"type": "string"},
                            "success_criteria": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Enrich decisions with contextual depth."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            content = inputs.get("content", "")
            decisions = inputs.get("decisions", [])

            if not decisions:
                result = ToolResult(
                    success=True,
                    data={"enriched_decisions": []},
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )
                self._finish_execution(execution, result)
                return result

            prompt = self._build_prompt(content, decisions)
            response = await self.claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=2048,
                temperature=0.0,
                operation="enrich_decisions",
            )

            response_text = self._get_response_text(response)
            yaml_content = extract_yaml_block(response_text)
            enrichments = parse_yaml_list(yaml_content, "enrichments")

            # Validate enrichments have required id field
            valid = [e for e in enrichments if isinstance(e, dict) and "id" in e]

            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=True,
                data={"enriched_decisions": valid},
                execution_time_ms=execution_time_ms,
            )
            self._finish_execution(execution, result)
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            result = ToolResult(
                success=False,
                data={"enriched_decisions": []},
                execution_time_ms=execution_time_ms,
                error=str(e),
            )
            self._finish_execution(execution, result)
            return result

    def _build_prompt(self, content: str, decisions: list[dict]) -> str:
        """Build the enrichment prompt."""
        decision_summaries = yaml.dump(
            [{"id": d.get("id", f"decision-{i+1}"), "decision": d.get("decision", "")}
             for i, d in enumerate(decisions)],
            default_flow_style=False,
        )

        return f"""Enrich these decisions with additional context from the content below.

<decisions>
{decision_summaries}
</decisions>

<content>
{content}
</content>

For each decision, add contextual depth. Return ONLY a YAML block — no preamble:

```yaml
enrichments:
  - id: "decision-1"
    alternatives_considered:
      - "Option that was discussed but not chosen"
    dependencies:
      - "What must happen first or what this depends on"
    implementation_timeline: "When and how this will be implemented"
    success_criteria: "How success will be measured"
    context: "Background and circumstances around this decision"
```

Rules:
- id MUST match the decision IDs above
- alternatives_considered: list of options discussed (empty [] if none mentioned)
- dependencies: list of prerequisites or blockers (empty [] if none mentioned)
- implementation_timeline: timeframe or "Not stated" if unclear
- success_criteria: measurable outcome or "Not stated" if unclear
- context: 1-2 sentences of background from the content
- Quote all string values
- Only include information clearly supported by the content"""

    @staticmethod
    def _get_response_text(response) -> str:
        """Extract text from Claude API response."""
        if hasattr(response, "content"):
            content_data = response.content
            if isinstance(content_data, list) and len(content_data) > 0:
                return content_data[0].text if hasattr(content_data[0], "text") else str(content_data[0])
            return str(content_data)
        return str(response)
