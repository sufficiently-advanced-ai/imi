"""
Workflow API Routes - HTTP endpoints for explicit workflow patterns.

Provides RESTful interface for executing the three core workflows:
meeting processing, document analysis, and commit enrichment.

Also provides GET /api/workflows endpoint for listing available workflow
configurations (Issue #693).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models.workflow import WorkflowConfig
from ..services.workflow_loader import get_workflow
from ..services.workflow_loader import list_workflows as loader_list_workflows
from ..workflows.base import WorkflowRegistry, WorkflowResult
from .sse_status import emit_status_event

# Default workflow ID for the system
DEFAULT_WORKFLOW_ID = "imi-demo"


def get_workflow_configs() -> list[WorkflowConfig]:
    """Get all available workflow configurations.

    Returns:
        List of WorkflowConfig objects for all workflows in config/workflows/.
    """
    workflow_ids = loader_list_workflows()
    return [get_workflow(wid) for wid in workflow_ids]


class MeetingProcessorRequest(BaseModel):
    """Request model for meeting processor workflow."""

    content: str
    meeting_id: str | None = None
    reference_date: str | None = None
    include_patterns: bool = True
    include_risks: bool = True


class DocumentAnalyzerRequest(BaseModel):
    """Request model for document analyzer workflow."""

    content: str
    file_path: str | None = None
    analysis_depth: str = "standard"  # basic, standard, comprehensive
    focus_areas: list[str] = ["entities", "commitments", "risks", "patterns"]
    generate_summary: bool = True


class CommitEnricherRequest(BaseModel):
    """Request model for commit enricher workflow."""

    commit_hash: str
    commit_message: str
    diff_content: str | None = None
    files_changed: list[str] = []
    author: str | None = None
    timestamp: str | None = None
    branch: str | None = None
    include_code_analysis: bool = True
    include_risk_assessment: bool = True


class WorkflowListResponse(BaseModel):
    """Response model for workflow listing."""

    workflows: list[dict[str, Any]]
    total_count: int


class WorkflowStatsResponse(BaseModel):
    """Response model for workflow statistics."""

    registry_stats: dict[str, Any]
    workflow_performance: dict[str, Any]


class WorkflowSummary(BaseModel):
    """Summary of a workflow configuration for API response."""

    workflow_id: str
    name: str
    description: str


class AvailableWorkflowsResponse(BaseModel):
    """Response model for GET /api/workflows endpoint (Issue #693)."""

    workflows: list[WorkflowSummary]
    default_workflow_id: str


router = APIRouter()


class WorkflowExecuteRequest(BaseModel):
    """Generic workflow execution request with SSE support."""

    workflow: str
    input_data: dict[str, Any]
    stream_status: bool = False
    execution_id: str | None = None


# Global registry instance
_workflow_registry = None


def get_workflow_registry() -> WorkflowRegistry:
    """Get configured workflow registry instance."""
    global _workflow_registry
    if _workflow_registry is None:
        try:
            # Use the global instances from main module
            from ..git_ops import git_ops
            from ..services.agent_tools import AgentToolRegistry
            from ..services.claude_client import get_claude_client
            from ..services.file_cache import FileCache

            claude_client = get_claude_client()
            file_cache = FileCache()

            # Get or create tool registry
            tool_registry = AgentToolRegistry(claude_client, git_ops, file_cache)

            _workflow_registry = WorkflowRegistry(
                claude_client, git_ops, file_cache, tool_registry
            )
        except Exception as e:
            print(f"Failed to initialize WorkflowRegistry: {e}")
            # For now, return a mock registry if initialization fails
            _workflow_registry = MockWorkflowRegistry()
    return _workflow_registry


class MockWorkflowRegistry:
    """Mock registry for testing when real initialization fails."""

    def __init__(self):
        self.workflows = {}

    def list_workflows(self):
        return [
            {
                "name": "meeting_processor",
                "description": "Mock meeting processor workflow",
                "input_schema": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "object"}},
                },
                "performance": {"total_executions": 0},
            },
            {
                "name": "document_analyzer",
                "description": "Mock document analyzer workflow",
                "input_schema": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"analysis": {"type": "object"}},
                },
                "performance": {"total_executions": 0},
            },
            {
                "name": "commit_enricher",
                "description": "Mock commit enricher workflow",
                "input_schema": {
                    "type": "object",
                    "properties": {"commit_hash": {"type": "string"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"enrichment": {"type": "object"}},
                },
                "performance": {"total_executions": 0},
            },
        ]

    async def execute_workflow(self, workflow_name: str, inputs):
        return WorkflowResult(
            success=False,
            data={},
            execution_time_ms=0,
            error="Workflow registry not properly initialized",
        )


