"""
Agent Tools API Routes - RESTful interface for the Agent Tool Arsenal.

Provides endpoints for tool discovery, execution, and performance monitoring.

The tools registered with AgentToolRegistry are also exposed via this REST
surface. Verb taxonomy and parameter conventions for new tools are
documented in docs/mcp_tool_conventions.md.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services.agent_tools import AgentToolRegistry, ToolResult
from ..services.auth import get_current_user


class ToolExecutionRequest(BaseModel):
    """Request model for tool execution."""

    tool_name: str
    inputs: dict[str, Any]


class ToolChainRequest(BaseModel):
    """Request model for tool chain execution."""

    tool_chain: list[dict[str, Any]]


class ToolListResponse(BaseModel):
    """Response model for tool listing."""

    tools: list[dict[str, Any]]
    total_count: int


class ToolStatsResponse(BaseModel):
    """Response model for tool statistics."""

    registry_stats: dict[str, Any]
    tool_performance: dict[str, Any]


router = APIRouter()

# Global registry instance
_tool_registry = None


def get_tool_registry() -> AgentToolRegistry:
    """Get configured tool registry instance."""
    global _tool_registry
    if _tool_registry is None:
        try:
            # Use the global instances from main module
            from ..git_ops import git_ops
            from ..services.claude_client import get_claude_client
            from ..services.file_cache import FileCache

            print("Initializing AgentToolRegistry...")
            claude_client = get_claude_client()
            file_cache = FileCache()
            _tool_registry = AgentToolRegistry(claude_client, git_ops, file_cache)
            print(f"AgentToolRegistry initialized successfully: {_tool_registry}")
        except Exception as e:
            print(f"Failed to initialize AgentToolRegistry: {e}")
            import traceback

            traceback.print_exc()
            # For now, return a mock registry if initialization fails
            _tool_registry = MockToolRegistry()
            print(f"Using MockToolRegistry instead: {_tool_registry}")
    return _tool_registry


class MockToolRegistry:
    """Mock registry for testing when real initialization fails."""

    def __init__(self):
        self.tools = {}

    def list_tools(self):
        return [
            {
                "name": "extract_entities",
                "description": "Mock entity extraction tool",
                "input_schema": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"entities": {"type": "array"}},
                },
                "performance": {"total_executions": 0},
            }
        ]

    def get_tool(self, tool_name: str):
        """Mock get_tool method."""
        return None

    async def execute_tool(self, tool_name: str, inputs):
        return ToolResult(
            success=False,
            data={},
            execution_time_ms=0,
            error="Tool registry not properly initialized",
        )


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """List all available agent tools with their schemas and performance metrics.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} listing agent tools")
    tools = registry.list_tools()

    return ToolListResponse(tools=tools, total_count=len(tools))


@router.get("/tools/{tool_name}")
async def get_tool_info(
    tool_name: str,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get detailed information about a specific tool.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} getting tool info: {tool_name}")
    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "output_schema": tool.output_schema,
        "performance": tool.get_performance_stats(),
    }


@router.post("/tools/execute")
async def execute_tool(
    request: ToolExecutionRequest,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
) -> ToolResult:
    """Execute a single agent tool with the provided inputs.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} executing tool: {request.tool_name}"
    )
    try:
        result = await registry.execute_tool(request.tool_name, request.inputs)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/chain")
async def execute_tool_chain(
    request: ToolChainRequest,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
) -> list[ToolResult]:
    """Execute a chain of tools, passing outputs between them.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} executing tool chain with {len(request.tool_chain)} tools"
    )
    try:
        results = await registry.execute_chain(request.tool_chain)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=ToolStatsResponse)
async def get_tool_stats(
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get comprehensive statistics for all tools in the registry.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing tool stats")
    registry_stats = registry.get_registry_stats()

    return ToolStatsResponse(
        registry_stats=registry_stats,
        tool_performance=registry_stats.get("tool_performance", {}),
    )


@router.get("/tools/{tool_name}/performance")
async def get_tool_performance(
    tool_name: str,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get detailed performance metrics for a specific tool.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} accessing performance for tool: {tool_name}"
    )
    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    return {
        "tool_name": tool_name,
        "performance": tool.get_performance_stats(),
        "recent_executions": [
            {
                "execution_id": exec.execution_id,
                "start_time": exec.start_time.isoformat(),
                "duration_ms": exec.duration_ms,
                "success": exec.success,
                "error": exec.error,
            }
            for exec in tool.executions[-10:]  # Last 10 executions
        ],
    }


