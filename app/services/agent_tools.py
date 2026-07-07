"""
Agent Tool Arsenal - Core tools for intelligent agent collaboration.

This module provides a standardized framework for agent tools that can be
combined to achieve complex objectives. All tools follow a consistent interface
and provide performance tracking.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ..git_ops import GitOperations
from .claude_client import ClaudeClient
from .file_cache import FileCache

# OpenTelemetry imports for manual instrumentation
try:
    from opentelemetry import trace

    OTEL_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    OTEL_AVAILABLE = False
    tracer = None


@dataclass
class ToolExecution:
    """Tracks execution metrics for tool performance measurement."""

    tool_name: str
    execution_id: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    # Enhanced fields for decision logging
    reasoning: str | None = None
    confidence: float | None = None
    context: dict[str, Any] = field(default_factory=dict)
    agent_name: str | None = None
    decision_type: str | None = None


class ToolResult(BaseModel):
    """Standardized result format for all agent tools."""

    success: bool
    data: dict[str, Any]
    execution_time_ms: int
    error: str | None = None
    metadata: dict[str, Any] = {}


class AgentTool(ABC):
    """Base class for all agent tools."""

    def __init__(
        self, claude_client: ClaudeClient, git_ops: GitOperations, file_cache: FileCache
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.executions: list[ToolExecution] = []
        self.decision_logger = None  # Will be injected by registry

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for identification and logging."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this tool does."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON schema defining expected inputs."""
        pass

    @property
    @abstractmethod
    def output_schema(self) -> dict[str, Any]:
        """JSON schema defining output format."""
        pass

    @abstractmethod
    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Execute the tool with given inputs."""
        pass

    def _start_execution(
        self,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> ToolExecution:
        """Start tracking tool execution with enhanced context."""
        execution = ToolExecution(
            tool_name=self.name,
            execution_id=str(uuid.uuid4()),
            start_time=datetime.now(),
            inputs=inputs,
            context=context or {},
            agent_name=agent_name or "unknown",
            decision_type=f"execute_{self.name}",
        )
        self.executions.append(execution)
        return execution

    def _finish_execution(
        self,
        execution: ToolExecution,
        result: ToolResult,
        reasoning: str | None = None,
    ) -> None:
        """Finish tracking tool execution with enhanced metrics."""
        execution.end_time = datetime.now()
        execution.duration_ms = (
            execution.end_time - execution.start_time
        ).total_seconds() * 1000
        execution.outputs = result.data
        execution.success = result.success
        execution.error = result.error
        execution.reasoning = reasoning

        # Log decision if logger is available
        if self.decision_logger:
            # This will be handled by the registry to avoid circular dependencies
            pass

    def set_decision_logger(self, logger):
        """Set the decision logger instance."""
        self.decision_logger = logger

    def get_performance_stats(self) -> dict[str, Any]:
        """Get performance statistics for this tool."""
        if not self.executions:
            return {
                "total_executions": 0,
                "successful_executions": 0,
                "success_rate": 0.0,
                "average_execution_time_ms": 0.0,
                "last_execution": None,
            }

        successful_executions = [e for e in self.executions if e.success]
        durations = [e.duration_ms for e in successful_executions if e.duration_ms]

        return {
            "total_executions": len(self.executions),
            "successful_executions": len(successful_executions),
            "success_rate": len(successful_executions) / len(self.executions)
            if self.executions
            else 0,
            "average_execution_time_ms": sum(durations) / len(durations)
            if durations
            else 0,
            "last_execution": self.executions[-1].start_time.isoformat()
            if self.executions
            else None,
        }


class AgentToolRegistry:
    """Central registry for all agent tools.

    Exposes extraction and analysis tools (extract_entities, extract_decisions,
    build_timeline, map_relationships, etc.) plus the graph CRUD tools used by
    chat_tools_mcp for graph mutations. Tools registered here are also exposed
    via the REST API at app/routes/agent_tools.py.

    Verb taxonomy and parameter conventions are documented in
    docs/mcp_tool_conventions.md. New tools should follow that doc.
    """

    def __init__(
        self, claude_client: ClaudeClient, git_ops: GitOperations, file_cache: FileCache
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.tools: dict[str, AgentTool] = {}

        # Initialize decision logger
        from .decision_logger import AgentDecisionLogger

        self.decision_logger = AgentDecisionLogger(claude_client, git_ops)

        self._register_core_tools()

    def _register_core_tools(self):
        """Register all core agent tools."""
        # Import and register tools here
        from .tools.build_timeline import BuildTimelineTool
        from .tools.compare_statements import CompareStatementsTool
        from .tools.detect_weak_signals import DetectWeakSignalsTool
        from .tools.extract_decisions import ExtractDecisionsTool
        from .tools.extract_entities import ExtractEntitiesTool
        from .tools.extract_patterns import ExtractPatternsTool
        from .tools.extract_risks import ExtractRisksTool
        from .tools.generate_insights import GenerateInsightsTool
        from .tools.graph_edge_tools import (
            AddEdgeTool,
            DeleteEdgeTool,
            UpdateEdgeTool,
        )
        from .tools.graph_node_tools import (
            AddNodeTool,
            DeleteNodeTool,
            MergeNodesTool,
            UpdateNodeTool,
        )
        from .tools.map_relationships import MapRelationshipsTool

        tools = [
            ExtractEntitiesTool(self.claude_client, self.git_ops, self.file_cache),
            ExtractDecisionsTool(self.claude_client, self.git_ops, self.file_cache),
            ExtractPatternsTool(self.claude_client, self.git_ops, self.file_cache),
            ExtractRisksTool(self.claude_client, self.git_ops, self.file_cache),
            CompareStatementsTool(self.claude_client, self.git_ops, self.file_cache),
            BuildTimelineTool(self.claude_client, self.git_ops, self.file_cache),
            MapRelationshipsTool(self.claude_client, self.git_ops, self.file_cache),
            GenerateInsightsTool(self.claude_client, self.git_ops, self.file_cache),
            DetectWeakSignalsTool(self.claude_client, self.git_ops, self.file_cache),
            # Graph maintenance tools
            AddNodeTool(self.claude_client, self.git_ops, self.file_cache),
            UpdateNodeTool(self.claude_client, self.git_ops, self.file_cache),
            DeleteNodeTool(self.claude_client, self.git_ops, self.file_cache),
            MergeNodesTool(self.claude_client, self.git_ops, self.file_cache),
            AddEdgeTool(self.claude_client, self.git_ops, self.file_cache),
            UpdateEdgeTool(self.claude_client, self.git_ops, self.file_cache),
            DeleteEdgeTool(self.claude_client, self.git_ops, self.file_cache),
        ]

        for tool in tools:
            self.register_tool(tool)

    def register_tool(self, tool: AgentTool):
        """Register a new tool in the registry."""
        tool.set_decision_logger(self.decision_logger)
        self.tools[tool.name] = tool

    def get_tool(self, tool_name: str) -> AgentTool | None:
        """Get a tool by name."""
        return self.tools.get(tool_name)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all available tools with their metadata."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
                "performance": tool.get_performance_stats(),
            }
            for tool in self.tools.values()
        ]

    async def execute_tool(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        agent_name: str = "system",
    ) -> ToolResult:
        """Execute a tool by name with decision logging."""
        # Create span if OpenTelemetry is available
        if OTEL_AVAILABLE and tracer:
            with tracer.start_as_current_span("agent_tool_execution") as span:
                span.set_attribute("tool.name", tool_name)
                span.set_attribute("tool.agent", agent_name)
                span.set_attribute("tool.execution_id", str(uuid.uuid4()))

                return await self._execute_tool_with_span(
                    tool_name, inputs, context, agent_name, span
                )
        else:
            return await self._execute_tool_with_span(
                tool_name, inputs, context, agent_name, None
            )

    async def _execute_tool_with_span(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        context: dict[str, Any] | None,
        agent_name: str,
        span: Any | None,
    ) -> ToolResult:
        """Internal method to execute tool with optional span."""
        tool = self.get_tool(tool_name)
        if not tool:
            if span:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR, f"Tool '{tool_name}' not found"
                    )
                )
            return ToolResult(
                success=False,
                data={},
                execution_time_ms=0,
                error=f"Tool '{tool_name}' not found",
            )

        # Start tracking execution
        datetime.now()
        execution_id = str(uuid.uuid4())

        if span:
            span.set_attribute("tool.execution_id", execution_id)

        # Execute the tool
        result = await tool.execute(inputs)

        # Add result attributes to span
        if span:
            span.set_attribute("tool.success", result.success)
            span.set_attribute("tool.execution_time_ms", result.execution_time_ms)
            if result.error:
                span.set_attribute("tool.error", result.error)
                span.set_status(trace.Status(trace.StatusCode.ERROR, result.error))

        # Create decision context for logging
        from .decision_logger import DecisionContext, ToolUsage

        decision_context = DecisionContext(
            file_path=context.get("file_path") if context else None,
            content_preview=context.get("content_preview") if context else None,
            user_request=context.get("user_request") if context else None,
            session_id=context.get("session_id") if context else None,
            metadata=context or {},
        )

        tool_usage = ToolUsage(
            tool_name=tool_name,
            execution_time_ms=result.execution_time_ms,
            success=result.success,
            inputs=inputs,
            outputs=result.data,
            error=result.error,
        )

        # Log the decision
        try:
            await self.decision_logger.log_decision(
                agent=agent_name,
                decision=f"execute_{tool_name}",
                tools_used=[tool_usage],
                context=decision_context,
                execution_id=execution_id,
                outcome="completed" if result.success else "failed",
            )
        except Exception as e:
            print(f"Failed to log decision: {e}")

        return result

    async def execute_chain(
        self,
        tool_chain: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        agent_name: str = "system",
    ) -> list[ToolResult]:
        """Execute a chain of tools with decision logging."""
        results = []
        tool_context = {}
        execution_id = str(uuid.uuid4())
        tools_used = []

        for step in tool_chain:
            tool_name = step["tool"]
            inputs = step.get("inputs", {})

            # Merge context from previous tools
            if "use_context" in step and step["use_context"]:
                inputs.update(tool_context)

            # Execute tool without individual logging (we'll log the whole chain)
            tool = self.get_tool(tool_name)
            if not tool:
                result = ToolResult(
                    success=False,
                    data={},
                    execution_time_ms=0,
                    error=f"Tool '{tool_name}' not found",
                )
            else:
                result = await tool.execute(inputs)

            results.append(result)

            # Track tool usage for decision logging
            from .decision_logger import ToolUsage

            tools_used.append(
                ToolUsage(
                    tool_name=tool_name,
                    execution_time_ms=result.execution_time_ms,
                    success=result.success,
                    inputs=inputs,
                    outputs=result.data,
                    error=result.error,
                )
            )

            # Update context with results for next tool
            if result.success:
                tool_context.update(result.data)
            else:
                # Stop chain execution on failure
                break

        # Log the entire chain as a single decision
        try:
            from .decision_logger import DecisionContext

            decision_context = DecisionContext(
                file_path=context.get("file_path") if context else None,
                content_preview=context.get("content_preview") if context else None,
                user_request=context.get("user_request") if context else None,
                session_id=context.get("session_id") if context else None,
                metadata=context or {},
            )

            chain_names = [step["tool"] for step in tool_chain]
            successful_tools = sum(1 for r in results if r.success)

            await self.decision_logger.log_decision(
                agent=agent_name,
                decision=f"execute_chain: {' -> '.join(chain_names)}",
                tools_used=tools_used,
                context=decision_context,
                execution_id=execution_id,
                outcome=f"completed {successful_tools}/{len(tool_chain)} tools",
            )
        except Exception as e:
            print(f"Failed to log chain decision: {e}")

        return results

    def get_registry_stats(self) -> dict[str, Any]:
        """Get overall statistics for the tool registry."""
        total_executions = sum(len(tool.executions) for tool in self.tools.values())
        successful_executions = sum(
            len([e for e in tool.executions if e.success])
            for tool in self.tools.values()
        )

        return {
            "total_tools": len(self.tools),
            "total_executions": total_executions,
            "overall_success_rate": successful_executions / total_executions
            if total_executions > 0
            else 0,
            "tool_performance": {
                tool_name: tool.get_performance_stats()
                for tool_name, tool in self.tools.items()
            },
        }

    def get_performance_summary(self) -> dict[str, Any]:
        """Get performance summary across all tools."""
        return {
            "total_tools": len(self.tools),
            "total_executions": sum(
                len(tool.executions) for tool in self.tools.values()
            ),
            "tool_stats": {
                tool_name: tool.get_performance_stats()
                for tool_name, tool in self.tools.items()
            },
        }

    def get_decision_logs(self) -> list[dict[str, Any]]:
        """Get current session decision logs."""
        return [log.to_dict() for log in self.decision_logger.get_session_logs()]

    def get_usage_patterns(self) -> dict[str, Any]:
        """Get tool usage patterns from decision logs."""
        return self.decision_logger.get_usage_patterns()

    async def commit_decision_logs(self, commit_message: str | None = None) -> bool:
        """Commit decision logs to git for audit trail."""
        success = await self.decision_logger.commit_logs_to_git(commit_message)
        if success:
            self.decision_logger.clear_session_logs()
        return success

    async def load_decision_history(self, days: int = 7) -> list[dict[str, Any]]:
        """Load decision history from git."""
        return await self.decision_logger.load_decision_history(days)
