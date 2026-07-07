"""
Commit Enricher Workflow - Enrich git commits with metadata and insights.

This workflow analyzes git commits and diffs to extract meaningful metadata,
identify patterns, and generate insights about code changes and development
activity.
"""

import time
from datetime import datetime
from typing import Any

from .base import WorkflowBase, WorkflowResult


class CommitEnricherWorkflow(WorkflowBase):
    """Workflow for enriching git commits with metadata and insights.

    This workflow analyzes git commits to extract metadata, identify patterns in code changes,
    assess risks, and generate development insights for better project understanding.

    Example Input:
        {
            "commit_hash": "a1b2c3d4e5f6789",
            "commit_message": "feat(api): Add user authentication endpoints\\n\\n"
                            "- Implement JWT token generation\\n"
                            "- Add login and logout endpoints\\n"
                            "- Include rate limiting for auth routes\\n\\n"
                            "Closes #142",
            "diff_content": "+from flask_jwt_extended import create_access_token\\n"
                          "+\\n"
                          "+@app.route('/api/auth/login', methods=['POST'])\\n"
                          "+def login():\\n"
                          "+    # Validate credentials\\n"
                          "+    token = create_access_token(identity=user_id)\\n"
                          "+    return {'token': token}\\n",
            "files_changed": ["app/auth.py", "app/routes.py", "tests/test_auth.py"],
            "author": "jane.developer@company.com",
            "timestamp": "2025-01-15T14:30:00Z",
            "branch": "feature/user-auth",
            "include_code_analysis": True,
            "include_risk_assessment": True
        }

    Example Output:
        {
            "commit_metadata": {
                "commit_hash": "a1b2c3d4e5f6789",
                "short_hash": "a1b2c3d4",
                "author": "jane.developer@company.com",
                "commit_type": "feature",
                "impact_scope": "moderate",
                "ticket_references": ["#142"],
                "follows_conventional_commits": True,
                "file_analysis": {
                    "total_files": 3,
                    "file_types": {"py": 3},
                    "most_common_type": "py"
                }
            },
            "entities": {
                "entities": {
                    "people": ["jane.developer"],
                    "projects": ["user authentication", "JWT implementation"],
                    "teams": ["API team"]
                }
            },
            "code_analysis": {
                "complexity_impact": "medium",
                "performance_implications": ["JWT generation may add 50ms latency"],
                "testing_needs": ["Unit tests for auth endpoints", "Integration tests for JWT flow"],
                "change_categories": ["security", "api", "authentication"]
            },
            "risk_assessment": {
                "risks": [
                    {
                        "description": "New authentication system needs security review",
                        "severity": "high",
                        "mitigation": "Schedule security audit before deployment"
                    }
                ]
            },
            "development_insights": {
                "insights": [
                    {
                        "insight": "Authentication implementation follows security best practices",
                        "confidence_level": 0.9,
                        "priority": "high"
                    }
                ]
            }
        }

    Common Errors and Solutions:
        1. Missing required fields:
           - Error: "commit_hash and commit_message are required"
           - Solution: Always provide at least commit_hash and commit_message fields

        2. Invalid timestamp format:
           - Error: "timestamp must be in ISO format"
           - Solution: Use ISO 8601 format like "2025-01-15T14:30:00Z"

        3. Empty diff analysis:
           - Error: "Code analysis requested but no diff_content provided"
           - Solution: Include diff_content when include_code_analysis is True

        4. YAML parsing error:
           - Error: "Failed to parse code analysis response"
           - Solution: Ensure diff_content is properly formatted and not truncated
    """

    @property
    def name(self) -> str:
        return "commit_enricher"

    @property
    def description(self) -> str:
        return "Enrich git commits with metadata, analyze code changes, and generate development insights"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "commit_hash": {
                    "type": "string",
                    "description": "Git commit hash to analyze",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message content",
                },
                "diff_content": {
                    "type": "string",
                    "description": "Git diff content for the commit",
                },
                "files_changed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files changed in the commit",
                },
                "author": {"type": "string", "description": "Commit author name/email"},
                "timestamp": {
                    "type": "string",
                    "description": "Commit timestamp (ISO format)",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name where commit was made",
                },
                "include_code_analysis": {
                    "type": "boolean",
                    "description": "Whether to perform detailed code analysis (default: true)",
                    "default": True,
                },
                "include_risk_assessment": {
                    "type": "boolean",
                    "description": "Whether to assess risks in the changes (default: true)",
                    "default": True,
                },
            },
            "required": ["commit_hash", "commit_message"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "commit_metadata": {
                    "type": "object",
                    "description": "Enhanced commit metadata and classification",
                },
                "entities": {
                    "type": "object",
                    "description": "People, projects, and components mentioned in commit",
                },
                "code_analysis": {
                    "type": "object",
                    "description": "Analysis of code changes and patterns (if requested)",
                },
                "risk_assessment": {
                    "type": "object",
                    "description": "Risk assessment of the changes (if requested)",
                },
                "development_insights": {
                    "type": "object",
                    "description": "Insights about development patterns and practices",
                },
                "enrichment_summary": {
                    "type": "object",
                    "description": "Summary of the commit enrichment process",
                },
            },
        }

    async def run(self, inputs: dict[str, Any]) -> WorkflowResult:
        """Execute the commit enrichment workflow."""
        execution = self._start_execution(inputs)
        start_time = time.time()

        try:
            # Parse inputs
            commit_hash = inputs["commit_hash"]
            commit_message = inputs["commit_message"]
            diff_content = inputs.get("diff_content", "")
            files_changed = inputs.get("files_changed", [])
            author = inputs.get("author", "")
            timestamp = inputs.get("timestamp", "")
            branch = inputs.get("branch", "")
            include_code_analysis = inputs.get("include_code_analysis", True)
            include_risk_assessment = inputs.get("include_risk_assessment", True)

            # Track execution steps and tools used
            steps_executed = []
            tools_used = []
            result_data = {}

            # Combine commit message and diff for analysis
            analysis_content = f"Commit Message:\n{commit_message}\n\n"
            if diff_content:
                analysis_content += f"Code Changes:\n{diff_content}\n\n"
            if files_changed:
                analysis_content += "Files Changed:\n" + "\n".join(files_changed)

            # Step 1: Generate enhanced commit metadata
            steps_executed.append("generate_commit_metadata")
            commit_metadata = await self._generate_commit_metadata(
                commit_hash, commit_message, files_changed, author, timestamp, branch
            )
            result_data["commit_metadata"] = commit_metadata

            # Step 2: Extract entities from commit message and diff
            steps_executed.append("extract_entities")
            entities_data = await self._execute_tool(
                "extract_entities", {"content": analysis_content}
            )
            tools_used.append("extract_entities")
            result_data["entities"] = entities_data

            # Step 3: Code analysis if requested and diff available
            if include_code_analysis and diff_content:
                steps_executed.append("analyze_code_patterns")
                code_analysis = await self._analyze_code_changes(
                    diff_content, files_changed
                )
                result_data["code_analysis"] = code_analysis

            # Step 4: Risk assessment if requested
            if include_risk_assessment:
                steps_executed.append("extract_risks")
                risks_data = await self._execute_tool(
                    "extract_risks", {"content": analysis_content}
                )
                tools_used.append("extract_risks")
                result_data["risk_assessment"] = risks_data

            # Step 5: Extract patterns from commit data
            steps_executed.append("extract_patterns")
            patterns_data = await self._execute_tool(
                "extract_patterns", {"content": analysis_content}
            )
            tools_used.append("extract_patterns")

            # Step 6: Generate development insights
            steps_executed.append("generate_insights")

            # Prepare data for insights generation
            insight_data = {
                "commit_metadata": commit_metadata,
                "entities": entities_data,
                "patterns": patterns_data,
            }

            if include_code_analysis and "code_analysis" in result_data:
                insight_data["code_analysis"] = result_data["code_analysis"]

            if include_risk_assessment:
                insight_data["risk_assessment"] = result_data["risk_assessment"]

            insights_data = await self._execute_tool(
                "generate_insights", {"data": insight_data}
            )
            tools_used.append("generate_insights")
            result_data["development_insights"] = insights_data

            # Generate enrichment summary
            result_data["enrichment_summary"] = self._generate_enrichment_summary(
                result_data, commit_hash, author, files_changed
            )

            # Calculate quality score
            quality_score = self._calculate_quality_score(
                result_data, include_code_analysis, include_risk_assessment
            )

            execution_time_ms = int((time.time() - start_time) * 1000)

            result = WorkflowResult(
                success=True,
                data=result_data,
                execution_time_ms=execution_time_ms,
                quality_score=quality_score,
                metadata={
                    "commit_hash": commit_hash,
                    "author": author,
                    "branch": branch,
                    "files_changed_count": len(files_changed),
                    "include_code_analysis": include_code_analysis,
                    "include_risk_assessment": include_risk_assessment,
                    "has_diff_content": bool(diff_content),
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

    async def _generate_commit_metadata(
        self,
        commit_hash: str,
        commit_message: str,
        files_changed: list[str],
        author: str,
        timestamp: str,
        branch: str,
    ) -> dict[str, Any]:
        """Generate enhanced metadata for the commit."""
        # Classify commit type based on message and files
        commit_type = self._classify_commit_type(commit_message, files_changed)

        # Determine impact scope
        impact_scope = self._determine_impact_scope(files_changed)

        # Extract ticket/issue references
        ticket_refs = self._extract_ticket_references(commit_message)

        # Analyze file types changed
        file_analysis = self._analyze_changed_files(files_changed)

        return {
            "commit_hash": commit_hash,
            "short_hash": commit_hash[:8] if commit_hash else "",
            "author": author,
            "timestamp": timestamp,
            "branch": branch,
            "commit_type": commit_type,
            "impact_scope": impact_scope,
            "ticket_references": ticket_refs,
            "file_analysis": file_analysis,
            "message_length": len(commit_message),
            "follows_conventional_commits": self._check_conventional_commits(
                commit_message
            ),
            "enrichment_timestamp": datetime.now().isoformat(),
        }

    def _classify_commit_type(
        self, commit_message: str, files_changed: list[str]
    ) -> str:
        """Classify the commit type based on message and files."""
        message_lower = commit_message.lower()

        # Check for conventional commit format
        if commit_message.startswith(("feat:", "feat(")):
            return "feature"
        elif commit_message.startswith(("fix:", "fix(")):
            return "bugfix"
        elif commit_message.startswith(("docs:", "docs(")):
            return "documentation"
        elif commit_message.startswith(("test:", "test(")):
            return "test"
        elif commit_message.startswith(("refactor:", "refactor(")):
            return "refactor"
        elif commit_message.startswith(("style:", "style(")):
            return "style"
        elif commit_message.startswith(("chore:", "chore(")):
            return "chore"

        # Fallback to keyword analysis
        if any(
            keyword in message_lower
            for keyword in ["add", "implement", "create", "new"]
        ):
            return "feature"
        elif any(
            keyword in message_lower for keyword in ["fix", "bug", "issue", "patch"]
        ):
            return "bugfix"
        elif any(
            keyword in message_lower for keyword in ["update", "modify", "change"]
        ):
            return "update"
        elif any(keyword in message_lower for keyword in ["remove", "delete", "clean"]):
            return "cleanup"
        elif any(keyword in message_lower for keyword in ["test", "spec"]):
            return "test"
        elif any(keyword in message_lower for keyword in ["doc", "readme", "comment"]):
            return "documentation"
        else:
            return "other"

    def _determine_impact_scope(self, files_changed: list[str]) -> str:
        """Determine the impact scope based on files changed."""
        if not files_changed:
            return "unknown"

        file_count = len(files_changed)

        if file_count == 1:
            return "minimal"
        elif file_count <= 5:
            return "moderate"
        elif file_count <= 15:
            return "significant"
        else:
            return "extensive"

    def _extract_ticket_references(self, commit_message: str) -> list[str]:
        """Extract ticket/issue references from commit message."""
        import re

        # Common patterns for ticket references
        patterns = [
            r"#(\d+)",  # GitHub issues: #123
            r"[A-Z]+-\d+",  # JIRA tickets: ABC-123
            r"ticket:?\s*(\d+)",  # ticket: 123
            r"issue:?\s*(\d+)",  # issue: 123
            r"closes?\s*#(\d+)",  # closes #123
            r"fixes?\s*#(\d+)",  # fixes #123
        ]

        references = []
        for pattern in patterns:
            matches = re.findall(pattern, commit_message, re.IGNORECASE)
            references.extend(matches)

        return list(set(references))  # Remove duplicates

    def _analyze_changed_files(self, files_changed: list[str]) -> dict[str, Any]:
        """Analyze the types and patterns of changed files."""
        if not files_changed:
            return {"total_files": 0}

        file_types = {}
        directories = set()

        for file_path in files_changed:
            # Extract file extension
            if "." in file_path:
                ext = file_path.split(".")[-1].lower()
                file_types[ext] = file_types.get(ext, 0) + 1

            # Extract directory
            if "/" in file_path:
                directory = file_path.split("/")[0]
                directories.add(directory)

        return {
            "total_files": len(files_changed),
            "file_types": file_types,
            "directories_affected": list(directories),
            "directory_count": len(directories),
            "most_common_type": max(file_types.keys(), key=file_types.get)
            if file_types
            else None,
        }

    def _check_conventional_commits(self, commit_message: str) -> bool:
        """Check if commit message follows conventional commits format."""
        import re

        # Conventional commits pattern: type(scope): description
        pattern = r"^(feat|fix|docs|style|refactor|test|chore)(\(.+\))?: .+"
        return bool(re.match(pattern, commit_message))

    async def _analyze_code_changes(
        self, diff_content: str, files_changed: list[str]
    ) -> dict[str, Any]:
        """Analyze code changes using Claude for deeper insights."""
        analysis_prompt = f"""
        Analyze the following git diff and provide insights about the code changes.

        Files changed: {', '.join(files_changed)}

        Diff content:
        {diff_content[:3000]}{"..." if len(diff_content) > 3000 else ""}

        Provide analysis in the following areas:
        1. Types of changes (additions, deletions, modifications)
        2. Code complexity impact
        3. Potential performance implications
        4. Testing considerations
        5. Code quality observations

        Respond in YAML format:
        ```yaml
        change_summary:
          lines_added: 0
          lines_removed: 0
          lines_modified: 0
        complexity_impact: "low/medium/high"
        performance_implications: ["implication 1", "implication 2"]
        testing_needs: ["test need 1", "test need 2"]
        quality_observations: ["observation 1", "observation 2"]
        change_categories: ["category1", "category2"]
        ```
        """

        messages = [{"role": "user", "content": analysis_prompt}]
        response = await self.claude_client.generate_message(
            messages, operation="commit_enrichment"
        )

        # Extract and parse response
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

        try:
            import yaml

            # Extract YAML from response
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

            parsed_analysis = yaml.safe_load(yaml_content)

            if isinstance(parsed_analysis, dict):
                return {
                    "change_summary": parsed_analysis.get("change_summary", {}),
                    "complexity_impact": parsed_analysis.get(
                        "complexity_impact", "unknown"
                    ),
                    "performance_implications": parsed_analysis.get(
                        "performance_implications", []
                    ),
                    "testing_needs": parsed_analysis.get("testing_needs", []),
                    "quality_observations": parsed_analysis.get(
                        "quality_observations", []
                    ),
                    "change_categories": parsed_analysis.get("change_categories", []),
                    "analysis_timestamp": datetime.now().isoformat(),
                }

        except Exception as e:
            print(f"Failed to parse code analysis YAML: {e}")

        # Fallback analysis
        return {
            "change_summary": {"analysis_failed": True},
            "complexity_impact": "unknown",
            "performance_implications": [],
            "testing_needs": [],
            "quality_observations": [],
            "change_categories": [],
            "analysis_timestamp": datetime.now().isoformat(),
            "note": "Automated code analysis failed",
        }

    def _generate_enrichment_summary(
        self, result_data: dict, commit_hash: str, author: str, files_changed: list[str]
    ) -> dict[str, Any]:
        """Generate a summary of the commit enrichment process."""
        commit_metadata = result_data.get("commit_metadata", {})
        entities = result_data.get("entities", {}).get("entities", {})

        # Count extracted information
        people_count = len(entities.get("people", []))
        projects_count = len(entities.get("projects", []))
        risks_count = len(result_data.get("risk_assessment", {}).get("risks", []))
        insights_count = len(
            result_data.get("development_insights", {}).get("insights", [])
        )

        summary_text = f"Enriched commit {commit_hash[:8]} by {author}. "
        summary_text += f"Changed {len(files_changed)} files, "
        summary_text += (
            f"identified {people_count} people and {projects_count} projects"
        )

        if risks_count > 0:
            summary_text += f", flagged {risks_count} risks"
        if insights_count > 0:
            summary_text += f", generated {insights_count} insights"

        return {
            "commit_hash": commit_hash,
            "author": author,
            "enrichment_timestamp": datetime.now().isoformat(),
            "summary_text": summary_text,
            "metrics": {
                "files_changed": len(files_changed),
                "people_mentioned": people_count,
                "projects_mentioned": projects_count,
                "risks_identified": risks_count,
                "insights_generated": insights_count,
                "commit_type": commit_metadata.get("commit_type", "unknown"),
                "impact_scope": commit_metadata.get("impact_scope", "unknown"),
            },
            "enrichment_completeness": {
                "metadata_generated": "commit_metadata" in result_data,
                "entities_extracted": "entities" in result_data,
                "code_analyzed": "code_analysis" in result_data,
                "risks_assessed": "risk_assessment" in result_data,
                "insights_generated": "development_insights" in result_data,
            },
        }

    def _calculate_quality_score(
        self,
        result_data: dict,
        include_code_analysis: bool,
        include_risk_assessment: bool,
    ) -> float:
        """Calculate quality score based on enrichment completeness."""
        score = 0.0

        # Base score for core components (40%)
        if "commit_metadata" in result_data:
            score += 0.2
        if "entities" in result_data:
            score += 0.2

        # Optional analysis bonuses (40%)
        if include_code_analysis and "code_analysis" in result_data:
            score += 0.2
        if include_risk_assessment and "risk_assessment" in result_data:
            score += 0.2

        # Quality bonuses (20%)
        if "development_insights" in result_data:
            insights = result_data["development_insights"].get("insights", [])
            if insights:
                score += 0.1
                # Bonus for high-quality insights
                high_quality = sum(
                    1
                    for i in insights
                    if isinstance(i, dict) and i.get("confidence_level", 0) > 0.7
                )
                if high_quality > 0:
                    score += 0.1 * (high_quality / len(insights))

        return min(1.0, score)
