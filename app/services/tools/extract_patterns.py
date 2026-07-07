"""
Extract Patterns Tool - Advanced pattern recognition across multiple data sources.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# EntityBrain remains as a compatibility adapter; new service is EntityService
from app.domain.entities.services import EntityService

from ...config import settings
from ..agent_tools import AgentTool, ToolResult
from ..prompts import PromptService


class ExtractPatternsTool(AgentTool):
    """Advanced tool for cross-source pattern detection and temporal analysis."""

    def __init__(self, claude_client, git_ops, file_cache):
        super().__init__(claude_client, git_ops, file_cache)
        self.entity_brain = EntityService()
        self.prompt_service = PromptService()

    @property
    def name(self) -> str:
        return "extract_patterns"

    @property
    def description(self) -> str:
        return "Advanced pattern recognition across meetings, commits, documents with temporal analysis and cross-source correlation"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Primary content to analyze for patterns",
                },
                "additional_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional content sources for cross-source analysis",
                },
                "pattern_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "temporal",
                        "cross_source",
                        "entity_related",
                        "sentiment",
                        "frequency",
                        "anomaly",
                    ],
                    "description": "Types of patterns to detect",
                },
                "timeframe_days": {
                    "type": "integer",
                    "default": 30,
                    "description": "Number of days to look back for temporal analysis",
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific entities (people, projects, teams) to focus on",
                },
                "include_git_history": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include git commit patterns in analysis",
                },
                "novelty_threshold": {
                    "type": "number",
                    "default": 0.7,
                    "description": "Minimum novelty score for insights (0.0-1.0)",
                },
            },
            "required": ["content"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "patterns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "category": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                            "frequency": {"type": "integer"},
                            "confidence_score": {"type": "number"},
                            "novelty_score": {"type": "number"},
                            "severity": {"type": "string"},
                            "timeframe": {"type": "string"},
                            "entities_involved": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "sources": {"type": "array", "items": {"type": "string"}},
                            "implications": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "recommendations": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "related_patterns": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "pattern_summary": {
                    "type": "object",
                    "properties": {
                        "total_patterns": {"type": "integer"},
                        "high_confidence_patterns": {"type": "integer"},
                        "novel_insights": {"type": "integer"},
                        "risk_patterns": {"type": "integer"},
                        "opportunity_patterns": {"type": "integer"},
                        "trend_patterns": {"type": "integer"},
                        "cross_source_correlations": {"type": "integer"},
                        "anomalies_detected": {"type": "integer"},
                    },
                },
                "insights": {"type": "array", "items": {"type": "string"}},
                "predictions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prediction": {"type": "string"},
                            "confidence": {"type": "number"},
                            "timeframe": {"type": "string"},
                            "supporting_patterns": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }

    async def execute(
        self, inputs: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute advanced pattern extraction with cross-source analysis."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            # Extract inputs
            content = inputs.get("content", "")
            additional_sources = inputs.get("additional_sources", [])
            pattern_types = inputs.get(
                "pattern_types",
                [
                    "temporal",
                    "cross_source",
                    "entity_related",
                    "sentiment",
                    "frequency",
                    "anomaly",
                ],
            )
            timeframe_days = inputs.get("timeframe_days", 30)
            target_entities = inputs.get("entities", [])
            include_git = inputs.get("include_git_history", True)
            novelty_threshold = inputs.get("novelty_threshold", 0.7)

            # Gather cross-source data
            analysis_data = await self._gather_cross_source_data(
                content, additional_sources, timeframe_days, include_git
            )

            # Get relevant entities
            entities_context = await self._get_entities_context(
                target_entities, analysis_data
            )

            # Get historical patterns for context
            historical_context = await self._get_historical_patterns_for_context(
                timeframe_days
            )

            # Perform pattern analysis using Claude
            patterns_result = await self._analyze_patterns_with_claude(
                analysis_data,
                entities_context,
                pattern_types,
                timeframe_days,
                novelty_threshold,
                historical_context,
            )

            # Calculate quality score

            # Learn from detected patterns
            await self._learn_from_patterns(patterns_result, context)

            result = ToolResult(
                success=True,
                data=patterns_result,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

            reasoning = f"Analyzed {len(analysis_data.get('sources', []))} sources for {len(pattern_types)} pattern types, found {patterns_result.get('pattern_summary', {}).get('total_patterns', 0)} patterns with {patterns_result.get('pattern_summary', {}).get('novel_insights', 0)} novel insights"

        except Exception as e:
            result = ToolResult(
                success=False,
                data={},
                execution_time_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )
            reasoning = f"Pattern analysis failed: {str(e)}"

        self._finish_execution(execution, result, reasoning)
        return result

    async def _gather_cross_source_data(
        self,
        primary_content: str,
        additional_sources: list[str],
        timeframe_days: int,
        include_git: bool,
    ) -> dict[str, Any]:
        """Gather data from multiple sources for cross-source analysis."""
        sources_data = {
            "primary_content": primary_content,
            "additional_sources": additional_sources,
            "sources": ["primary"],
            "timeframe": f"{timeframe_days} days",
            "git_history": [],
            "meeting_files": [],
            "document_files": [],
        }

        # Get timeframe bounds
        end_date = datetime.now()
        start_date = end_date - timedelta(days=timeframe_days)

        try:
            # Get git history if requested
            if include_git:
                git_logs = await self._get_git_history(start_date, end_date)
                sources_data["git_history"] = git_logs
                if git_logs:
                    sources_data["sources"].append("git_commits")

            # Find recent meeting files
            meeting_files = await self._find_recent_files(
                "meeting-*.md", start_date, end_date
            )
            sources_data["meeting_files"] = meeting_files
            if meeting_files:
                sources_data["sources"].append("meetings")

            # Find other markdown documents
            doc_files = await self._find_recent_files(
                "*.md", start_date, end_date, exclude_meetings=True
            )
            sources_data["document_files"] = doc_files
            if doc_files:
                sources_data["sources"].append("documents")

        except Exception as e:
            print(f"Warning: Failed to gather some cross-source data: {e}")

        return sources_data

    async def _get_git_history(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Get git commit history for the specified timeframe."""
        try:
            # Format dates for git log
            since = start_date.strftime("%Y-%m-%d")
            until = end_date.strftime("%Y-%m-%d")

            # Get commit logs with format
            log_output = self.git_ops.get_commit_history(
                since=since,
                until=until,
                format="--pretty=format:%H|%an|%ad|%s",
                date_format="short",
            )

            commits = []
            for line in log_output.split("\n"):
                if line.strip():
                    parts = line.split("|", 3)
                    if len(parts) == 4:
                        commits.append(
                            {
                                "hash": parts[0],
                                "author": parts[1],
                                "date": parts[2],
                                "message": parts[3],
                            }
                        )

            return commits

        except Exception as e:
            print(f"Failed to get git history: {e}")
            return []

    async def _find_recent_files(
        self,
        pattern: str,
        start_date: datetime,
        end_date: datetime,
        exclude_meetings: bool = False,
    ) -> list[dict[str, Any]]:
        """Find files matching pattern within the timeframe."""
        try:
            repo_path = Path(self.git_ops.repo_path)
            files = []

            # Find files matching pattern
            if pattern.startswith("meeting-") and not exclude_meetings:
                # Meeting files
                for file_path in repo_path.glob("meeting-*.md"):
                    file_info = await self._get_file_info(
                        file_path, start_date, end_date
                    )
                    if file_info:
                        files.append(file_info)
            elif not exclude_meetings or not pattern.startswith("meeting-"):
                # Other markdown files
                for file_path in repo_path.glob("**/*.md"):
                    if exclude_meetings and file_path.name.startswith("meeting-"):
                        continue
                    file_info = await self._get_file_info(
                        file_path, start_date, end_date
                    )
                    if file_info:
                        files.append(file_info)

            return files

        except Exception as e:
            print(f"Failed to find recent files: {e}")
            return []

    async def _get_file_info(
        self, file_path: Path, start_date: datetime, end_date: datetime
    ) -> dict[str, Any] | None:
        """Get file information if it's within the timeframe."""
        try:
            # Get file modification time
            mod_time = datetime.fromtimestamp(file_path.stat().st_mtime)

            if start_date <= mod_time <= end_date:
                # Read file content (limited for performance)
                content = file_path.read_text(encoding="utf-8")[
                    :2000
                ]  # First 2000 chars

                return {
                    "path": str(file_path.relative_to(file_path.parent.parent)),
                    "modified": mod_time.isoformat(),
                    "content_preview": content,
                    "size": len(content),
                }

        except Exception as e:
            print(f"Failed to get file info for {file_path}: {e}")

        return None

    async def _get_entities_context(
        self, target_entities: list[str], analysis_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Get context about relevant entities."""
        entities_context = {
            "people": [],
            "projects": [],
            "teams": [],
            "relationships": [],
        }

        try:
            # If specific entities requested, get their details
            if target_entities:
                for entity_id in target_entities:
                    # Try to identify entity type and get details
                    entity_details = await self._get_entity_details(entity_id)
                    if entity_details:
                        entity_type = entity_details.get("type", "unknown")
                        if entity_type in entities_context:
                            entities_context[entity_type].append(entity_details)

            # Extract entities mentioned in the analysis data
            mentioned_entities = await self._extract_mentioned_entities(analysis_data)
            for entity_type, entities in mentioned_entities.items():
                entities_context[entity_type].extend(entities)

        except Exception as e:
            print(f"Failed to get entities context: {e}")

        return entities_context

    async def _get_entity_details(self, entity_id: str) -> dict[str, Any] | None:
        """Get details for a specific entity."""
        try:
            # This would integrate with EntityBrain to get entity details
            # For now, return basic structure
            return {
                "id": entity_id,
                "type": "unknown",
                "name": entity_id,
                "metadata": {},
            }
        except Exception as e:
            print(f"Failed to get entity details for {entity_id}: {e}")
            return None

    async def _extract_mentioned_entities(
        self, analysis_data: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        """Extract entities mentioned in the analysis data."""
        # This would use EntityBrain to extract entities from content
        # For now, return empty structure
        return {"people": [], "projects": [], "teams": []}

    async def _analyze_patterns_with_claude(
        self,
        analysis_data: dict[str, Any],
        entities_context: dict[str, Any],
        pattern_types: list[str],
        timeframe_days: int,
        novelty_threshold: float,
        historical_context: dict[str, Any] = None,
    ) -> dict[str, Any]:
        """Use Claude to analyze patterns in the gathered data."""
        try:
            # Load prompt template
            prompt_template = self.prompt_service.get_prompt("extract_patterns")

            # Prepare context data
            content_summary = self._prepare_content_summary(analysis_data)
            entities_summary = self._prepare_entities_summary(entities_context)
            timeframe_info = f"Analysis window: {timeframe_days} days ending {datetime.now().strftime('%Y-%m-%d')}"
            pattern_types_str = ", ".join(pattern_types)

            # Prepare historical context
            historical_summary = self._prepare_historical_summary(
                historical_context or {}
            )

            # Format the prompt
            formatted_prompt = prompt_template.replace(
                "<!-- Content to analyze gets injected here -->", content_summary
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Related entities (people, projects, teams) get injected here -->",
                entities_summary,
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Time window and context get injected here -->", timeframe_info
            )
            formatted_prompt = formatted_prompt.replace(
                "<!-- Requested pattern types get injected here -->", pattern_types_str
            )

            # Add historical context and novelty threshold instruction
            formatted_prompt += (
                f"\n\nHISTORICAL LEARNING CONTEXT:\n{historical_summary}"
            )
            formatted_prompt += f"\n\nIMPORTANT: Focus on patterns with novelty scores >= {novelty_threshold}. Prioritize non-obvious insights that humans would likely miss. Use historical context to avoid repeating previous patterns and to identify truly novel insights."

            # Make Claude API call
            messages = [{"role": "user", "content": formatted_prompt}]
            response = await self.claude_client.generate_message(
                messages=messages,
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=4000,
                temperature=0.3,
                operation="pattern_recognition",
            )

            # Parse JSON response
            response_text = response.content[0].text
            patterns_result = json.loads(response_text)

            return patterns_result

        except json.JSONDecodeError as e:
            print(f"Failed to parse Claude response as JSON: {e}")
            return {
                "patterns": [],
                "pattern_summary": {},
                "insights": [],
                "predictions": [],
            }
        except Exception as e:
            print(f"Failed to analyze patterns with Claude: {e}")
            return {
                "patterns": [],
                "pattern_summary": {},
                "insights": [],
                "predictions": [],
            }

    def _prepare_content_summary(self, analysis_data: dict[str, Any]) -> str:
        """Prepare a summary of all content for Claude analysis."""
        summary_parts = []

        # Primary content
        if analysis_data.get("primary_content"):
            summary_parts.append(
                f"PRIMARY CONTENT:\n{analysis_data['primary_content'][:1500]}"
            )

        # Additional sources
        for i, source in enumerate(analysis_data.get("additional_sources", [])):
            summary_parts.append(f"ADDITIONAL SOURCE {i+1}:\n{source[:1000]}")

        # Git history
        if analysis_data.get("git_history"):
            git_summary = "\n".join(
                [
                    f"- {commit['date']} by {commit['author']}: {commit['message']}"
                    for commit in analysis_data["git_history"][:20]
                ]
            )
            summary_parts.append(f"GIT COMMITS:\n{git_summary}")

        # Meeting files
        if analysis_data.get("meeting_files"):
            meetings_summary = "\n".join(
                [
                    f"- {file['path']} ({file['modified']}): {file['content_preview'][:200]}..."
                    for file in analysis_data["meeting_files"][:10]
                ]
            )
            summary_parts.append(f"RECENT MEETINGS:\n{meetings_summary}")

        # Document files
        if analysis_data.get("document_files"):
            docs_summary = "\n".join(
                [
                    f"- {file['path']} ({file['modified']}): {file['content_preview'][:200]}..."
                    for file in analysis_data["document_files"][:10]
                ]
            )
            summary_parts.append(f"RECENT DOCUMENTS:\n{docs_summary}")

        return "\n\n".join(summary_parts)

    def _prepare_entities_summary(self, entities_context: dict[str, Any]) -> str:
        """Prepare a summary of entities for Claude analysis."""
        summary_parts = []

        for entity_type, entities in entities_context.items():
            if entities:
                entities_list = ", ".join(
                    [
                        entity.get("name", entity.get("id", "unknown"))
                        for entity in entities[:10]
                    ]
                )
                summary_parts.append(f"{entity_type.upper()}: {entities_list}")

        return (
            "\n".join(summary_parts)
            if summary_parts
            else "No specific entities identified"
        )

    def _prepare_historical_summary(self, historical_context: dict[str, Any]) -> str:
        """Prepare historical context summary for Claude analysis."""
        summary_parts = []

        # Historical patterns summary
        historical_patterns = historical_context.get("historical_patterns", [])
        if historical_patterns:
            summary_parts.append(
                f"HISTORICAL PATTERNS ({len(historical_patterns)} recent):"
            )

            # Group by type for better summary
            pattern_types = {}
            for pattern in historical_patterns[:10]:  # Limit for context
                ptype = pattern.get("type", "unknown")
                if ptype not in pattern_types:
                    pattern_types[ptype] = []
                pattern_types[ptype].append(pattern)

            for ptype, patterns in pattern_types.items():
                avg_confidence = sum(p.get("confidence", 0) for p in patterns) / len(
                    patterns
                )
                verified_count = sum(1 for p in patterns if p.get("verified"))
                summary_parts.append(
                    f"- {ptype}: {len(patterns)} patterns, avg confidence {avg_confidence:.2f}, {verified_count} verified"
                )

        # Trend analysis summary
        trend_analysis = historical_context.get("trend_analysis", {})
        if trend_analysis.get("pattern_type_trends"):
            summary_parts.append("TREND ANALYSIS:")
            trends = trend_analysis["pattern_type_trends"]
            for trend_type, trend_data in list(trends.items())[:5]:  # Top 5 trends
                detection_rate = trend_data.get("detection_rate", 0)
                avg_confidence = trend_data.get("average_confidence", 0)
                summary_parts.append(
                    f"- {trend_type}: {detection_rate:.2f}/day, confidence {avg_confidence:.2f}"
                )

        # Learning insights summary
        learning_insights = historical_context.get("learning_insights", {})
        if learning_insights.get("learning_summary"):
            learning_summary = learning_insights["learning_summary"]
            total_learned = learning_summary.get("total_patterns_learned", 0)
            avg_accuracy = learning_summary.get("average_accuracy", 0)
            false_positive_rate = learning_summary.get("false_positive_rate", 0)

            summary_parts.append("LEARNING SUMMARY:")
            summary_parts.append(f"- Total patterns learned: {total_learned}")
            summary_parts.append(f"- Average accuracy: {avg_accuracy:.2f}")
            summary_parts.append(f"- False positive rate: {false_positive_rate:.2f}")

        return (
            "\n".join(summary_parts)
            if summary_parts
            else "No historical learning data available"
        )

    async def _learn_from_patterns(
        self, patterns_result: dict[str, Any], context: dict[str, Any] = None
    ) -> None:
        """Learn from detected patterns for future improvement."""
        try:
            patterns = patterns_result.get("patterns", [])

            for pattern in patterns:
                if isinstance(pattern, dict):
                    # Add historical context for learning
                    {
                        "detection_method": "cross_source_analysis",
                        "timeframe": context.get("timeframe") if context else None,
                        "sources_used": len(pattern.get("sources", [])),
                        "entities_count": len(pattern.get("entities_involved", [])),
                    }

                    # Learn from this pattern
            #                     self.pattern_learner.learn_from_pattern(pattern, learning_context)

            # Learn trends if available
            if "insights" in patterns_result:
                for insight in patterns_result["insights"]:
                    if "trend" in insight.lower():
                        {
                            "type": "insight_trend",
                            "description": insight,
                            "start_date": datetime.now().isoformat(),
                            "strength": 0.5,  # Default strength for insights
                            "direction": "emerging",
                        }
                        #                         self.pattern_learner.learn_trend(trend_data)

            # Periodically commit learning data (every 10 patterns)
            #             if len(self.pattern_learner.pattern_records) % 10 == 0:
            #                 await self.pattern_learner.commit_learning_data()

        except Exception as e:
            print(f"Failed to learn from patterns: {e}")

    async def _get_historical_patterns_for_context(
        self, timeframe_days: int
    ) -> dict[str, Any]:
        """Get historical patterns for context in analysis."""
        try:
            # Get historical patterns from learner
            #             historical_patterns = self.pattern_learner.get_historical_patterns(days_back=timeframe_days * 2)

            # Get trend analysis
            #             trend_analysis = self.pattern_learner.analyze_pattern_trends(timeframe_days)

            # Get learning insights
            #             learning_insights = self.pattern_learner.get_learning_insights()

            return {
                "historical_patterns": [],
                "trend_analysis": {},
                "learning_insights": {},
            }

        except Exception as e:
            print(f"Failed to get historical patterns: {e}")
            return {}

    def predict_pattern_success(self, pattern: dict[str, Any]) -> float:
        """Predict the likelihood of pattern success based on historical learning."""
        try:
            #             return self.pattern_learner.predict_pattern_accuracy(pattern)
            return 0.5  # Default moderate prediction
        except Exception as e:
            print(f"Failed to predict pattern success: {e}")
            return 0.5  # Default moderate prediction