# ==================== Available Workflows Configuration Endpoint (Issue #693) ====================


async def list_available_workflows() -> dict[str, Any]:
    """List all available workflow configurations.

    Returns workflow summaries (id, name, description) without internal details.
    Used by frontend to populate workflow selection UI.

    Returns:
        Dict with 'workflows' list and 'default_workflow_id'.
    """
    configs = get_workflow_configs()

    workflows = [
        {
            "workflow_id": config.workflow_id,
            "name": config.name,
            "description": config.description
        }
        for config in configs
    ]

    return {
        "workflows": workflows,
        "default_workflow_id": DEFAULT_WORKFLOW_ID
    }


@router.get("/api/workflows", response_model=AvailableWorkflowsResponse)
async def get_available_workflows() -> AvailableWorkflowsResponse:
    """GET /api/workflows - List available workflow configurations.

    Returns a list of workflow configurations for frontend consumption.
    Each workflow includes workflow_id, name, and description.

    Issue #693: Add backend API endpoint to list available workflows.
    """
    result = await list_available_workflows()
    return AvailableWorkflowsResponse(**result)


# ==================== Core Workflow Endpoints ====================


@router.post("/workflows/execute")
async def execute_workflow(
    request: WorkflowExecuteRequest,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
) -> WorkflowResult:
    """Execute a workflow with optional SSE status streaming."""
    import uuid

    try:
        # Get the workflow
        workflow = registry.get_workflow(request.workflow)
        if not workflow:
            raise HTTPException(
                status_code=404, detail=f"Workflow '{request.workflow}' not found"
            )

        # Set up execution ID and status emitter if streaming requested
        execution_id = request.execution_id or str(uuid.uuid4())

        if request.stream_status:
            # Set up the status emitter for this workflow
            workflow._status_emitter = emit_status_event
            workflow._execution_id = execution_id
            workflow._completed_steps = 0

            # Emit initial status
            await emit_status_event(
                execution_id,
                "workflow_start",
                {"workflow": request.workflow, "execution_id": execution_id},
            )

        # Execute the workflow
        result = await workflow.run(request.input_data)

        # Add execution ID to result metadata
        result.metadata["execution_id"] = execution_id

        return result

    except Exception as e:
        # Emit error if streaming
        if request.stream_status and execution_id:
            await emit_status_event(
                execution_id,
                "workflow_failed",
                {"workflow": request.workflow, "error": str(e)},
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(registry: WorkflowRegistry = Depends(get_workflow_registry)):
    """List all available workflows with their schemas and performance metrics."""
    workflows = registry.list_workflows()

    return WorkflowListResponse(workflows=workflows, total_count=len(workflows))


@router.get("/workflows/{workflow_name}")
async def get_workflow_info(
    workflow_name: str, registry: WorkflowRegistry = Depends(get_workflow_registry)
):
    """Get detailed information about a specific workflow."""
    workflow = registry.get_workflow(workflow_name)
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_name}' not found"
        )

    return {
        "name": workflow.name,
        "description": workflow.description,
        "input_schema": workflow.input_schema,
        "output_schema": workflow.output_schema,
        "performance": workflow.get_performance_stats(),
    }


