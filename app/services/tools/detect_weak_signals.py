"""
Detect Weak Signals Tool - Early opportunity identification from subtle indicators.
"""

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from ..prompts import PromptService


class DetectWeakSignalsTool(AgentTool):
    """Advanced tool for detecting weak signals and early opportunity indicators."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)
        self.prompt_service = PromptService()

    @property
    def name(self) -> str:
        return "detect_weak_signals"

    @property
    def description(self) -> str:
        return "Detect weak signals and early indicators for opportunity identification and risk prediction"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Primary content to analyze for weak signals",
                },
                "historical_data": {
                    "type": "object",
                    "description": "Historical patterns and baselines for comparison",
                },
                "signal_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "opportunity",
                        "risk",
                        "trend_shift",
                        "behavioral_change",
                        "communication_pattern",
                    ],
                    "description": "Types of weak signals to detect",
                },
                "sensitivity_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "default": "medium",
                    "description": "Detection sensitivity (higher = more subtle signals)",
                },
                "timeframe_days": {
                    "type": "integer",
                    "default": 14,
                    "description": "Time window for trend analysis",
                },
                "entities_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific entities to monitor for signals",
                },
                "threshold_deviation": {
                    "type": "number",
                    "default": 0.15,
                    "description": "Minimum deviation from baseline to consider a signal (0.0-1.0)",
                },
            },
            "required": ["content"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "weak_signals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "signal_id": {"type": "string"},
                            "type": {"type": "string"},
                            "strength": {"type": "number"},
                            "confidence": {"type": "number"},
                            "direction": {"type": "string"},
                            "description": {"type": "string"},
                            "indicators": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "baseline_comparison": {"type": "object"},
                            "potential_outcomes": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "early_actions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "monitoring_metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "entities_involved": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "timeframe": {"type": "string"},
                            "urgency": {"type": "string"},
                        },
                    },
                },
                "signal_summary": {
                    "type": "object",
                    "properties": {
                        "total_signals": {"type": "integer"},
                        "opportunity_signals": {"type": "integer"},
                        "risk_signals": {"type": "integer"},
                        "high_strength_signals": {"type": "integer"},
                        "immediate_attention_needed": {"type": "integer"},
                        "average_confidence": {"type": "number"},
                    },
                },
                "trend_analysis": {
                    "type": "object",
                    "properties": {
                        "emerging_trends": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "declining_trends": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "anomalies": {"type": "array", "items": {"type": "string"}},
                        "correlation_insights": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "recommendations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "priority": {"type": "string"},
                            "target_entities": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "expected_impact": {"type": "string"},
                            "timeline": {"type": "string"},
                        },
                    },
                },
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute weak signal detection analysis."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            # Extract inputs
            content = inputs.get("content", "")
            historical_data = inputs.get("historical_data", {})
            signal_types = inputs.get(
                "signal_types",
                [
                    "opportunity",
                    "risk",
                    "trend_shift",
                    "behavioral_change",
                    "communication_pattern",
                ],
            )
            sensitivity = inputs.get("sensitivity_level", "medium")
            timeframe_days = inputs.get("timeframe_days", 14)
            entities_focus = inputs.get("entities_focus", [])
            threshold_deviation = inputs.get("threshold_deviation", 0.15)

            # Gather baseline and comparison data
            baseline_data = await self._establish_baseline(
                historical_data, timeframe_days
            )

            # Perform statistical analysis for deviation detection
            statistical_signals = await self._detect_statistical_deviations(
                content, baseline_data, threshold_deviation, timeframe_days
            )

            # Perform semantic analysis for subtle pattern changes
            semantic_signals = await self._detect_semantic_signals(
                content, historical_data, signal_types, sensitivity
            )

            # Combine and validate signals
            all_signals = statistical_signals + semantic_signals
            validated_signals = await self._validate_and_score_signals(
                all_signals, entities_focus
            )

            # Generate comprehensive analysis
            weak_signals_result = await self._generate_weak_signals_analysis(
                validated_signals, baseline_data, signal_types, timeframe_days
            )

            # Calculate quality score

            result = ToolResult(
                success=True,
                data=weak_signals_result,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

            reasoning = f"Detected {weak_signals_result.get('signal_summary', {}).get('total_signals', 0)} weak signals with {weak_signals_result.get('signal_summary', {}).get('high_strength_signals', 0)} high-strength indicators"

        except Exception as e:
            result = ToolResult(
                success=False,
                data={},
                execution_time_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )
            reasoning = f"Weak signal detection failed: {str(e)}"

        self._finish_execution(execution, result, reasoning)
        return result

    async def _establish_baseline(
        self, historical_data: dict[str, Any], timeframe_days: int
    ) -> dict[str, Any]:
        """Establish baseline metrics for comparison."""
        baseline = {
            "communication_frequency": {},
            "entity_mentions": {},
            "sentiment_patterns": {},
            "activity_levels": {},
            "timeframe": f"{timeframe_days} days baseline",
        }

        try:
            # Get historical activity for baseline
            end_date = datetime.now() - timedelta(days=timeframe_days)
            start_date = end_date - timedelta(
                days=timeframe_days * 2
            )  # Baseline period

            historical_activity = await self._get_historical_activity(
                start_date, end_date
            )
            baseline["activity_levels"] = self._calculate_activity_baselines(
                historical_activity
            )

            # Extract baselines from historical data if provided
            if historical_data:
                baseline.update(self._extract_baselines_from_data(historical_data))

        except Exception as e:
            print(f"Warning: Failed to establish complete baseline: {e}")

        return baseline

    async def _get_historical_activity(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Get historical activity for baseline calculation."""
        try:
            since = start_date.strftime("%Y-%m-%d")
            until = end_date.strftime("%Y-%m-%d")

            log_output = self.git_ops.get_commit_history(
                since=since,
                until=until,
                format="--pretty=format:%an|%ad|%s",
                date_format="short",
            )

            activity = []
            for line in log_output.split("\n"):
                if line.strip():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        activity.append(
                            {"author": parts[0], "date": parts[1], "message": parts[2]}
                        )

            return activity

        except Exception as e:
            print(f"Failed to get historical activity: {e}")
            return []

    def _calculate_activity_baselines(
        self, historical_activity: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Calculate baseline activity metrics."""
        if not historical_activity:
            return {}

        # Daily commit frequency
        daily_commits = Counter(activity["date"] for activity in historical_activity)
        avg_daily_commits = (
            sum(daily_commits.values()) / len(daily_commits) if daily_commits else 0
        )

        # Author activity distribution
        author_activity = Counter(
            activity["author"] for activity in historical_activity
        )

        # Message length patterns
        message_lengths = [len(activity["message"]) for activity in historical_activity]
        avg_message_length = (
            sum(message_lengths) / len(message_lengths) if message_lengths else 0
        )

        return {
            "avg_daily_commits": avg_daily_commits,
            "author_distribution": dict(author_activity),
            "avg_message_length": avg_message_length,
            "total_baseline_period_activity": len(historical_activity),
        }

    def _extract_baselines_from_data(
        self, historical_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract baseline patterns from historical data."""
        baselines = {}

        # Entity mention frequency baselines
        if "entity_mentions" in historical_data:
            baselines["entity_mentions"] = historical_data["entity_mentions"]

        # Communication pattern baselines
        if "communication_patterns" in historical_data:
            baselines["communication_patterns"] = historical_data[
                "communication_patterns"
            ]

        return baselines

    async def _detect_statistical_deviations(
        self,
        content: str,
        baseline_data: dict[str, Any],
        threshold: float,
        timeframe_days: int,
    ) -> list[dict[str, Any]]:
        """Detect statistical deviations from baseline patterns."""
        signals = []

        try:
            # Analyze current period activity
            end_date = datetime.now()
            start_date = end_date - timedelta(days=timeframe_days)

            current_activity = await self._get_historical_activity(start_date, end_date)
            current_metrics = self._calculate_activity_baselines(current_activity)

            # Compare with baseline
            baseline_activity = baseline_data.get("activity_levels", {})

            # Commit frequency deviation
            baseline_commits = baseline_activity.get("avg_daily_commits", 0)
            current_commits = current_metrics.get("avg_daily_commits", 0)

            if baseline_commits > 0:
                commit_deviation = (
                    abs(current_commits - baseline_commits) / baseline_commits
                )
                if commit_deviation >= threshold:
                    direction = (
                        "increase" if current_commits > baseline_commits else "decrease"
                    )
                    signals.append(
                        {
                            "type": "activity_deviation",
                            "strength": min(1.0, commit_deviation),
                            "direction": direction,
                            "description": f"Commit frequency {direction} of {commit_deviation:.1%} from baseline",
                            "indicators": [
                                f"Current: {current_commits:.1f}/day",
                                f"Baseline: {baseline_commits:.1f}/day",
                            ],
                            "baseline_comparison": {
                                "current": current_commits,
                                "baseline": baseline_commits,
                                "deviation": commit_deviation,
                            },
                        }
                    )

            # Author activity pattern changes
            baseline_authors = baseline_activity.get("author_distribution", {})
            current_authors = current_metrics.get("author_distribution", {})

            author_signals = self._detect_author_pattern_changes(
                baseline_authors, current_authors, threshold
            )
            signals.extend(author_signals)

            # Message pattern changes
            baseline_msg_length = baseline_activity.get("avg_message_length", 0)
            current_msg_length = current_metrics.get("avg_message_length", 0)

            if baseline_msg_length > 0:
                length_deviation = (
                    abs(current_msg_length - baseline_msg_length) / baseline_msg_length
                )
                if length_deviation >= threshold:
                    direction = (
                        "increase"
                        if current_msg_length > baseline_msg_length
                        else "decrease"
                    )
                    signals.append(
                        {
                            "type": "communication_pattern",
                            "strength": min(1.0, length_deviation),
                            "direction": direction,
                            "description": f"Message length {direction} of {length_deviation:.1%} from baseline",
                            "indicators": [
                                f"Current avg: {current_msg_length:.1f} chars",
                                f"Baseline avg: {baseline_msg_length:.1f} chars",
                            ],
                            "baseline_comparison": {
                                "current": current_msg_length,
                                "baseline": baseline_msg_length,
                                "deviation": length_deviation,
                            },
                        }
                    )

        except Exception as e:
            print(f"Failed to detect statistical deviations: {e}")

        return signals

    def _detect_author_pattern_changes(
        self,
        baseline_authors: dict[str, int],
        current_authors: dict[str, int],
        threshold: float,
    ) -> list[dict[str, Any]]:
        """Detect changes in author activity patterns."""
        signals = []

        all_authors = set(baseline_authors.keys()) | set(current_authors.keys())

        for author in all_authors:
            baseline_count = baseline_authors.get(author, 0)
            current_count = current_authors.get(author, 0)

            # New author signal
            if baseline_count == 0 and current_count > 0:
                signals.append(
                    {
                        "type": "behavioral_change",
                        "strength": 0.7,
                        "direction": "new_contributor",
                        "description": f"New contributor detected: {author}",
                        "indicators": [
                            f"{author} has {current_count} contributions in current period"
                        ],
                        "entities_involved": [author],
                        "baseline_comparison": {
                            "current": current_count,
                            "baseline": baseline_count,
                            "status": "new",
                        },
                    }
                )

            # Disappeared author signal
            elif baseline_count > 0 and current_count == 0:
                signals.append(
                    {
                        "type": "behavioral_change",
                        "strength": 0.6,
                        "direction": "contributor_dropout",
                        "description": f"Contributor absence detected: {author}",
                        "indicators": [
                            f"{author} had {baseline_count} baseline contributions but none recently"
                        ],
                        "entities_involved": [author],
                        "baseline_comparison": {
                            "current": current_count,
                            "baseline": baseline_count,
                            "status": "absent",
                        },
                    }
                )

            # Significant activity change
            elif baseline_count > 0:
                deviation = abs(current_count - baseline_count) / baseline_count
                if deviation >= threshold:
                    direction = (
                        "increase" if current_count > baseline_count else "decrease"
                    )
                    signals.append(
                        {
                            "type": "behavioral_change",
                            "strength": min(1.0, deviation),
                            "direction": direction,
                            "description": f"{author} activity {direction} of {deviation:.1%}",
                            "indicators": [
                                f"Current: {current_count}",
                                f"Baseline: {baseline_count}",
                            ],
                            "entities_involved": [author],
                            "baseline_comparison": {
                                "current": current_count,
                                "baseline": baseline_count,
                                "deviation": deviation,
                            },
                        }
                    )

        return signals

    async def _detect_semantic_signals(
        self,
        content: str,
        historical_data: dict[str, Any],
        signal_types: list[str],
        sensitivity: str,
    ) -> list[dict[str, Any]]:
        """Detect semantic weak signals using Claude analysis."""
        try:
            # Prepare prompt for weak signal detection
            prompt = self._create_weak_signals_prompt(
                content, historical_data, signal_types, sensitivity
            )

            # Make Claude API call
            messages = [{"role": "user", "content": prompt}]
            response = await self.claude_client.generate_message(
                messages=messages,
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=3000,
                temperature=0.3,  # Lower temperature for analytical precision
                operation="weak_signal_detection",
            )

            # Parse response
            response_text = response.content[0].text
            semantic_result = json.loads(response_text)

            return semantic_result.get("weak_signals", [])

        except Exception as e:
            print(f"Failed to detect semantic signals: {e}")
            return []

    def _create_weak_signals_prompt(
        self,
        content: str,
        historical_data: dict[str, Any],
        signal_types: list[str],
        sensitivity: str,
    ) -> str:
        """Create prompt for weak signal detection."""
        sensitivity_instructions = {
            "low": "Focus only on strong, obvious signals with clear evidence",
            "medium": "Detect moderate signals that might be overlooked in casual observation",
            "high": "Identify subtle, barely perceptible signals that require deep analysis to notice",
        }

        return f"""
You are an expert weak signal analyst specializing in early opportunity identification and risk prediction. Analyze the provided content for subtle indicators that most people would miss.

CONTENT TO ANALYZE:
{content[:2000]}

HISTORICAL CONTEXT:
{json.dumps(historical_data, indent=2)[:1000]}

DETECTION PARAMETERS:
- Signal types to focus on: {', '.join(signal_types)}
- Sensitivity level: {sensitivity} ({sensitivity_instructions.get(sensitivity, '')})

INSTRUCTIONS:
1. Look for subtle changes in language patterns, tone, or emphasis
2. Identify emerging themes that haven't fully manifested yet
3. Detect shifts in priorities, interests, or concerns
4. Notice changes in communication frequency or depth
5. Spot early indicators of relationship dynamics
6. Find hints of future opportunities or challenges
7. Identify behavioral pattern changes
8. Look for correlations between seemingly unrelated elements

Focus on signals that are:
- Early indicators (before others notice)
- Actionable (can lead to specific responses)
- Significant (could have meaningful impact)
- Non-obvious (require analytical insight to detect)

OUTPUT FORMAT (JSON):
{{
    "weak_signals": [
        {{
            "signal_id": "unique_identifier",
            "type": "opportunity|risk|trend_shift|behavioral_change|communication_pattern",
            "strength": 0.75,
            "confidence": 0.8,
            "direction": "positive|negative|neutral|emerging|declining",
            "description": "Clear description of the weak signal",
            "indicators": ["Specific evidence points supporting this signal"],
            "potential_outcomes": ["What this signal might lead to"],
            "early_actions": ["Actions that could be taken based on this signal"],
            "monitoring_metrics": ["What to watch to confirm or refute this signal"],
            "entities_involved": ["person_id", "project_id", "team_id"],
            "timeframe": "when this signal is relevant",
            "urgency": "low|medium|high|immediate"
        }}
    ]
}}
"""

    async def _validate_and_score_signals(
        self, signals: list[dict[str, Any]], entities_focus: list[str]
    ) -> list[dict[str, Any]]:
        """Validate and score detected signals."""
        validated_signals = []

        for signal in signals:
            if not isinstance(signal, dict):
                continue

            # Ensure required fields
            signal.setdefault("signal_id", f"signal_{len(validated_signals) + 1}")
            signal.setdefault("strength", 0.5)
            signal.setdefault("confidence", 0.5)
            signal.setdefault("type", "unknown")
            signal.setdefault("direction", "neutral")
            signal.setdefault("description", "No description provided")
            signal.setdefault("indicators", [])
            signal.setdefault("potential_outcomes", [])
            signal.setdefault("early_actions", [])
            signal.setdefault("monitoring_metrics", [])
            signal.setdefault("entities_involved", [])
            signal.setdefault("timeframe", "unknown")
            signal.setdefault("urgency", "medium")

            # Entity focus filtering
            if entities_focus:
                involved_entities = signal.get("entities_involved", [])
                if not any(entity in entities_focus for entity in involved_entities):
                    continue  # Skip signals not involving focus entities

            # Quality scoring
            validated_signals.append(signal)

        return validated_signals

    def _score_signal_quality(self, signal: dict[str, Any]) -> float:
        """Score the quality of a weak signal."""
        score = 0.0

        # Base score from strength and confidence
        strength = signal.get("strength", 0)
        confidence = signal.get("confidence", 0)
        score += (strength + confidence) / 2 * 0.4

        # Evidence quality
        indicators = signal.get("indicators", [])
        if indicators:
            score += min(0.2, len(indicators) * 0.05)

        # Actionability
        early_actions = signal.get("early_actions", [])
        if early_actions:
            score += min(0.15, len(early_actions) * 0.03)

        # Potential outcomes
        outcomes = signal.get("potential_outcomes", [])
        if outcomes:
            score += min(0.1, len(outcomes) * 0.02)

        # Monitoring capability
        metrics = signal.get("monitoring_metrics", [])
        if metrics:
            score += min(0.1, len(metrics) * 0.02)

        # Description quality
        description = signal.get("description", "")
        if len(description) > 20:
            score += 0.05

        return min(1.0, score)

    async def _generate_weak_signals_analysis(
        self,
        validated_signals: list[dict[str, Any]],
        baseline_data: dict[str, Any],
        signal_types: list[str],
        timeframe_days: int,
    ) -> dict[str, Any]:
        """Generate comprehensive weak signals analysis."""

        # Signal summary
        total_signals = len(validated_signals)
        opportunity_signals = len(
            [s for s in validated_signals if s.get("type") == "opportunity"]
        )
        risk_signals = len([s for s in validated_signals if s.get("type") == "risk"])
        high_strength_signals = len(
            [s for s in validated_signals if s.get("strength", 0) >= 0.7]
        )
        immediate_attention = len(
            [s for s in validated_signals if s.get("urgency") == "immediate"]
        )

        avg_confidence = (
            sum(s.get("confidence", 0) for s in validated_signals) / total_signals
            if total_signals > 0
            else 0
        )

        # Trend analysis
        emerging_trends = []
        declining_trends = []
        anomalies = []

        for signal in validated_signals:
            direction = signal.get("direction", "")
            signal_type = signal.get("type", "")
            description = signal.get("description", "")

            if direction in ["positive", "emerging"]:
                emerging_trends.append(f"{signal_type}: {description}")
            elif direction in ["negative", "declining"]:
                declining_trends.append(f"{signal_type}: {description}")
            elif signal_type == "anomaly" or "anomaly" in description.lower():
                anomalies.append(description)

        # Generate recommendations
        recommendations = self._generate_signal_recommendations(validated_signals)

        return {
            "weak_signals": validated_signals,
            "signal_summary": {
                "total_signals": total_signals,
                "opportunity_signals": opportunity_signals,
                "risk_signals": risk_signals,
                "high_strength_signals": high_strength_signals,
                "immediate_attention_needed": immediate_attention,
                "average_confidence": avg_confidence,
            },
            "trend_analysis": {
                "emerging_trends": emerging_trends[:10],  # Limit for readability
                "declining_trends": declining_trends[:10],
                "anomalies": anomalies[:5],
                "correlation_insights": self._identify_signal_correlations(
                    validated_signals
                ),
            },
            "recommendations": recommendations,
        }

    def _generate_signal_recommendations(
        self, signals: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Generate actionable recommendations from signals."""
        recommendations = []

        # High-priority signals
        high_priority_signals = [
            s for s in signals if s.get("urgency") in ["high", "immediate"]
        ]
        for signal in high_priority_signals:
            for action in signal.get("early_actions", []):
                recommendations.append(
                    {
                        "action": action,
                        "priority": signal.get("urgency", "medium"),
                        "target_entities": signal.get("entities_involved", []),
                        "expected_impact": f"Address {signal.get('type', 'signal')} with {signal.get('strength', 0):.0%} strength",
                        "timeline": signal.get("timeframe", "immediate"),
                    }
                )

        # Opportunity signals
        opportunity_signals = [s for s in signals if s.get("type") == "opportunity"]
        for signal in opportunity_signals[:3]:  # Top 3 opportunities
            recommendations.append(
                {
                    "action": f"Capitalize on {signal.get('description', 'opportunity')}",
                    "priority": "medium",
                    "target_entities": signal.get("entities_involved", []),
                    "expected_impact": "Potential competitive advantage",
                    "timeline": signal.get("timeframe", "short-term"),
                }
            )

        return recommendations

    def _identify_signal_correlations(self, signals: list[dict[str, Any]]) -> list[str]:
        """Identify correlations between signals."""
        correlations = []

        # Group signals by entities
        entity_signals = defaultdict(list)
        for signal in signals:
            for entity in signal.get("entities_involved", []):
                entity_signals[entity].append(signal)

        # Find entities with multiple signals
        for entity, entity_signal_list in entity_signals.items():
            if len(entity_signal_list) >= 2:
                signal_types = [s.get("type") for s in entity_signal_list]
                correlations.append(
                    f"{entity} shows multiple signals: {', '.join(set(signal_types))}"
                )

        # Find temporal correlations
        immediate_signals = [s for s in signals if s.get("urgency") == "immediate"]
        if len(immediate_signals) >= 2:
            correlations.append(
                "Multiple urgent signals detected simultaneously - possible systemic issue"
            )

        return correlations[:5]  # Limit for readability
