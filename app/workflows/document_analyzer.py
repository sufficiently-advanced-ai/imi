"""
Document Analyzer Workflow - Extract summaries and entities from documents.

This workflow orchestrates the analysis of general documents to extract
entities, summaries, patterns, and generate insights from any text content.
"""

import time
from datetime import datetime
from typing import Any

from .base import WorkflowBase, WorkflowResult


class DocumentAnalyzerWorkflow(WorkflowBase):
    """Workflow for analyzing documents to extract summaries, entities, and insights.

    This workflow provides comprehensive document analysis by extracting entities, generating
    summaries, identifying patterns and risks, and producing actionable insights from any text.

    Example Input:
        {
            "content": "Technical Architecture Review - Cloud Migration Strategy\\n\\n"
                      "Presented by: Alex Kumar, Chief Architect\\n"
                      "Date: January 10, 2025\\n\\n"
                      "We are proposing a phased migration to AWS over 6 months.\\n"
                      "Phase 1: Migrate customer database (Feb 2025)\\n"
                      "Phase 2: Move application servers (Apr 2025)\\n"
                      "Key risks: Data security during migration, potential downtime\\n"
                      "Budget: $450,000 allocated for Q1-Q2",
            "file_path": "/docs/cloud-migration-plan.md",
            "analysis_depth": "comprehensive",
            "focus_areas": ["entities", "risks", "patterns", "timeline"],
            "generate_summary": True
        }

    Example Output:
        {
            "summary": {
                "main_topic": "Cloud migration to AWS",
                "purpose": "Outline phased migration strategy",
                "key_points": [
                    "6-month phased migration approach",
                    "Customer database migration in Phase 1",
                    "$450,000 budget allocated"
                ],
                "document_type": "technical proposal"
            },
            "entities": {
                "entities": {
                    "people": ["Alex Kumar"],
                    "projects": ["Cloud Migration", "AWS Migration"],
                    "teams": ["Architecture Team"]
                }
            },
            "risks": {
                "risks": [
                    {
                        "description": "Data security during migration",
                        "severity": "high",
                        "mitigation": "Implement encryption in transit"
                    }
                ]
            },
            "timeline": {
                "events": [
                    {"date": "2025-02", "event": "Migrate customer database"},
                    {"date": "2025-04", "event": "Move application servers"}
                ]
            },
            "insights": {
                "insights": [
                    {
                        "insight": "Aggressive timeline may require additional resources",
                        "confidence_level": 0.85
                    }
                ]
            }
        }

    Common Errors and Solutions:
        1. Missing content error:
           - Error: "content is required"
           - Solution: Provide document text in the 'content' field

        2. Invalid analysis depth:
           - Error: "analysis_depth must be one of: basic, standard, comprehensive"
           - Solution: Use one of the allowed values for analysis_depth

        3. Summary generation failure:
           - Error: "Failed to generate document summary"
           - Solution: Ensure document has sufficient content (>100 characters) for analysis

        4. Tool chain timeout:
           - Error: "Workflow execution exceeded timeout"
           - Solution: Use "standard" or "basic" analysis_depth for large documents
    """

    @property
    def name(self) -> str:
        return "document_analyzer"

    @property
    def description(self) -> str:
        return "Analyze documents to extract entities, generate summaries, identify patterns, and produce insights"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Document content to analyze",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional file path for context",
                },
                "analysis_depth": {
                    "type": "string",
                    "enum": ["basic", "standard", "comprehensive"],
                    "description": "Depth of analysis to perform (default: standard)",
                    "default": "standard",
                },
                "focus_areas": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "entities",
                            "risks",
                            "patterns",
                            "decisions",
                            "timeline",
                        ],
                    },
                    "description": "Specific areas to focus analysis on (default: all)",
                    "default": ["entities", "risks", "patterns"],
                },
                "generate_summary": {
                    "type": "boolean",
                    "description": "Whether to generate a document summary (default: true)",
                    "default": True,
                },
            },
            "required": ["content"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "object",
                    "description": "Document summary and key points",
                },
                "entities": {
                    "type": "object",
                    "description": "People, projects, and teams mentioned in the document",
                },
                "risks": {
                    "type": "object",
                    "description": "Identified risks and concerns (if requested)",
                },
                "patterns": {
                    "type": "object",
                    "description": "Identified patterns and trends (if requested)",
                },
                "decisions": {
                    "type": "object",
                    "description": "Key decisions made (if requested)",
                },
                "timeline": {
                    "type": "object",
                    "description": "Timeline of events (if requested)",
                },
                "insights": {
                    "type": "object",
                    "description": "Generated insights and recommendations",
                },
                "analysis_summary": {
                    "type": "object",
                    "description": "High-level summary of the document analysis",
                },
            },
        }

    async def run(self, inputs: dict[str, Any]) -> WorkflowResult:
        """Execute the document analysis workflow."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            # Parse inputs
            content = inputs["content"]
            file_path = inputs.get("file_path", "")
            analysis_depth = inputs.get("analysis_depth", "standard")
            focus_areas = inputs.get(
                "focus_areas", ["entities", "commitments", "risks", "patterns"]
            )
            generate_summary = inputs.get("generate_summary", True)

            # Track execution steps and tools used
            steps_executed = []
            tools_used = []
            result_data = {}

            # Determine analysis scope based on depth
            if analysis_depth == "basic":
                focus_areas = ["entities"]
            elif analysis_depth == "comprehensive":
                focus_areas = [
                    "entities",
                    "risks",
                    "patterns",
                    "decisions",
                    "timeline",
                ]

            # Step 1: Always extract entities as foundation
            if "entities" in focus_areas or analysis_depth != "basic":
                steps_executed.append("extract_entities")
                entity_inputs = {"content": content}
                if file_path:
                    entity_inputs["file_path"] = file_path
                entities_data = await self._execute_tool(
                    "extract_entities", entity_inputs
                )
                tools_used.append("extract_entities")
                result_data["entities"] = entities_data

            # Step 2: Extract decisions if requested
            if "decisions" in focus_areas:
                steps_executed.append("extract_decisions")
                decisions_data = await self._execute_tool(
                    "extract_decisions", {"content": content}
                )
                tools_used.append("extract_decisions")
                result_data["decisions"] = decisions_data

            # Step 4: Risk analysis if requested
            if "risks" in focus_areas:
                steps_executed.append("extract_risks")
                risks_data = await self._execute_tool(
                    "extract_risks", {"content": content}
                )
                tools_used.append("extract_risks")
                result_data["risks"] = risks_data

            # Step 5: Pattern analysis if requested
            if "patterns" in focus_areas:
                steps_executed.append("extract_patterns")
                patterns_data = await self._execute_tool(
                    "extract_patterns", {"content": content}
                )
                tools_used.append("extract_patterns")
                result_data["patterns"] = patterns_data

            # Step 6: Timeline extraction if requested
            if "timeline" in focus_areas:
                steps_executed.append("build_timeline")
                timeline_data = await self._execute_tool(
                    "build_timeline", {"content": content}
                )
                tools_used.append("build_timeline")
                result_data["timeline"] = timeline_data

            # Step 7: Generate summary if requested
            if generate_summary:
                steps_executed.append("generate_summary")
                summary_data = await self._generate_document_summary(content, file_path)
                result_data["summary"] = summary_data

            # Step 8: Generate insights from combined analysis
            if analysis_depth in ["standard", "comprehensive"]:
                steps_executed.append("generate_insights")
                insights_data = await self._execute_tool(
                    "generate_insights", {"data": result_data}
                )
                tools_used.append("generate_insights")
                result_data["insights"] = insights_data

            # Generate analysis summary
            result_data["analysis_summary"] = self._generate_analysis_summary(
                result_data, analysis_depth, focus_areas, file_path
            )

            # Calculate quality score
            quality_score = self._calculate_quality_score(
                result_data, analysis_depth, focus_areas
            )

            execution_time_ms = int((time.time() - start_time) * 1000)

            result = WorkflowResult(
                success=True,
                data=result_data,
                execution_time_ms=execution_time_ms,
                quality_score=quality_score,
                metadata={
                    "file_path": file_path,
                    "analysis_depth": analysis_depth,
                    "focus_areas": focus_areas,
                    "generate_summary": generate_summary,
                    "content_length": len(content),
                },
                steps_executed=steps_executed,
                tools_used=tools_used,
            )

            self._finish_execution(execution, result)
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            result = WorkflowResult(
                success=False,
                data={},
                execution_time_ms=execution_time_ms,
                error=str(e),
                steps_executed=steps_executed,
                tools_used=tools_used,
            )

            self._finish_execution(execution, result)
            return result

    async def _generate_document_summary(
        self, content: str, file_path: str = ""
    ) -> dict[str, Any]:
        """Generate a comprehensive document summary."""
        # Use Claude to generate a structured summary
        summary_prompt = f"""
        Analyze the following document and provide a structured summary.

        Document content:
        {content[:2000]}{"..." if len(content) > 2000 else ""}

        Provide a summary that includes:
        1. Main topic and purpose
        2. Key points (3-5 bullet points)
        3. Important people mentioned
        4. Key decisions or outcomes
        5. Next steps or action items

        Respond in YAML format:
        ```yaml
        main_topic: "Brief description of the main topic"
        purpose: "Purpose or goal of the document"
        key_points:
          - "First key point"
          - "Second key point"
          - "Third key point"
        important_people: ["Person 1", "Person 2"]
        key_decisions: ["Decision 1", "Decision 2"]
        next_steps: ["Step 1", "Step 2"]
        document_type: "meeting notes/report/memo/etc"
        ```
        """

        messages = [{"role": "user", "content": summary_prompt}]
        response = await self.claude_client.generate_message(
            messages, operation="document_summary"
        )

        # Extract and parse YAML response
        if hasattr(response, "content"):
            content_data = response.content
            if isinstance(content_data, list) and len(content_data) > 0:
                response_text = (
                    content_data[0].text
                    if hasattr(content_data[0], "text")
                    else str(content_data[0])
                )
            else:
                response_text = str(content_data)
        else:
            response_text = str(response)

        # Parse YAML from response
        try:
            import yaml

            yaml_start = response_text.find("```yaml")
            if yaml_start != -1:
                yaml_end = response_text.find("```", yaml_start + 7)
                if yaml_end != -1:
                    yaml_content = response_text[yaml_start + 7 : yaml_end].strip()
                else:
                    yaml_content = response_text[yaml_start + 7 :].strip()
            else:
                # Try to find any code block
                code_start = response_text.find("```")
                if code_start != -1:
                    code_end = response_text.find("```", code_start + 3)
                    if code_end != -1:
                        yaml_content = response_text[code_start + 3 : code_end].strip()
                    else:
                        yaml_content = response_text[code_start + 3 :].strip()
                else:
                    yaml_content = response_text.strip()

            parsed_summary = yaml.safe_load(yaml_content)

            if isinstance(parsed_summary, dict):
                return {
                    "main_topic": parsed_summary.get("main_topic", ""),
                    "purpose": parsed_summary.get("purpose", ""),
                    "key_points": parsed_summary.get("key_points", []),
                    "important_people": parsed_summary.get("important_people", []),
                    "key_decisions": parsed_summary.get("key_decisions", []),
                    "next_steps": parsed_summary.get("next_steps", []),
                    "document_type": parsed_summary.get("document_type", "unknown"),
                    "file_path": file_path,
                    "summary_generated_at": datetime.now().isoformat(),
                }

        except Exception as e:
            print(f"Failed to parse summary YAML: {e}")

        # Fallback summary
        return {
            "main_topic": "Document analysis",
            "purpose": "Content analysis and extraction",
            "key_points": ["Document processed for entity and pattern extraction"],
            "important_people": [],
            "key_decisions": [],
            "next_steps": [],
            "document_type": "unknown",
            "file_path": file_path,
            "summary_generated_at": datetime.now().isoformat(),
            "note": "Automatic summary generation failed, using fallback",
        }

    def _generate_analysis_summary(
        self,
        result_data: dict,
        analysis_depth: str,
        focus_areas: list[str],
        file_path: str,
    ) -> dict[str, Any]:
        """Generate a high-level summary of the document analysis."""
        # Count extracted items
        entities_count = 0
        if "entities" in result_data:
            entities = result_data["entities"].get("entities", {})
            entities_count = (
                len(entities.get("people", []))
                + len(entities.get("projects", []))
                + len(entities.get("teams", []))
            )

        commitments_count = 0
        if "commitments" in result_data:
            commitments_count = len(result_data["commitments"].get("commitments", []))

        risks_count = 0
        if "risks" in result_data:
            risks_count = len(result_data["risks"].get("risks", []))

        patterns_count = 0
        if "patterns" in result_data:
            patterns_count = len(result_data["patterns"].get("patterns", []))

        insights_count = 0
        if "insights" in result_data:
            insights_count = len(result_data["insights"].get("insights", []))

        # Generate descriptive text
        file_descriptor = f"document {file_path}" if file_path else "document"
        summary_text = f"Analyzed {file_descriptor} with {analysis_depth} depth. "
        summary_text += f"Extracted {entities_count} entities"

        if commitments_count > 0:
            summary_text += f", {commitments_count} commitments"
        if risks_count > 0:
            summary_text += f", {risks_count} risks"
        if patterns_count > 0:
            summary_text += f", {patterns_count} patterns"
        if insights_count > 0:
            summary_text += f", and generated {insights_count} insights"

        return {
            "file_path": file_path,
            "analysis_depth": analysis_depth,
            "focus_areas": focus_areas,
            "processing_timestamp": datetime.now().isoformat(),
            "summary_text": summary_text,
            "metrics": {
                "entities_extracted": entities_count,
                "commitments_found": commitments_count,
                "risks_identified": risks_count,
                "patterns_detected": patterns_count,
                "insights_generated": insights_count,
            },
            "completeness": {
                "entities_analyzed": "entities" in result_data,
                "commitments_analyzed": "commitments" in result_data,
                "risks_analyzed": "risks" in result_data,
                "patterns_analyzed": "patterns" in result_data,
                "summary_generated": "summary" in result_data,
                "insights_generated": "insights" in result_data,
            },
        }

    def _calculate_quality_score(
        self, result_data: dict, analysis_depth: str, focus_areas: list[str]
    ) -> float:
        """Calculate quality score based on completeness and analysis depth."""
        score = 0.0

        # Base score for requested analyses completion (50%)
        requested_analyses = len(focus_areas)
        completed_analyses = 0

        if "entities" in focus_areas and "entities" in result_data:
            completed_analyses += 1
        if "commitments" in focus_areas and "commitments" in result_data:
            completed_analyses += 1
        if "risks" in focus_areas and "risks" in result_data:
            completed_analyses += 1
        if "patterns" in focus_areas and "patterns" in result_data:
            completed_analyses += 1
        if "decisions" in focus_areas and "decisions" in result_data:
            completed_analyses += 1
        if "timeline" in focus_areas and "timeline" in result_data:
            completed_analyses += 1

        if requested_analyses > 0:
            score += 0.5 * (completed_analyses / requested_analyses)

        # Quality bonuses based on extraction richness (30%)
        if "entities" in result_data:
            entities = result_data["entities"].get("entities", {})
            total_entities = (
                len(entities.get("people", []))
                + len(entities.get("projects", []))
                + len(entities.get("teams", []))
            )
            if total_entities > 0:
                score += 0.15

        if "commitments" in result_data:
            commitments = result_data["commitments"].get("commitments", [])
            if commitments:
                score += 0.15

        # Insights quality bonus (20%)
        if "insights" in result_data:
            insights = result_data["insights"].get("insights", [])
            if insights:
                score += 0.1
                # Bonus for high-quality insights
                high_quality_insights = sum(
                    1
                    for i in insights
                    if isinstance(i, dict) and i.get("confidence_level", 0) > 0.8
                )
                if high_quality_insights > 0:
                    score += 0.1 * (high_quality_insights / len(insights))

        # Analysis depth bonus (based on depth setting)
        if analysis_depth == "comprehensive":
            score += 0.1
        elif analysis_depth == "standard":
            score += 0.05

        return min(1.0, score)