@router.get("/workflows/stats", response_model=WorkflowStatsResponse)
async def get_workflow_stats(
    registry: WorkflowRegistry = Depends(get_workflow_registry),
):
    """Get comprehensive statistics for all workflows in the registry."""
    registry_stats = registry.get_registry_stats()

    return WorkflowStatsResponse(
        registry_stats=registry_stats,
        workflow_performance=registry_stats.get("workflow_performance", {}),
    )


# ==================== Specific Workflow Endpoints ====================


@router.post("/workflows/meeting-processor")
async def process_meeting(
    request: MeetingProcessorRequest,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
) -> WorkflowResult:
    """Process meeting notes to extract commitments, entities, and insights."""
    try:
        inputs = {
            "content": request.content,
            "meeting_id": request.meeting_id,
            "reference_date": request.reference_date,
            "include_patterns": request.include_patterns,
            "include_risks": request.include_risks,
        }

        result = await registry.execute_workflow("meeting_processor", inputs)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows/document-analyzer")
async def analyze_document(
    request: DocumentAnalyzerRequest,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
) -> WorkflowResult:
    """Analyze documents to extract entities, summaries, and insights."""
    try:
        inputs = {
            "content": request.content,
            "file_path": request.file_path,
            "analysis_depth": request.analysis_depth,
            "focus_areas": request.focus_areas,
            "generate_summary": request.generate_summary,
        }

        result = await registry.execute_workflow("document_analyzer", inputs)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows/commit-enricher")
async def enrich_commit(
    request: CommitEnricherRequest,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
) -> WorkflowResult:
    """Enrich git commits with metadata and development insights."""
    try:
        inputs = {
            "commit_hash": request.commit_hash,
            "commit_message": request.commit_message,
            "diff_content": request.diff_content,
            "files_changed": request.files_changed,
            "author": request.author,
            "timestamp": request.timestamp,
            "branch": request.branch,
            "include_code_analysis": request.include_code_analysis,
            "include_risk_assessment": request.include_risk_assessment,
        }

        result = await registry.execute_workflow("commit_enricher", inputs)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Workflow Performance and Monitoring ====================


@router.get("/workflows/{workflow_name}/performance")
async def get_workflow_performance(
    workflow_name: str, registry: WorkflowRegistry = Depends(get_workflow_registry)
):
    """Get detailed performance metrics for a specific workflow."""
    workflow = registry.get_workflow(workflow_name)
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_name}' not found"
        )

    return {
        "workflow_name": workflow_name,
        "performance": workflow.get_performance_stats(),
        "recent_executions": [
            {
                "execution_id": exec.execution_id,
                "start_time": exec.start_time.isoformat(),
                "duration_ms": exec.duration_ms,
                "success": exec.success,
                "quality_score": exec.quality_score,
                "steps_executed": exec.steps_executed,
                "tools_used": exec.tools_used,
                "error": exec.error,
            }
            for exec in workflow.executions[-10:]  # Last 10 executions
        ],
    }


# ==================== Convenience Endpoints ====================


@router.post("/workflows/quick-meeting-analysis")
async def quick_meeting_analysis(
    content: str,
    meeting_id: str | None = None,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
):
    """Quick meeting analysis with standard settings."""
    try:
        inputs = {
            "content": content,
            "meeting_id": meeting_id,
            "include_patterns": True,
            "include_risks": True,
        }

        result = await registry.execute_workflow("meeting_processor", inputs)

        # Return simplified response with key highlights
        if result.success:
            summary = result.data.get("summary", {})
            metrics = summary.get("metrics", {})

            return {
                "success": True,
                "meeting_id": meeting_id,
                "key_metrics": {
                    "people_mentioned": metrics.get("people_count", 0),
                    "commitments_found": metrics.get("total_commitments", 0),
                    "high_priority_items": metrics.get("high_priority_commitments", 0),
                    "risks_identified": metrics.get("risks_identified", 0),
                    "insights_generated": metrics.get("total_insights", 0),
                },
                "execution_time_ms": result.execution_time_ms,
                "quality_score": result.quality_score,
            }
        else:
            return {"success": False, "error": result.error}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows/quick-document-summary")
