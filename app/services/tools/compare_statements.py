"""
Compare Statements Tool - Find contradictions across documents.
"""

import time
from typing import Any

import yaml

from ...config import settings
from ..agent_tools import AgentTool, ToolResult


class CompareStatementsTool(AgentTool):
    """Tool for finding contradictions and inconsistencies across content."""

    @property
    def name(self) -> str:
        return "compare_statements"

    @property
    def description(self) -> str:
        return "Find contradictions, inconsistencies, and conflicting statements across content"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content_list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "source": {
                                "type": "string",
                                "description": "Source identifier (e.g., meeting date, file name)",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Additional context",
                            },
                        },
                        "required": ["content", "source"],
                    },
                    "description": "List of content pieces to compare with source information",
                },
                "comparison_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "contradictions",
                            "inconsistencies",
                            "changes",
                            "conflicts",
                        ],
                    },
                    "default": ["contradictions", "inconsistencies", "conflicts"],
                    "description": "Types of comparisons to perform",
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "commitments",
                        "decisions",
                        "timelines",
                        "responsibilities",
                    ],
                    "description": "Specific areas to focus comparison on",
                },
            },
            "required": ["content_list"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "comparisons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": [
                                    "contradiction",
                                    "inconsistency",
                                    "change",
                                    "conflict",
                                ],
                            },
                            "description": {"type": "string"},
                            "statements": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "source": {"type": "string"},
                                        "context": {"type": "string"},
                                    },
                                },
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["critical", "high", "medium", "low"],
                            },
                            "category": {"type": "string"},
                            "impact": {"type": "string"},
                            "recommendation": {"type": "string"},
                        },
                    },
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "total_comparisons": {"type": "integer"},
                        "by_severity": {"type": "object"},
                        "by_type": {"type": "object"},
                        "by_category": {"type": "object"},
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute statement comparison analysis."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            content_list = inputs["content_list"]
            comparison_types = inputs.get(
                "comparison_types", ["contradictions", "inconsistencies", "conflicts"]
            )
            focus_areas = inputs.get(
                "focus_areas",
                ["commitments", "decisions", "timelines", "responsibilities"],
            )

            if len(content_list) < 2:
                raise ValueError("At least 2 content pieces required for comparison")

            # Prepare comparison prompt
            prompt = self._build_comparison_prompt(
                content_list, comparison_types, focus_areas
            )

            messages = [{"role": "user", "content": prompt}]
            response_dict = await self.claude_client.generate_message(
                messages,
                model=settings.CLAUDE_HAIKU_MODEL,
                operation="statement_comparison",
            )

            # Extract content from response
            if hasattr(response_dict, "content"):
                content = response_dict.content
                if isinstance(content, list) and len(content) > 0:
                    response = (
                        content[0].text
                        if hasattr(content[0], "text")
                        else str(content[0])
                    )
                else:
                    response = str(content)
            elif isinstance(response_dict, dict) and "content" in response_dict:
                response = response_dict["content"]
            else:
                response = str(response_dict)

            # Parse YAML response
            try:
                yaml_start = response.find("```yaml")
                yaml_end = response.find("```", yaml_start + 7)
                if yaml_start != -1 and yaml_end != -1:
                    yaml_content = response[yaml_start + 7 : yaml_end].strip()
                else:
                    yaml_content = response

                parsed_data = yaml.safe_load(yaml_content)
                comparisons = parsed_data.get("comparisons", [])

            except yaml.YAMLError as e:
                raise ValueError(f"Failed to parse YAML response: {e}")

            # Process and enhance comparisons
            processed_comparisons = []
            for i, comparison in enumerate(comparisons):
                comparison_id = comparison.get("id", f"comp-{i+1}")

                processed_comparison = {
                    "id": comparison_id,
                    "type": comparison.get("type", "inconsistency"),
                    "description": comparison.get("description", ""),
                    "statements": comparison.get("statements", []),
                    "severity": comparison.get("severity", "medium"),
                    "category": comparison.get("category", "general"),
                    "impact": comparison.get("impact", ""),
                    "recommendation": comparison.get("recommendation", ""),
                }

                processed_comparisons.append(processed_comparison)

            # Generate summary
            summary = self._generate_summary(processed_comparisons)

            execution_time_ms = int((time.time() - start_time) * 1000)

            result = ToolResult(
                success=True,
                data={"comparisons": processed_comparisons, "summary": summary},
                execution_time_ms=execution_time_ms,
                metadata={
                    "content_count": len(content_list),
                    "comparison_types": comparison_types,
                    "focus_areas": focus_areas,
                    "total_found": len(comparisons),
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

    def _build_comparison_prompt(
        self,
        content_list: list[dict],
        comparison_types: list[str],
        focus_areas: list[str],
    ) -> str:
        """Build the comparison analysis prompt."""

        # Format content sources
        content_sections = []
        for i, item in enumerate(content_list, 1):
            source = item.get("source", f"Source {i}")
            content = item.get("content", "")
            metadata = item.get("metadata", {})

            section = f"## {source}\n"
            if metadata:
                section += f"**Metadata:** {metadata}\n\n"
            section += f"{content}\n"
            content_sections.append(section)

        prompt = f"""You are an expert at identifying contradictions, inconsistencies, and conflicts across multiple documents or content pieces.

Analyze the following content sources and identify any:
- {', '.join(comparison_types)}

Focus particularly on these areas:
- {', '.join(focus_areas)}

Look for:
1. **Contradictions**: Direct conflicts between statements
2. **Inconsistencies**: Subtle differences that may cause confusion
3. **Changes**: Evolution of positions or decisions over time
4. **Conflicts**: Competing priorities or incompatible commitments

For each issue found, consider:
- Severity of the potential impact
- Category of the issue (e.g., timeline, responsibility, scope)
- Recommendations for resolution

# Content to Analyze

{chr(10).join(content_sections)}

# Output Format

Respond with valid YAML in the following format:

```yaml
comparisons:
  - id: "comp-1"
    type: "contradiction|inconsistency|change|conflict"
    description: "Clear description of the issue identified"
    statements:
      - text: "Exact quote or paraphrase of conflicting statement"
        source: "Source identifier"
        context: "Surrounding context for clarity"
      - text: "Conflicting statement"
        source: "Different source"
        context: "Context of second statement"
    severity: "critical|high|medium|low"
    category: "timeline|responsibility|scope|priority|decision|commitment"
    impact: "Description of potential impact if not resolved"
    recommendation: "Suggested action to resolve the conflict"
  # ... more comparisons

summary:
  total_comparisons: number
  critical_issues: number
  high_priority: number
```

Be thorough but focus on genuine conflicts that could impact execution or understanding."""

        return prompt

    def _generate_summary(self, comparisons: list[dict]) -> dict[str, Any]:
        """Generate summary of comparison results."""
        total = len(comparisons)

        # Count by severity
        by_severity = {}
        for comp in comparisons:
            severity = comp.get("severity", "medium")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        # Count by type
        by_type = {}
        for comp in comparisons:
            comp_type = comp.get("type", "inconsistency")
            by_type[comp_type] = by_type.get(comp_type, 0) + 1

        # Count by category
        by_category = {}
        for comp in comparisons:
            category = comp.get("category", "general")
            by_category[category] = by_category.get(category, 0) + 1

        return {
            "total_comparisons": total,
            "critical_issues": by_severity.get("critical", 0),
            "high_priority": by_severity.get("high", 0),
            "by_severity": by_severity,
            "by_type": by_type,
            "by_category": by_category,
        }
