"""
Agent Base Classes - Foundation for autonomous decision-making components.

Provides standardized interface for agents that make dynamic, context-aware
decisions, distinct from deterministic workflows.
"""

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..git_ops import GitOperations
from ..services.claude_client import ClaudeClient
from ..services.file_cache import FileCache


@dataclass
class DecisionContext:
    """Context information for agent decision-making."""

    inputs: dict[str, Any]
    background_context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)


class DecisionOutcome(BaseModel):
    """Result of an agent decision-making process."""

    decision: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    actions: list[str]
    metadata: dict[str, Any]

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("Confidence must be between 0.0 and 1.0")
        return v


@dataclass
class AgentExecution:
    """Tracks execution metrics for agent performance measurement."""

    agent_name: str
    execution_id: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    context: DecisionContext | None = None
    outcome: DecisionOutcome | None = None
    success: bool = True
    error: str | None = None


class AgentResult(BaseModel):
    """Standardized result format for all agent executions."""

    success: bool
    agent_name: str
    decision_outcome: DecisionOutcome | None = None
    execution_time_ms: int
    error: str | None = None
    metadata: dict[str, Any] = {}


class AgentBase(ABC):
    """Base class for all autonomous agent implementations."""

    def __init__(
        self, claude_client: ClaudeClient, git_ops: GitOperations, file_cache: FileCache
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.executions: list[AgentExecution] = []

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name for identification and logging."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this agent does."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        """List of capabilities this agent provides."""
        pass

    @abstractmethod
    async def make_decision(self, context: DecisionContext) -> DecisionOutcome:
        """Make a context-aware decision based on the given inputs."""
        pass

    async def execute_with_tracking(self, context: DecisionContext) -> AgentResult:
        """Execute agent decision-making with performance tracking."""
        execution = self._start_execution(context)
        start_time = time.time()

        try:
            # Make the decision
            outcome = await self.make_decision(context)

            execution_time_ms = max(1, int((time.time() - start_time) * 1000))

            result = AgentResult(
                success=True,
                agent_name=self.name,
                decision_outcome=outcome,
                execution_time_ms=execution_time_ms,
                metadata={
                    "execution_id": execution.execution_id,
                    "context_size": len(str(context.inputs)),
                    "confidence": outcome.confidence,
                    "actions_count": len(outcome.actions),
                },
            )

            self._finish_execution(execution, result, outcome)
            return result

        except Exception as e:
            execution_time_ms = max(1, int((time.time() - start_time) * 1000))
            result = AgentResult(
                success=False,
                agent_name=self.name,
                decision_outcome=None,
                execution_time_ms=execution_time_ms,
                error=str(e),
                metadata={
                    "execution_id": execution.execution_id,
                    "context_size": len(str(context.inputs)),
                },
            )

            self._finish_execution(execution, result, None, str(e))
            return result

    def _start_execution(self, context: DecisionContext) -> AgentExecution:
        """Start tracking agent execution."""
        execution = AgentExecution(
            agent_name=self.name,
            execution_id=str(uuid.uuid4()),
            start_time=datetime.now(),
            context=context,
        )
        self.executions.append(execution)
        return execution

    def _finish_execution(
        self,
        execution: AgentExecution,
        result: AgentResult,
        outcome: DecisionOutcome | None,
        error: str | None = None,
    ) -> None:
        """Finish tracking agent execution."""
        execution.end_time = datetime.now()
        execution.duration_ms = (
            execution.end_time - execution.start_time
        ).total_seconds() * 1000
        execution.outcome = outcome
        execution.success = result.success
        execution.error = error

    def get_performance_stats(self) -> dict[str, Any]:
        """Get performance statistics for this agent."""
        if not self.executions:
            return {
                "total_executions": 0,
                "successful_executions": 0,
                "success_rate": 0.0,
                "average_execution_time_ms": 0.0,
                "average_confidence": 0.0,
                "last_execution": None,
            }

        successful_executions = [e for e in self.executions if e.success]
        durations = [e.duration_ms for e in successful_executions if e.duration_ms]
        confidences = [
            e.outcome.confidence
            for e in successful_executions
            if e.outcome and e.outcome.confidence is not None
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
            "average_confidence": sum(confidences) / len(confidences)
            if confidences
            else 0,
            "last_execution": self.executions[-1].start_time.isoformat()
            if self.executions
            else None,
        }


class AgentRegistry:
    """Central registry for all agent implementations."""

    def __init__(
        self, claude_client: ClaudeClient, git_ops: GitOperations, file_cache: FileCache
    ):
        self.claude_client = claude_client
        self.git_ops = git_ops
        self.file_cache = file_cache
        self.agents: dict[str, AgentBase] = {}

    def register_agent(self, agent: AgentBase):
        """Register a new agent in the registry."""
        self.agents[agent.name] = agent

    def get_agent(self, agent_name: str) -> AgentBase | None:
        """Get an agent by name."""
        return self.agents.get(agent_name)

    def list_agents(self) -> list[dict[str, Any]]:
        """List all available agents with their metadata."""
        return [
            {
                "name": agent.name,
                "description": agent.description,
                "capabilities": agent.capabilities,
                "performance": agent.get_performance_stats(),
            }
            for agent in self.agents.values()
        ]

    async def execute_agent(
        self, agent_name: str, context: DecisionContext
    ) -> AgentResult:
        """Execute an agent by name."""
        agent = self.get_agent(agent_name)
        if not agent:
            return AgentResult(
                success=False,
                agent_name=agent_name,
                decision_outcome=None,
                execution_time_ms=0,
                error=f"Agent '{agent_name}' not found",
            )

        return await agent.execute_with_tracking(context)

    async def execute_chain(self, chain: list[dict[str, Any]]) -> list[AgentResult]:
        """Execute a chain of agents with context passing."""
        results = []
        previous_outcome = None

        for step in chain:
            agent_name = step["agent"]
            step_inputs = step.get("inputs", {})

            # Build context for this step
            context = DecisionContext(
                inputs=step_inputs,
                background_context=step.get("background_context", {}),
                constraints=step.get("constraints", {}),
                goals=step.get("goals", []),
            )

            # Add previous outcome to context if available
            if previous_outcome:
                context.background_context["previous_decision"] = {
                    "decision": previous_outcome.decision,
                    "confidence": previous_outcome.confidence,
                    "actions": previous_outcome.actions,
                    "metadata": previous_outcome.metadata,
                }

            # Execute this step
            result = await self.execute_agent(agent_name, context)
            results.append(result)

            # If step failed, break the chain
            if not result.success:
                break

            # Store outcome for next step
            previous_outcome = result.decision_outcome

        return results

    def get_registry_stats(self) -> dict[str, Any]:
        """Get overall statistics for the agent registry."""
        total_executions = sum(len(agent.executions) for agent in self.agents.values())
        successful_executions = sum(
            len([e for e in agent.executions if e.success])
            for agent in self.agents.values()
        )

        return {
            "total_agents": len(self.agents),
            "total_executions": total_executions,
            "overall_success_rate": successful_executions / total_executions
            if total_executions > 0
            else 0,
            "agent_performance": {
                agent_name: agent.get_performance_stats()
                for agent_name, agent in self.agents.items()
            },
        }