async def quick_document_summary(
    content: str,
    file_path: str | None = None,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
):
    """Quick document summary with basic analysis."""
    try:
        inputs = {
            "content": content,
            "file_path": file_path,
            "analysis_depth": "basic",
            "focus_areas": ["entities"],
            "generate_summary": True,
        }

        result = await registry.execute_workflow("document_analyzer", inputs)

        # Return simplified response
        if result.success:
            summary = result.data.get("summary", {})
            entities = result.data.get("entities", {}).get("entities", {})

            return {
                "success": True,
                "file_path": file_path,
                "summary": summary,
                "entities_found": {
                    "people": len(entities.get("people", [])),
                    "projects": len(entities.get("projects", [])),
                    "teams": len(entities.get("teams", [])),
                },
                "execution_time_ms": result.execution_time_ms,
                "quality_score": result.quality_score,
            }
        else:
            return {"success": False, "error": result.error}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows/quick-commit-analysis")
async def quick_commit_analysis(
    commit_hash: str,
    commit_message: str,
    files_changed: list[str] | None = None,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
):
    """Quick commit analysis with basic enrichment."""
    try:
        if files_changed is None:
            files_changed = []
        inputs = {
            "commit_hash": commit_hash,
            "commit_message": commit_message,
            "files_changed": files_changed,
            "include_code_analysis": False,  # Skip detailed code analysis for quick mode
            "include_risk_assessment": True,
        }

        result = await registry.execute_workflow("commit_enricher", inputs)

        # Return simplified response
        if result.success:
            metadata = result.data.get("commit_metadata", {})
            enrichment_summary = result.data.get("enrichment_summary", {})

            return {
                "success": True,
                "commit_hash": commit_hash,
                "commit_type": metadata.get("commit_type", "unknown"),
                "impact_scope": metadata.get("impact_scope", "unknown"),
                "files_affected": len(files_changed),
                "follows_conventions": metadata.get(
                    "follows_conventional_commits", False
                ),
                "enrichment_metrics": enrichment_summary.get("metrics", {}),
                "execution_time_ms": result.execution_time_ms,
                "quality_score": result.quality_score,
            }
        else:
            return {"success": False, "error": result.error}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Batch Processing Endpoints ====================


class BatchMeetingRequest(BaseModel):
    """Request model for batch meeting processing."""

    meetings: list[dict[str, Any]]


@router.post("/workflows/batch/meeting-processor")
async def batch_process_meetings(
    request: BatchMeetingRequest,
    registry: WorkflowRegistry = Depends(get_workflow_registry),
):
    """Process multiple meetings in batch."""
    try:
        results = []
        total_time = 0
        successful_count = 0

        for meeting_data in request.meetings:
            try:
                result = await registry.execute_workflow(
                    "meeting_processor", meeting_data
                )
                results.append(
                    {
                        "meeting_id": meeting_data.get("meeting_id", "unknown"),
                        "success": result.success,
                        "execution_time_ms": result.execution_time_ms,
                        "quality_score": result.quality_score,
                        "data": result.data if result.success else None,
                        "error": result.error if not result.success else None,
                    }
                )

                total_time += result.execution_time_ms
                if result.success:
                    successful_count += 1

            except Exception as e:
                results.append(
                    {
                        "meeting_id": meeting_data.get("meeting_id", "unknown"),
                        "success": False,
                        "execution_time_ms": 0,
                        "quality_score": 0,
                        "data": None,
                        "error": str(e),
                    }
                )

        return {
            "batch_summary": {
                "total_meetings": len(request.meetings),
                "successful_processing": successful_count,
                "failed_processing": len(request.meetings) - successful_count,
                "success_rate": successful_count / len(request.meetings)
                if request.meetings
                else 0,
                "total_execution_time_ms": total_time,
                "average_execution_time_ms": total_time / len(request.meetings)
                if request.meetings
                else 0,
            },
            "results": results,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
