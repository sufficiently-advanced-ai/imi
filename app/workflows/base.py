"""
Workflow Base Classes - Foundation for explicit workflow patterns.

Provides standardized interface for complex operations that chain multiple tools
and services together in common patterns.
"""

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ..git_ops import GitOperations
from ..services.agent_tools import AgentToolRegistry
from ..services.claude_client import ClaudeClient
from ..services.file_cache import FileCache
from ..utils.fallback import partial_success


@dataclass
class WorkflowExecution:
    """Tracks execution metrics for workflow performance measurement."""

    workflow_name: str
    execution_id: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    quality_score: float | None = None
    steps_executed: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


class WorkflowResult(BaseModel):
    """Standardized result format for all workflows."""

    success: bool
    data: dict[str, Any]
    execution_time_ms: int
    quality_score: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = {}
    steps_executed: list[str] = []
    tools_used: list[str] = []


class WorkflowBase(ABC):
    """Base class for all workflow implementations."""

    def __init__(
        self,
        claude_client: ClaudeClient,
        git_ops: GitOperations,
        file_cache: FileCache,
        tool_registry: AgentToolRegistry,
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.tool_registry = tool_registry
        self.executions: list[WorkflowExecution] = []
        self._status_emitter = None  # Will be set when status streaming is enabled
        self._execution_id = None  # Current execution ID for status tracking
        self._total_steps = 0  # Total steps for progress calculation
        self._completed_steps = 0  # Completed steps for progress calculation

    @property
    @abstractmethod
    def name(self) -> str:
        """Workflow name for identification and logging."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this workflow does."""
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
    async def run(self, inputs: dict[str, Any]) -> WorkflowResult:
        """Execute the workflow with given inputs."""
        pass

    def _start_execution(self, inputs: dict[str, Any]) -> WorkflowExecution:
        """Start tracking workflow execution."""
        execution = WorkflowExecution(
            workflow_name=self.name,
            execution_id=str(uuid.uuid4()),
            start_time=datetime.now(),
            inputs=inputs,
        )
        self.executions.append(execution)
        return execution

    def _finish_execution(
        self, execution: WorkflowExecution, result: WorkflowResult
    ) -> None:
        """Finish tracking workflow execution."""
        execution.end_time = datetime.now()
        execution.duration_ms = (
            execution.end_time - execution.start_time
        ).total_seconds() * 1000
        execution.outputs = result.data
        execution.success = result.success
        execution.error = result.error
        execution.quality_score = result.quality_score
        execution.steps_executed = result.steps_executed
        execution.tools_used = result.tools_used

    async def _emit_status(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit a status event if streaming is enabled."""
        if self._status_emitter and self._execution_id:
            await self._status_emitter(self._execution_id, event_type, data)

    def _calculate_progress(self) -> float:
        """Calculate current progress percentage."""
        if self._total_steps == 0:
            return 0.0
        return (self._completed_steps / self._total_steps) * 100.0

    async def _emit_workflow_start(self) -> None:
        """Emit workflow start event."""
        await self._emit_status(
            "workflow_start", {"workflow": self.name, "total_steps": self._total_steps}
        )

    async def _emit_step_update(
        self,
        step_name: str,
        status: str,
        execution_time: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Emit step update event."""
        if status == "completed":
            self._completed_steps += 1

        event_data = {
            "step_name": step_name,
            "status": status,
            "progress": self._calculate_progress(),
            "completed_steps": self._completed_steps,
            "total_steps": self._total_steps,
        }

        if execution_time is not None:
            event_data["execution_time"] = execution_time

        if details:
            event_data["details"] = details

        await self._emit_status("step", event_data)

    async def _emit_tool_execution(
        self,
        tool_name: str,
        status: str,
        execution_time: float | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Emit tool execution event."""
        event_data = {"tool": tool_name, "status": status}

        if execution_time is not None:
            event_data["execution_time"] = execution_time

        if result:
            event_data["result_summary"] = {
                "success": result.get("success", False),
                "quality_score": result.get("quality_score"),
            }

        await self._emit_status("tool_execution", event_data)

    async def _emit_parallel_execution(self, branches: list[dict[str, Any]]) -> None:
        """Emit parallel execution event."""
        await self._emit_status("parallel_execution", {"branches": branches})

    async def _emit_error(
        self, error_message: str, stack_trace: str | None = None
    ) -> None:
        """Emit error event."""
        event_data = {"error_message": error_message}
        if stack_trace:
            event_data["stack_trace"] = stack_trace
        await self._emit_status("error", event_data)

    async def _emit_retry(
        self, retry_attempt: int, max_retries: int, reason: str
    ) -> None:
        """Emit retry event."""
        await self._emit_status(
            "retry",
            {
                "retry_attempt": retry_attempt,
                "max_retries": max_retries,
                "reason": reason,
            },
        )

    async def _emit_workflow_complete(
        self, total_execution_time: float, quality_score: float | None = None
    ) -> None:
        """Emit workflow completion event."""
        await self._emit_status(
            "workflow_complete",
            {
                "workflow": self.name,
                "total_execution_time": total_execution_time,
                "total_steps": self._total_steps,
                "tools_used": list(set(self.executions[-1].tools_used))
                if self.executions
                else [],
                "quality_score": quality_score,
            },
        )

    async def _emit_workflow_failed(
        self, error: str, total_execution_time: float
    ) -> None:
        """Emit workflow failure event."""
        await self._emit_status(
            "workflow_failed",
            {
                "workflow": self.name,
                "error": error,
                "total_execution_time": total_execution_time,
                "completed_steps": self._completed_steps,
                "total_steps": self._total_steps,
            },
        )

    async def _execute_tool(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> dict[str, Any]:
        """Execute a single tool and return its result data.

        Args:
            tool_name: Name of the tool to execute
            inputs: Input data for the tool
            context: Optional context data
            optional: If True, failures will return None instead of raising
        """
        start_time = time.time()

        # Emit tool start event
        await self._emit_tool_execution(tool_name, "started")

        try:
            result = await self.tool_registry.execute_tool(
                tool_name=tool_name,
                inputs=inputs,
                context=context,
                agent_name=f"workflow_{self.name}",
            )

            execution_time = time.time() - start_time

            if not result.success:
                await self._emit_tool_execution(
                    tool_name, "failed", execution_time, result.__dict__
                )
                if optional:
                    return None
                raise Exception(f"Tool {tool_name} failed: {result.error}")

            # Emit success event
            await self._emit_tool_execution(
                tool_name, "completed", execution_time, result.__dict__
            )

            return result.data
        except Exception as e:
            execution_time = time.time() - start_time
            await self._emit_tool_execution(tool_name, "failed", execution_time)
            await self._emit_error(str(e))

            if optional:
                # Log but don't fail
                print(f"Optional tool {tool_name} failed: {str(e)}")
                return None
            raise

    async def _execute_tool_chain(
        self,
        tool_chain: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        allow_partial_success: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a chain of tools and return their results.

        Args:
            tool_chain: List of tool configurations
            context: Optional context data
            allow_partial_success: If True, continue on failures and return partial results
        """
        if allow_partial_success:
            # Track partial success
            tracker = partial_success()
            results_data = []

            for i, tool_config in enumerate(tool_chain):
                try:
                    result = await self.tool_registry.execute_tool(
                        tool_name=tool_config["tool"],
                        inputs=tool_config.get("inputs", {}),
                        context=context,
                        agent_name=f"workflow_{self.name}",
                    )
                    if result.success:
                        tracker.add_success(
                            f"step_{i}_{tool_config['tool']}", result.data
                        )
                        results_data.append(result.data)
                    else:
                        tracker.add_failure(
                            f"step_{i}_{tool_config['tool']}", Exception(result.error)
                        )
                        if tool_config.get("optional", False):
                            results_data.append(None)
                        else:
                            results_data.append({"error": result.error})
                except Exception as e:
                    tracker.add_failure(
                        f"step_{i}_{tool_config.get('tool', 'unknown')}", e
                    )
                    if not tool_config.get("optional", False):
                        raise
                    results_data.append(None)

            return results_data
        else:
            # Original behavior - fail on any error
            results = await self.tool_registry.execute_chain(
                tool_chain=tool_chain,
                context=context,
                agent_name=f"workflow_{self.name}",
            )

            # Extract data from successful results
            results_data = []
            for result in results:
                if result.success:
                    results_data.append(result.data)
                else:
                    raise Exception(f"Tool chain failed: {result.error}")

            return results_data

    def get_performance_stats(self) -> dict[str, Any]:
        """Get performance statistics for this workflow."""
        if not self.executions:
            return {
                "total_executions": 0,
                "successful_executions": 0,
                "success_rate": 0.0,
                "average_execution_time_ms": 0.0,
                "average_quality_score": 0.0,
                "last_execution": None,
            }

        successful_executions = [e for e in self.executions if e.success]
        durations = [e.duration_ms for e in successful_executions if e.duration_ms]
        quality_scores = [
            e.quality_score for e in successful_executions if e.quality_score
        ]

        return {
            "total_executions": len(self.executions),
            "successful_executions": len(successful_executions),
            "success_rate": len(successful_executions) / len(self.executions)
            if self.executions
            else 0,
            "average_execution_time_ms": sum(durations) / len(durations)
            if durations
            else 0,
            "average_quality_score": sum(quality_scores) / len(quality_scores)
            if quality_scores
            else 0,
            "last_execution": self.executions[-1].start_time.isoformat()
            if self.executions
            else None,
        }


class WorkflowRegistry:
    """Central registry for all workflow implementations."""

    def __init__(
        self,
        claude_client: ClaudeClient,
        git_ops: GitOperations,
        file_cache: FileCache,
        tool_registry: AgentToolRegistry,
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.tool_registry = tool_registry
        self.workflows: dict[str, WorkflowBase] = {}

        self._register_core_workflows()

    def _register_core_workflows(self):
        """Register all core workflow implementations."""
        from .commit_enricher import CommitEnricherWorkflow
        from .document_analyzer import DocumentAnalyzerWorkflow
        from .meeting_processor import MeetingProcessorWorkflow

        workflows = [
            MeetingProcessorWorkflow(
                self.claude_client, self.git_ops, self.file_cache, self.tool_registry
            ),
            DocumentAnalyzerWorkflow(
                self.claude_client, self.git_ops, self.file_cache, self.tool_registry
            ),
            CommitEnricherWorkflow(
                self.claude_client, self.git_ops, self.file_cache, self.tool_registry
            ),
        ]

        for workflow in workflows:
            self.register_workflow(workflow)

    def register_workflow(self, workflow: WorkflowBase):
        """Register a new workflow in the registry."""
        self.workflows[workflow.name] = workflow

    def get_workflow(self, workflow_name: str) -> WorkflowBase | None:
        """Get a workflow by name."""
        return self.workflows.get(workflow_name)

    def list_workflows(self) -> list[dict[str, Any]]:
        """List all available workflows with their metadata."""
        return [
            {
                "name": workflow.name,
                "description": workflow.description,
                "input_schema": workflow.input_schema,
                "output_schema": workflow.output_schema,
                "performance": workflow.get_performance_stats(),
            }
            for workflow in self.workflows.values()
        ]

    async def execute_workflow(
        self, workflow_name: str, inputs: dict[str, Any]
    ) -> WorkflowResult:
        """Execute a workflow by name."""
        workflow = self.get_workflow(workflow_name)
        if not workflow:
            return WorkflowResult(
                success=False,
                data={},
                execution_time_ms=0,
                error=f"Workflow '{workflow_name}' not found",
            )

        return await workflow.run(inputs)

    def get_registry_stats(self) -> dict[str, Any]:
        """Get overall statistics for the workflow registry."""
        total_executions = sum(
            len(workflow.executions) for workflow in self.workflows.values()
        )
        successful_executions = sum(
            len([e for e in workflow.executions if e.success])
            for workflow in self.workflows.values()
        )

        return {
            "total_workflows": len(self.workflows),
            "total_executions": total_executions,
            "overall_success_rate": successful_executions / total_executions
            if total_executions > 0
            else 0,
            "workflow_performance": {
                workflow_name: workflow.get_performance_stats()
                for workflow_name, workflow in self.workflows.items()
            },
        }
