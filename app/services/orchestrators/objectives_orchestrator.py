"""
Agent Objectives Business Logic Orchestrator

Handles all business logic for the Agent Objective Framework that was
previously embedded in the objectives route handlers. Provides clean
separation between HTTP concerns and business logic.

Responsibilities:
- Objective CRUD operations with validation
- Execution management and background processing
- KPI evaluation and trend calculation
- Template management and instantiation
- Framework health checks and statistics
- Comprehensive objective validation

This orchestrator coordinates objective operations across multiple
services while maintaining proper error handling and logging.
"""

import uuid
from datetime import datetime
from typing import Any

from ...models import (
    AgentObjective,
    ObjectiveBoundaries,
    ObjectiveKPI,
    ObjectiveStatus,
)
from .base import BaseOrchestrator


class ObjectivesOrchestrator(BaseOrchestrator):
    """
    Orchestrates agent objective operations and business logic.

    This orchestrator handles the complete objective lifecycle from
    creation through execution and monitoring, coordinating across
    multiple services while maintaining clean separation of concerns.
    """

    def __init__(self):
        """Initialize the objectives orchestrator."""
        super().__init__()
        # Service dependencies will be injected
        self.engine = None
        self.storage = None
        self.kpi_tracker = None

    def inject_dependencies(self, engine, storage, kpi_tracker):
        """
        Inject service dependencies.

        Args:
            engine: Objective execution engine
            storage: Objective storage service
            kpi_tracker: KPI tracking service
        """
        self.engine = engine
        self.storage = storage
        self.kpi_tracker = kpi_tracker

    async def process(self, *args, **kwargs) -> Any:
        """
        Main processing method - not used for objectives orchestrator.
        Individual operations are called directly.
        """
        raise NotImplementedError("ObjectivesOrchestrator uses specific operation methods")

    async def create_objective(
        self,
        name: str,
        description: str,
        kpis: list[ObjectiveKPI],
        boundaries: ObjectiveBoundaries | None = None,
        tool_chain: list[dict[str, Any]] | None = None,
        priority: int | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> AgentObjective:
        """
        Create a new agent objective.

        Args:
            name: Human-readable name for the objective
            description: Detailed description of the objective
            kpis: Key performance indicators
            boundaries: Execution boundaries
            tool_chain: Preferred tool sequence
            priority: Priority (1=highest, 5=lowest)
            execution_context: Initial execution context

        Returns:
            Created AgentObjective

        Raises:
            Exception: If creation fails
        """
        operation = "create_objective"

        try:
            objective_id = str(uuid.uuid4())

            objective = AgentObjective(
                id=objective_id,
                name=name,
                description=description,
                kpis=kpis,
                boundaries=boundaries or ObjectiveBoundaries(),
                tool_chain=tool_chain or [],
                priority=priority or 1,
                execution_context=execution_context or {},
            )

            self._log_operation(operation, {
                "objective_id": objective_id,
                "name": name,
                "kpi_count": len(kpis),
                "status": "creating"
            })

            # Save to storage
            success = await self.storage.save_objective(objective)

            if not success:
                raise Exception("Failed to save objective to storage")

            self._log_operation(operation, {
                "objective_id": objective_id,
                "name": name,
                "status": "created"
            })

            return objective

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "name": name,
                "kpi_count": len(kpis) if kpis else 0
            })
            raise

    async def get_objective(self, objective_id: str) -> AgentObjective | None:
        """
        Retrieve a specific objective by ID.

        Args:
            objective_id: Unique identifier for the objective

        Returns:
            AgentObjective if found, None otherwise

        Raises:
            Exception: If retrieval fails
        """
        operation = "get_objective"

        try:
            objective = self.storage.load_objective(objective_id)

            self._log_operation(operation, {
                "objective_id": objective_id,
                "found": objective is not None
            })

            return objective

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_id": objective_id
            })
            raise

    async def update_objective(
        self,
        objective_id: str,
        updates: dict[str, Any]
    ) -> AgentObjective | None:
        """
        Update an existing objective.

        Args:
            objective_id: Unique identifier for the objective
            updates: Dictionary of fields to update

        Returns:
            Updated AgentObjective if successful, None if not found

        Raises:
            Exception: If update fails
        """
        operation = "update_objective"

        try:
            objective = self.storage.load_objective(objective_id)
            if not objective:
                return None

            # Apply updates (restrict to allowlist of fields)
            allowed_fields = {"name", "description", "kpis", "boundaries", "tool_chain", "priority", "execution_context", "status"}
            for field, value in updates.items():
                if field in allowed_fields and value is not None:
                    setattr(objective, field, value)

            objective.updated_at = datetime.utcnow()

            success = await self.storage.save_objective(objective)
            if not success:
                raise Exception("Failed to save updated objective")

            self._log_operation(operation, {
                "objective_id": objective_id,
                "updated_fields": list(updates.keys()),
                "status": "updated"
            })

            return objective

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_id": objective_id,
                "updates": list(updates.keys()) if updates else []
            })
            raise

    async def delete_objective(self, objective_id: str) -> bool:
        """
        Delete an objective.

        Args:
            objective_id: Unique identifier for the objective

        Returns:
            True if deleted successfully, False if not found

        Raises:
            Exception: If deletion fails
        """
        operation = "delete_objective"

        try:
            success = await self.storage.delete_objective(objective_id)

            self._log_operation(operation, {
                "objective_id": objective_id,
                "deleted": success
            })

            return success

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_id": objective_id
            })
            raise

    async def list_objectives(
        self,
        status_filter: ObjectiveStatus | None = None,
        page: int = 0,
        limit: int = 100
    ) -> dict[str, Any]:
        """
        List objectives with optional filtering and pagination.

        Args:
            status_filter: Optional status to filter by
            page: Page number for pagination
            limit: Number of items per page

        Returns:
            Dictionary with objectives list, total count, page info

        Raises:
            Exception: If listing fails
        """
        operation = "list_objectives"

        try:
            objective_ids = self.storage.list_objectives(status_filter=status_filter)

            # Apply pagination (clamp inputs)
            page = max(0, page)
            limit = max(1, limit)
            start_idx = page * limit
            end_idx = start_idx + limit
            paginated_ids = objective_ids[start_idx:end_idx]

            # Load objective details
            objectives = []
            for obj_id in paginated_ids:
                objective = self.storage.load_objective(obj_id)
                if objective:
                    objectives.append(objective)

            result = {
                "objectives": objectives,
                "total_count": len(objective_ids),
                "page": page,
                "limit": limit,
            }

            self._log_operation(operation, {
                "total_count": len(objective_ids),
                "page": page,
                "limit": limit,
                "returned_count": len(objectives)
            })

            return result

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "status_filter": status_filter.value if status_filter else None,
                "page": page,
                "limit": limit
            })
            raise

    async def execute_objective(
        self,
        objective_id: str,
        strategy,
        context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Execute an objective in the background.

        Args:
            objective_id: Unique identifier for the objective
            strategy: Execution strategy
            context: Additional execution context

        Returns:
            Dictionary with execution status

        Raises:
            Exception: If execution setup fails
        """
        operation = "execute_objective"

        try:
            objective = self.storage.load_objective(objective_id)
            if not objective:
                raise Exception(f"Objective {objective_id} not found")

            # Check if objective is already running
            if objective.status == ObjectiveStatus.IN_PROGRESS:
                raise Exception("Objective is already in progress")

            self._log_operation(operation, {
                "objective_id": objective_id,
                "strategy": strategy.value if hasattr(strategy, 'value') else str(strategy),
                "status": "starting_execution"
            })

            # Note: Actual background execution would be handled by FastAPI BackgroundTasks
            # This orchestrator just validates and logs the execution request

            return {
                "status": "execution_started",
                "objective_id": objective_id,
                "strategy": strategy.value if hasattr(strategy, 'value') else str(strategy),
                "message": "Objective execution started in background",
            }

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_id": objective_id,
                "strategy": strategy.value if hasattr(strategy, 'value') else str(strategy)
            })
            raise

    async def get_objective_status(self, objective_id: str) -> dict[str, Any]:
        """
        Get current status and progress of an objective.

        Args:
            objective_id: Unique identifier for the objective

        Returns:
            Dictionary with comprehensive status information

        Raises:
            Exception: If status retrieval fails
        """
        operation = "get_objective_status"

        try:
            objective = self.storage.load_objective(objective_id)
            if not objective:
                raise Exception(f"Objective {objective_id} not found")

            # Get KPI evaluation
            kpi_status = self.kpi_tracker.evaluate_objective_kpis(objective)

            # Get recent executions
            execution_ids = self.storage.list_executions(objective_id)
            recent_executions = []
            for exec_id in execution_ids[-5:]:  # Last 5 executions
                execution = self.storage.load_execution(exec_id)
                if execution:
                    recent_executions.append({
                        "execution_id": execution.execution_id,
                        "status": execution.status,
                        "start_time": execution.start_time.isoformat(),
                        "end_time": execution.end_time.isoformat() if execution.end_time else None,
                        "final_score": execution.final_score,
                    })

            result = {
                "objective": objective,
                "kpi_status": kpi_status,
                "recent_executions": recent_executions,
                "progress_percentage": objective.calculate_progress(),
                "weighted_score": objective.calculate_weighted_score(),
            }

            self._log_operation(operation, {
                "objective_id": objective_id,
                "status": objective.status.value if objective.status else "unknown",
                "recent_executions_count": len(recent_executions)
            })

            return result

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_id": objective_id
            })
            raise

    async def validate_objective(self, objective: AgentObjective) -> dict[str, Any]:
        """
        Validate an objective configuration.

        Args:
            objective: Objective to validate

        Returns:
            Validation result with errors, warnings, and suggestions

        Raises:
            Exception: If validation process fails
        """
        operation = "validate_objective"

        try:
            validation_result = {
                "valid": True,
                "errors": [],
                "warnings": [],
                "suggestions": [],
            }

            # Validate KPIs
            if not objective.kpis:
                validation_result["errors"].append("Objective must have at least one KPI")
                validation_result["valid"] = False

            total_weight = sum(kpi.weight for kpi in objective.kpis)
            if total_weight <= 0:
                validation_result["errors"].append("Total KPI weight must be positive")
                validation_result["valid"] = False

            # Check for duplicate KPI names
            kpi_names = [kpi.name for kpi in objective.kpis]
            if len(kpi_names) != len(set(kpi_names)):
                validation_result["errors"].append("KPI names must be unique")
                validation_result["valid"] = False

            # Validate operators
            valid_operators = [">", "<", ">=", "<=", "==", "!="]
            for kpi in objective.kpis:
                if kpi.operator not in valid_operators:
                    validation_result["errors"].append(
                        f"Invalid operator '{kpi.operator}' for KPI '{kpi.name}'"
                    )
                    validation_result["valid"] = False

            # Validate boundaries
            if objective.boundaries.timeout_seconds <= 0:
                validation_result["errors"].append("Timeout must be positive")
                validation_result["valid"] = False

            if objective.boundaries.max_retries < 0:
                validation_result["errors"].append("Max retries cannot be negative")
                validation_result["valid"] = False

            # Add suggestions
            if len(objective.kpis) == 1:
                validation_result["suggestions"].append(
                    "Consider adding multiple KPIs for better objective tracking"
                )

            if objective.boundaries.timeout_seconds > 3600:  # 1 hour
                validation_result["suggestions"].append(
                    "Consider reducing timeout for faster feedback"
                )

            self._log_operation(operation, {
                "objective_name": objective.name,
                "valid": validation_result["valid"],
                "errors_count": len(validation_result["errors"]),
                "warnings_count": len(validation_result["warnings"])
            })

            return validation_result

        except Exception as e:
            await self._handle_orchestrator_error(operation, e, {
                "objective_name": getattr(objective, 'name', 'unknown')
            })
            raise