# Example endpoints for common tool combinations
@router.post("/tools/analyze-content")
async def analyze_content(
    content: str,
    analysis_types: list[str] | None = None,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Convenience endpoint to run multiple analysis tools on content.

    Requires authentication.
    """
    if analysis_types is None:
        analysis_types = ["entities", "risks"]
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} analyzing content with types: {analysis_types}"
    )
    tool_chain = []

    if "entities" in analysis_types:
        tool_chain.append({"tool": "extract_entities", "inputs": {"content": content}})

    if "risks" in analysis_types:
        tool_chain.append({"tool": "extract_risks", "inputs": {"content": content}})

    if "patterns" in analysis_types:
        tool_chain.append({"tool": "extract_patterns", "inputs": {"content": content}})

    try:
        results = await registry.execute_chain(tool_chain)

        # Combine results into a unified response
        combined_result = {
            "content_length": len(content),
            "analysis_types": analysis_types,
            "results": {},
        }

        for i, result in enumerate(results):
            if result.success:
                tool_name = tool_chain[i]["tool"]
                combined_result["results"][tool_name] = result.data

        return combined_result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/comprehensive-analysis")
async def comprehensive_analysis(
    content: str,
    file_path: str | None = None,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Run all available analysis tools on content for comprehensive insights.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} running comprehensive analysis on content"
    )
    tool_chain = [
        {
            "tool": "extract_entities",
            "inputs": {"content": content, "file_path": file_path},
        },
        {"tool": "extract_patterns", "inputs": {"content": content}},
        {"tool": "extract_risks", "inputs": {"content": content}},
        {"tool": "build_timeline", "inputs": {"content": content}},
        {"tool": "map_relationships", "inputs": {"content": content}},
    ]

    try:
        results = await registry.execute_chain(tool_chain)

        # Generate insights from all the extracted data
        combined_data = {}
        for i, result in enumerate(results):
            if result.success:
                tool_name = tool_chain[i]["tool"]
                combined_data[tool_name] = result.data

        # Run insights generation on combined data
        insights_result = await registry.execute_tool(
            "generate_insights", {"data": combined_data}
        )

        return {
            "comprehensive_analysis": combined_data,
            "insights": insights_result.data if insights_result.success else {},
            "execution_summary": {
                "total_tools": len(tool_chain) + 1,
                "successful_tools": len([r for r in results if r.success])
                + (1 if insights_result.success else 0),
                "total_execution_time_ms": sum(r.execution_time_ms for r in results)
                + insights_result.execution_time_ms,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Decision Logging Endpoints
@router.get("/decisions")
async def get_decision_logs(
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get current session decision logs.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing decision logs")
    try:
        logs = registry.get_decision_logs()
        return {"decision_logs": logs, "total_decisions": len(logs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/decisions/patterns")
async def get_usage_patterns(
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get tool usage patterns and analytics from decision logs.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} accessing usage patterns")
    try:
        patterns = registry.get_usage_patterns()
        return patterns
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/decisions/commit")
async def commit_decision_logs(
    commit_message: str | None = None,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Commit current session decision logs to git for audit trail.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} committing decision logs")
    try:
        success = await registry.commit_decision_logs(commit_message)
        return {
            "success": success,
            "message": "Decision logs committed to git"
            if success
            else "Failed to commit decision logs",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/decisions/history")
async def get_decision_history(
    days: int = 7,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get decision history from the last N days.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} accessing decision history ({days} days)"
    )
    try:
        history = await registry.load_decision_history(days)
        return {
            "decision_history": history,
            "days_retrieved": days,
            "total_decisions": len(history),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DecisionExplainRequest(BaseModel):
    """Request model for decision explanation."""

    execution_id: str


@router.post("/decisions/explain")
async def explain_decision(
    request: DecisionExplainRequest,
    registry: AgentToolRegistry = Depends(get_tool_registry),
    user: dict = Depends(get_current_user),
):
    """Get detailed explanation for a specific decision.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} explaining decision: {request.execution_id}"
    )
    try:
        logs = registry.get_decision_logs()
        decision_log = next(
            (log for log in logs if log.get("execution_id") == request.execution_id),
            None,
        )

        if not decision_log:
            raise HTTPException(status_code=404, detail="Decision not found")

        return {
            "execution_id": request.execution_id,
            "explanation": {
                "decision": decision_log.get("decision"),
                "reasoning": decision_log.get("reasoning"),
                "confidence": decision_log.get("confidence"),
                "tools_used": decision_log.get("tools_used"),
                "context": decision_log.get("context"),
                "outcome": decision_log.get("outcome"),
                "timestamp": decision_log.get("timestamp"),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
