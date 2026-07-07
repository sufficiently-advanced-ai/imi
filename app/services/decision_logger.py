"""
Agent Decision Logger - Transparent reasoning system for agent actions.

This module provides comprehensive logging of agent decisions, tool usage patterns,
and reasoning chains for explainability and audit trails.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..git_ops import GitOperations
from .claude_client import ClaudeClient


@dataclass
class DecisionContext:
    """Context information for an agent decision."""

    file_path: str | None = None
    content_preview: str | None = None
    user_request: str | None = None
    session_id: str | None = None
    previous_decisions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolUsage:
    """Information about tool usage in a decision."""

    tool_name: str
    execution_time_ms: float
    success: bool
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DecisionLog:
    """Complete log entry for an agent decision."""

    timestamp: datetime
    agent: str
    decision: str
    reasoning: str
    tools_used: list[ToolUsage]
    confidence: float
    outcome: str
    context: DecisionContext
    execution_id: str
    total_execution_time_ms: float
    success: bool = True
    error: str | None = None

    def to_yaml(self) -> str:
        """Convert decision log to human-readable YAML format."""
        # Convert to dict and handle datetime serialization
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()

        # Format tools_used for readability
        if self.tools_used:
            data["tools_used"] = {
                tool.tool_name: {
                    "execution_time_ms": tool.execution_time_ms,
                    "success": tool.success,
                    "error": tool.error,
                }
                for tool in self.tools_used
            }

        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with proper serialization."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


class AgentDecisionLogger:
    """Central logging system for agent decisions and reasoning."""

    def __init__(
        self,
        claude_client: ClaudeClient,
        git_ops: GitOperations,
        log_dir: str = "logs/decisions",
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.log_dir = Path(log_dir)
        self.current_session_logs: list[DecisionLog] = []

        # Ensure log directory exists in the repo
        repo_log_dir = Path(git_ops.repo_path) / self.log_dir
        repo_log_dir.mkdir(parents=True, exist_ok=True)

    async def log_decision(
        self,
        agent: str,
        decision: str,
        tools_used: list[ToolUsage],
        context: DecisionContext,
        execution_id: str,
        outcome: str = "completed",
        confidence: float | None = None,
        custom_reasoning: str | None = None,
    ) -> DecisionLog:
        """Log an agent decision with full context and reasoning."""

        # Generate reasoning if not provided
        if custom_reasoning:
            reasoning = custom_reasoning
        else:
            reasoning = await self._generate_reasoning(
                agent, decision, tools_used, context
            )

        # Calculate confidence if not provided
        if confidence is None:
            confidence = self._calculate_confidence(tools_used, outcome)

        # Calculate total execution time
        total_time = sum(tool.execution_time_ms for tool in tools_used)

        # Create decision log
        decision_log = DecisionLog(
            timestamp=datetime.now(),
            agent=agent,
            decision=decision,
            reasoning=reasoning,
            tools_used=tools_used,
            confidence=confidence,
            outcome=outcome,
            context=context,
            execution_id=execution_id,
            total_execution_time_ms=total_time,
            success=all(tool.success for tool in tools_used),
        )

        # Add to current session
        self.current_session_logs.append(decision_log)

        # Store the decision log
        await self._store_decision_log(decision_log)

        return decision_log

    async def _generate_reasoning(
        self,
        agent: str,
        decision: str,
        tools_used: list[ToolUsage],
        context: DecisionContext,
    ) -> str:
        """Generate human-readable reasoning for the decision."""

        # Build context for reasoning generation
        reasoning_context = {
            "agent": agent,
            "decision": decision,
            "tools_used": [tool.tool_name for tool in tools_used],
            "context": {
                "file_path": context.file_path,
                "content_preview": context.content_preview[:200]
                if context.content_preview
                else None,
                "user_request": context.user_request,
            },
            "tool_results": {
                tool.tool_name: {
                    "success": tool.success,
                    "execution_time": tool.execution_time_ms,
                }
                for tool in tools_used
            },
        }

        prompt = f"""
        Generate a clear, human-readable explanation for this agent decision:

        Agent: {agent}
        Decision: {decision}
        Context: {json.dumps(reasoning_context, indent=2)}

        Provide a 2-3 sentence explanation that covers:
        1. What triggered this decision
        2. Why these specific tools were chosen
        3. What the agent was trying to achieve

        Make it understandable to both technical and non-technical stakeholders.
        """

        try:
            response = await self.claude_client.complete(prompt)
            return response.strip()
        except Exception:
            # Fallback to basic reasoning if Claude fails
            return f"Agent {agent} executed {decision} using tools: {', '.join(tool.tool_name for tool in tools_used)}. Context: {context.user_request or 'automated processing'}"

    def _calculate_confidence(self, tools_used: list[ToolUsage], outcome: str) -> float:
        """Calculate confidence score based on tool performance and outcome."""
        if not tools_used:
            return 0.5

        # Base confidence from tool success rates
        success_rate = sum(1 for tool in tools_used if tool.success) / len(tools_used)

        # Factor in quality scores

        # Factor in outcome
        outcome_confidence = 1.0 if outcome == "completed" else 0.6

        # Combine factors
        confidence = (success_rate * 0.4) + (0.7 * 0.4) + (outcome_confidence * 0.2)

        return min(1.0, max(0.0, confidence))

    async def _store_decision_log(self, decision_log: DecisionLog) -> None:
        """Store decision log to file system and git."""

        # Create daily log file
        date_str = decision_log.timestamp.strftime("%Y-%m-%d")
        log_file = self.log_dir / f"decisions-{date_str}.yaml"
        repo_log_file = Path(self.git_ops.repo_path) / log_file

        # Append to daily log
        with open(repo_log_file, "a", encoding="utf-8") as f:
            f.write("---\n")
            f.write(decision_log.to_yaml())
            f.write("\n")

        # Also store as individual JSON file for programmatic access
        json_file = self.log_dir / "json" / f"{decision_log.execution_id}.json"
        repo_json_file = Path(self.git_ops.repo_path) / json_file
        repo_json_file.parent.mkdir(parents=True, exist_ok=True)

        with open(repo_json_file, "w", encoding="utf-8") as f:
            json.dump(decision_log.to_dict(), f, indent=2)

    def get_session_logs(self) -> list[DecisionLog]:
        """Get all decision logs from current session."""
        return self.current_session_logs.copy()

    def get_usage_patterns(self) -> dict[str, Any]:
        """Analyze tool usage patterns from current session."""
        if not self.current_session_logs:
            return {
                "total_decisions": 0,
                "tool_usage": {},
                "success_rate": 0.0,
                "average_confidence": 0.0,
            }

        # Tool usage statistics
        tool_usage = {}
        for log in self.current_session_logs:
            for tool in log.tools_used:
                if tool.tool_name not in tool_usage:
                    tool_usage[tool.tool_name] = {
                        "count": 0,
                        "total_time_ms": 0,
                        "success_count": 0,
                    }

                stats = tool_usage[tool.tool_name]
                stats["count"] += 1
                stats["total_time_ms"] += tool.execution_time_ms
                if tool.success:
                    stats["success_count"] += 1

        # Calculate averages
        for _tool_name, stats in tool_usage.items():
            stats["success_rate"] = stats["success_count"] / stats["count"]
            stats["avg_time_ms"] = stats["total_time_ms"] / stats["count"]

        return {
            "total_decisions": len(self.current_session_logs),
            "tool_usage": tool_usage,
            "success_rate": sum(1 for log in self.current_session_logs if log.success)
            / len(self.current_session_logs),
            "average_confidence": sum(
                log.confidence for log in self.current_session_logs
            )
            / len(self.current_session_logs),
            "average_execution_time_ms": sum(
                log.total_execution_time_ms for log in self.current_session_logs
            )
            / len(self.current_session_logs),
        }

    async def commit_logs_to_git(self, commit_message: str | None = None) -> bool:
        """Commit decision logs to git for audit trail."""
        try:
            # Add log files to git
            self.git_ops.add_file(str(self.log_dir))

            if not commit_message:
                session_count = len(self.current_session_logs)
                commit_message = (
                    f"Add agent decision logs: {session_count} decisions logged"
                )

            # Commit with metadata about the session
            full_message = f"{commit_message}\n\nSession Statistics:\n"
            patterns = self.get_usage_patterns()
            full_message += f"- Total decisions: {patterns['total_decisions']}\n"
            full_message += f"- Success rate: {patterns['success_rate']:.2%}\n"
            full_message += (
                f"- Average confidence: {patterns['average_confidence']:.2f}\n"
            )

            self.git_ops.commit(full_message)
            return True

        except Exception as e:
            print(f"Failed to commit decision logs: {e}")
            return False

    def clear_session_logs(self) -> None:
        """Clear current session logs (typically after committing)."""
        self.current_session_logs.clear()

    async def load_decision_history(self, days: int = 7) -> list[DecisionLog]:
        """Load decision history from the last N days."""
        history = []

        for i in range(days):
            date = datetime.now().date() - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            log_file = (
                Path(self.git_ops.repo_path)
                / self.log_dir
                / f"decisions-{date_str}.yaml"
            )

            if log_file.exists():
                try:
                    with open(log_file, encoding="utf-8") as f:
                        # Parse YAML documents separated by ---
                        docs = yaml.safe_load_all(f)
                        for doc in docs:
                            if doc:
                                # Convert back to DecisionLog object
                                doc["timestamp"] = datetime.fromisoformat(
                                    doc["timestamp"]
                                )
                                # Note: This would need proper deserialization for full objects
                                # For now, just store as dict
                                history.append(doc)
                except Exception as e:
                    print(f"Failed to load decision history from {log_file}: {e}")

        return history
