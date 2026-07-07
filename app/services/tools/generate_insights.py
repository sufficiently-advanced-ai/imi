"""
Generate Insights Tool - Advanced predictive analytics and opportunity identification.
"""

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from ..prompts import PromptService


class GenerateInsightsTool(AgentTool):
    """Advanced tool for predictive analytics, issue escalation detection, and opportunity identification."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)
        self.prompt_service = PromptService()

    @property
    def name(self) -> str:
        return "generate_insights"

    @property
    def description(self) -> str:
        return "Advanced predictive analytics for issue escalation, opportunity identification, and strategic recommendations"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "data": {
                    "type": "string",
                    "description": "Data to analyze for insights. Can be JSON, text, or natural language request like 'analyze risks for project alpha' or 'predict issues for next 2 weeks'",
                }
            },
            "required": ["data"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "insights": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "category": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "supporting_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "confidence_level": {"type": "number"},
                            "novelty_score": {"type": "number"},
                            "impact_level": {"type": "string"},
                            "urgency": {"type": "string"},
                            "timeframe": {"type": "string"},
                            "predicted_outcome": {"type": "string"},
                            "success_probability": {"type": "number"},
                            "entities_affected": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "actionable_steps": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "success_metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "related_patterns": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "risk_factors": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "predictions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prediction_id": {"type": "string"},
                            "prediction": {"type": "string"},
                            "probability": {"type": "number"},
                            "confidence_interval": {"type": "string"},
                            "timeframe": {"type": "string"},
                            "category": {"type": "string"},
                            "severity": {"type": "string"},
                            "supporting_insights": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "early_indicators": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "mitigation_strategies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "opportunity_actions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "recommendations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "recommendation_id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "priority": {"type": "string"},
                            "effort_required": {"type": "string"},
                            "expected_impact": {"type": "string"},
                            "implementation_steps": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "timeline": {"type": "string"},
                            "resources_needed": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "success_criteria": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "supporting_insights": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "total_insights": {"type": "integer"},
                        "high_confidence_insights": {"type": "integer"},
                        "critical_issues_predicted": {"type": "integer"},
                        "opportunities_identified": {"type": "integer"},
                        "immediate_actions_needed": {"type": "integer"},
                        "average_prediction_confidence": {"type": "number"},
                        "novel_insights_percentage": {"type": "number"},
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute advanced insight generation with predictive analytics."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            # Parse natural language input
            data_input = inputs["data"]
            parsed_params = self._parse_insight_request(data_input)

            analyzed_data = parsed_params["analyzed_data"]
            historical_data = parsed_params["historical_data"]
            insight_types = parsed_params["insight_types"]
            focus_areas = parsed_params["focus_areas"]
            prediction_horizon = parsed_params["prediction_horizon_days"]
            confidence_threshold = parsed_params["confidence_threshold"]
            novelty_threshold = parsed_params["novelty_threshold"]
            target_entities = parsed_params["entities"]

            # Gather historical context for predictive analysis
            historical_context = await self._gather_historical_context(
                historical_data, prediction_horizon
            )

            # Get current entities context
            entities_context = await self._get_current_entities_context(
                target_entities, analyzed_data
            )

            # Generate insights using Claude with predictive prompts
            insights_result = await self._generate_insights_with_claude(
                analyzed_data,
                historical_context,
                entities_context,
                insight_types,
                focus_areas,
                prediction_horizon,
                confidence_threshold,
                novelty_threshold,
            )

            # Apply post-processing filters
            filtered_insights = self._filter_insights_by_quality(
                insights_result, confidence_threshold, novelty_threshold
            )

            result = ToolResult(
                success=True,
                data=filtered_insights,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

            reasoning = f"Generated {filtered_insights.get('summary', {}).get('total_insights', 0)} insights with {filtered_insights.get('summary', {}).get('high_confidence_insights', 0)} high-confidence predictions and {filtered_insights.get('summary', {}).get('opportunities_identified', 0)} opportunities"

        except Exception as e:
            result = ToolResult(
                success=False,
                data={},
                execution_time_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )
            reasoning = f"Insight generation failed: {str(e)}"

        self._finish_execution(execution, result, reasoning)
        return result

    def _parse_insight_request(self, data_input: Any) -> dict[str, Any]:
        """Parse natural language insight request and extract parameters."""
        import json
        import re

        from .parsing_utils import (
            extract_entities_from_text,
            normalize_whitespace,
            parse_time_horizon,
        )

        # Default values
        params = {
            "analyzed_data": {},
            "historical_data": {},
            "insight_types": [
                "predictions",
                "opportunities",
                "risks",
                "trends",
                "recommendations",
            ],
            "focus_areas": [
                "issue_escalation",
                "opportunity_identification",
                "team_dynamics",
                "project_health",
            ],
            "prediction_horizon_days": 30,
            "confidence_threshold": 0.8,
            "novelty_threshold": 0.7,
            "entities": [],
        }

        if not data_input:
            return params

        # If data_input is already a dict, use it directly
        if isinstance(data_input, dict):
            params["analyzed_data"] = data_input
            return params

        # Convert to string if not already
        data_str = str(data_input) if not isinstance(data_input, str) else data_input

        # Try to parse as JSON first
        try:
            if data_str.strip().startswith("{"):
                json_data = json.loads(data_str)
                if isinstance(json_data, dict):
                    params["analyzed_data"] = json_data
                    return params
        except json.JSONDecodeError:
            pass

        # Normalize text for processing
        normalized_text = normalize_whitespace(data_str)
        lower_text = normalized_text.lower()

        # Extract time horizon using utility
        params["prediction_horizon_days"] = parse_time_horizon(normalized_text)

        # Extract insight types based on keywords
        if re.search(r"\brisk", lower_text):
            params["insight_types"] = ["risks", "predictions"]
            params["focus_areas"] = ["issue_escalation", "project_health"]
        elif re.search(r"\bopportunit", lower_text):
            params["insight_types"] = ["opportunities", "recommendations"]
            params["focus_areas"] = ["opportunity_identification"]
        elif re.search(r"\bpredict", lower_text):
            params["insight_types"] = ["predictions", "trends"]
        elif re.search(r"\brecommend", lower_text):
            params["insight_types"] = ["recommendations", "opportunities"]

        # Extract entities using improved utility
        params["entities"] = extract_entities_from_text(data_str)  # Use original case

        # Extract confidence preferences
        if re.search(r"\bhigh confidence\b", lower_text):
            params["confidence_threshold"] = 0.9
        elif re.search(r"\b(?:low confidence|any confidence)\b", lower_text):
            params["confidence_threshold"] = 0.5

        # Determine if input is data or instructions
        instruction_words = [
            "analyze",
            "predict",
            "find",
            "generate",
            "insights",
            "risks",
            "opportunities",
        ]
        has_instructions = any(word in lower_text for word in instruction_words)

        if not has_instructions:
            # Treat the entire input as data to analyze
            params["analyzed_data"] = {"raw_content": data_str}
        else:
            # Extract content after instruction words
            content_patterns = [
                r"analyze\s+(.+)",
                r"predict\s+(.+)",
                r"insights?\s+for\s+(.+)",
                r"insights?\s+about\s+(.+)",
                r"data:\s*(.+)",
            ]

            for pattern in content_patterns:
                match = re.search(pattern, data_str, re.IGNORECASE | re.DOTALL)
                if match:
                    content = match.group(1).strip()
                    if content:
                        params["analyzed_data"] = {"raw_content": content}
                    break

            # If no content extracted but we have entities, use them as context
            if not params["analyzed_data"] and params["entities"]:
                params["analyzed_data"] = {
                    "raw_content": f"Analysis requested for: {', '.join(params['entities'])}"
                }

        return params

    async def _gather_historical_context(
        self, historical_data: dict[str, Any], prediction_horizon: int
    ) -> dict[str, Any]:
        """Gather historical context for predictive analysis."""
        context = {
            "timeframe": f"Historical analysis for {prediction_horizon}-day predictions",
            "patterns": [],
            "trends": [],
            "outcomes": [],
            "success_factors": [],
            "failure_indicators": [],
        }

        try:
            # If historical data provided, extract key elements
            if historical_data:
                context["patterns"] = historical_data.get("patterns", [])
                context["trends"] = historical_data.get("trends", [])
                context["outcomes"] = historical_data.get("outcomes", [])

            # Get recent git history for trend analysis
            end_date = datetime.now()
            start_date = end_date - timedelta(
                days=prediction_horizon * 2
            )  # Look back twice the prediction horizon

            recent_commits = await self._get_recent_activity_trends(
                start_date, end_date
            )
            context["recent_activity"] = recent_commits

        except Exception as e:
            print(f"Warning: Failed to gather some historical context: {e}")

        return context

    async def _get_recent_activity_trends(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Get recent activity trends for predictive context."""
        try:
            # TODO: Implement proper git history retrieval
            # For now, return empty trends to avoid errors
            # The git_ops module doesn't have get_commit_history method
            return []

        except Exception as e:
            print(f"Failed to get activity trends: {e}")
            return []

    async def _get_current_entities_context(
        self, target_entities: list[str], analyzed_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Get current context about entities for predictive analysis."""
        context = {
            "target_entities": target_entities,
            "entity_patterns": {},
            "relationship_dynamics": [],
            "current_workload": {},
            "communication_patterns": [],
        }

        try:
            # Extract entity-related patterns from analyzed data
            if "patterns" in analyzed_data:
                entity_patterns = {}
                for pattern in analyzed_data["patterns"]:
                    if isinstance(pattern, dict) and "entities_involved" in pattern:
                        for entity in pattern["entities_involved"]:
                            if entity not in entity_patterns:
                                entity_patterns[entity] = []
                            entity_patterns[entity].append(pattern)

                context["entity_patterns"] = entity_patterns

        except Exception as e:
            print(f"Failed to get entities context: {e}")

        return context

    async def _generate_insights_with_claude(
        self,
        analyzed_data: dict[str, Any],
        historical_context: dict[str, Any],
        entities_context: dict[str, Any],
        insight_types: list[str],
        focus_areas: list[str],
        prediction_horizon: int,
        confidence_threshold: float,
        novelty_threshold: float,
    ) -> dict[str, Any]:
        """Use Claude to generate advanced insights with predictive analytics."""
        try:
            # Load prompt template
            prompt_template = self.prompt_service.get_prompt("generate_insights")

            # Prepare context data
            analyzed_data_summary = self._prepare_analyzed_data_summary(analyzed_data)
            historical_summary = self._prepare_historical_summary(historical_context)
            entities_summary = self._prepare_entities_summary(entities_context)
            focus_summary = f"Focus areas: {', '.join(focus_areas)}\nInsight types: {', '.join(insight_types)}\nPrediction horizon: {prediction_horizon} days"

            # Format the prompt
            formatted_prompt = prompt_template.replace(
                "<!-- Results from pattern analysis and other tools get injected here -->",
                analyzed_data_summary,
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Historical patterns and trends get injected here -->",
                historical_summary,
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Current people, projects, teams context get injected here -->",
                entities_summary,
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Specific types of insights requested get injected here -->",
                focus_summary,
            )

            # Add quality thresholds
            formatted_prompt += f"\n\nQUALITY REQUIREMENTS:\n- Minimum confidence level: {confidence_threshold}\n- Minimum novelty score: {novelty_threshold}\n- Focus on actionable insights with >80% prediction accuracy"

            # Make Claude API call with higher token limit for complex analysis
            messages = [{"role": "user", "content": formatted_prompt}]
            response = await self.claude_client.generate_message(
                messages=messages,
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=4000,
                temperature=0.2,  # Lower temperature for more analytical precision
                operation="insight_generation",
            )

            # Parse JSON response
            response_text = response.content[0].text

            # Try to extract JSON from the response
            # Sometimes Claude includes explanatory text before/after the JSON
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                try:
                    insights_result = json.loads(json_match.group())
                    return insights_result
                except json.JSONDecodeError:
                    pass

            # If no JSON found or parsing failed, try direct parsing
            insights_result = json.loads(response_text)

            return insights_result

        except json.JSONDecodeError as e:
            print(f"Failed to parse Claude response as JSON: {e}")
            # Log the actual response for debugging
            print(
                f"Response text was: {response_text[:200]}..."
                if "response_text" in locals()
                else "No response text available"
            )
            return self._get_empty_insights_result()
        except Exception as e:
            print(f"Failed to generate insights with Claude: {e}")
            return self._get_empty_insights_result()

    def _prepare_analyzed_data_summary(self, analyzed_data: dict[str, Any]) -> str:
        """Prepare a summary of analyzed data for Claude."""
        summary_parts = []

        # Patterns from analysis
        if "patterns" in analyzed_data:
            patterns_summary = []
            for pattern in analyzed_data["patterns"][:10]:  # Limit for context window
                if isinstance(pattern, dict):
                    patterns_summary.append(
                        f"- {pattern.get('type', 'unknown')} pattern: {pattern.get('description', 'no description')}"
                    )

            if patterns_summary:
                summary_parts.append(
                    "IDENTIFIED PATTERNS:\n" + "\n".join(patterns_summary)
                )

        # Pattern summary statistics
        if "pattern_summary" in analyzed_data:
            summary = analyzed_data["pattern_summary"]
            stats = f"Total patterns: {summary.get('total_patterns', 0)}, High confidence: {summary.get('high_confidence_patterns', 0)}, Anomalies: {summary.get('anomalies_detected', 0)}"
            summary_parts.append(f"PATTERN STATISTICS:\n{stats}")

        # Existing insights if any
        if "insights" in analyzed_data:
            insights_summary = "\n".join(
                [f"- {insight}" for insight in analyzed_data["insights"][:5]]
            )
            summary_parts.append(f"EXISTING INSIGHTS:\n{insights_summary}")

        return (
            "\n\n".join(summary_parts) if summary_parts else "No analyzed data provided"
        )

    def _prepare_historical_summary(self, historical_context: dict[str, Any]) -> str:
        """Prepare historical context summary."""
        summary_parts = []

        # Historical patterns
        if historical_context.get("patterns"):
            patterns_count = len(historical_context["patterns"])
            summary_parts.append(f"Historical patterns available: {patterns_count}")

        # Recent activity trends
        if historical_context.get("recent_activity"):
            activity_count = len(historical_context["recent_activity"])
            summary_parts.append(f"Recent activity events: {activity_count}")

            # Sample recent activity
            recent_sample = []
            for activity in historical_context["recent_activity"][:5]:
                recent_sample.append(
                    f"- {activity.get('date')}: {activity.get('author')} - {activity.get('message', '')[:50]}..."
                )

            if recent_sample:
                summary_parts.append(
                    "RECENT ACTIVITY SAMPLE:\n" + "\n".join(recent_sample)
                )

        return (
            "\n\n".join(summary_parts)
            if summary_parts
            else "Limited historical data available"
        )

    def _prepare_entities_summary(self, entities_context: dict[str, Any]) -> str:
        """Prepare entities context summary."""
        summary_parts = []

        if entities_context.get("target_entities"):
            entities_list = ", ".join(entities_context["target_entities"])
            summary_parts.append(f"TARGET ENTITIES: {entities_list}")

        if entities_context.get("entity_patterns"):
            patterns_count = sum(
                len(patterns)
                for patterns in entities_context["entity_patterns"].values()
            )
            summary_parts.append(f"Entity-related patterns: {patterns_count}")

        return (
            "\n".join(summary_parts)
            if summary_parts
            else "No specific entity context available"
        )

    def _filter_insights_by_quality(
        self,
        insights_result: dict[str, Any],
        confidence_threshold: float,
        novelty_threshold: float,
    ) -> dict[str, Any]:
        """Filter insights based on quality thresholds."""
        filtered_result = insights_result.copy()

        try:
            # Filter insights
            if "insights" in filtered_result:
                high_quality_insights = []
                for insight in filtered_result["insights"]:
                    if isinstance(insight, dict):
                        confidence = insight.get("confidence_level", 0)
                        novelty = insight.get("novelty_score", 0)

                        if (
                            confidence >= confidence_threshold
                            and novelty >= novelty_threshold
                        ):
                            high_quality_insights.append(insight)

                filtered_result["insights"] = high_quality_insights

            # Filter predictions
            if "predictions" in filtered_result:
                high_confidence_predictions = []
                for prediction in filtered_result["predictions"]:
                    if isinstance(prediction, dict):
                        probability = prediction.get("probability", 0)

                        if probability >= confidence_threshold:
                            high_confidence_predictions.append(prediction)

                filtered_result["predictions"] = high_confidence_predictions

            # Update summary
            if "summary" in filtered_result:
                summary = filtered_result["summary"]
                summary["total_insights"] = len(filtered_result.get("insights", []))
                summary["high_confidence_insights"] = len(
                    [
                        i
                        for i in filtered_result.get("insights", [])
                        if i.get("confidence_level", 0) >= confidence_threshold
                    ]
                )

        except Exception as e:
            print(f"Failed to filter insights: {e}")

        return filtered_result

    def _get_empty_insights_result(self) -> dict[str, Any]:
        """Return empty insights result structure."""
        return {
            "insights": [],
            "predictions": [],
            "recommendations": [],
            "summary": {
                "total_insights": 0,
                "high_confidence_insights": 0,
                "critical_issues_predicted": 0,
                "opportunities_identified": 0,
                "immediate_actions_needed": 0,
                "average_prediction_confidence": 0.0,
                "novel_insights_percentage": 0.0,
            },
        }
