"""
Streaming chat endpoint with SSE for real-time ChatAgent output.
Issue #40 implementation.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.chat import ChatAgent
from app.config import settings
from app.services.sse_manager import sse_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class ConversationMessage(BaseModel):
    """A single message in the conversation history"""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class StreamingChatRequest(BaseModel):
    """Request model for streaming chat"""

    query: str = Field(..., min_length=1, description="User query")
    manual_context: list[str] | None = Field(
        None, description="Optional manual context files"
    )
    conversation_history: list[ConversationMessage] | None = Field(
        None, description="Previous conversation messages for context"
    )


class AgentStartEvent(BaseModel):
    """Agent start event structure"""

    type: str = Field(default="agent_start")
    execution_id: str
    query_len: int
    model: str
    manual_context: bool
    timestamp: str


class ToolStartEvent(BaseModel):
    """Tool start event structure"""

    type: str = Field(default="tool_start")
    execution_id: str
    tool_name: str
    tool_args: dict[str, Any]
    tool_id: str
    timestamp: str


class ToolCompleteEvent(BaseModel):
    """Tool complete event structure"""

    type: str = Field(default="tool_complete")
    execution_id: str
    tool_name: str
    duration: float
    result_summary: str
    status: str
    timestamp: str


class AgentCompleteEvent(BaseModel):
    """Agent complete event structure"""

    type: str = Field(default="agent_complete")
    execution_id: str
    total_duration: float
    iterations: int
    tools_called: int
    unique_tools: list[str]
    context_files: int
    answer_length: int
    timestamp: str


def _match_demo_conversation(query: str, app_state) -> dict[str, Any] | None:
    """Check if a query matches a scripted demo conversation."""
    demo_conversations = getattr(app_state, "demo_chat_conversations", {})
    if not demo_conversations:
        return None

    normalized = query.lower().strip()
    if not normalized:
        return None

    # Exact match
    if normalized in demo_conversations:
        return demo_conversations[normalized]

    # Partial match - prefer longest match to avoid unintended hits
    best_match: tuple[str, dict[str, Any]] | None = None
    for stored_query, conv in demo_conversations.items():
        if not stored_query:
            continue
        if stored_query in normalized or normalized in stored_query:
            if best_match is None or len(stored_query) > len(best_match[0]):
                best_match = (stored_query, conv)

    return best_match[1] if best_match else None


async def demo_chat_event_generator(
    request: Request,
    execution_id: str,
    conversation: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events from a scripted demo conversation.

    Simulates real agent behavior by emitting tool_start/tool_complete events
    with realistic delays before delivering the pre-written answer.
    """
    try:
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'execution_id': execution_id, 'timestamp': datetime.utcnow().isoformat()})}\n\n"

        # Send agent start
        yield f"data: {json.dumps({'type': 'agent_start', 'execution_id': execution_id, 'query_len': len(conversation.get('query', '')), 'model': settings.CLAUDE_SONNET_MODEL, 'manual_context': False, 'timestamp': datetime.utcnow().isoformat()})}\n\n"

        start_time = datetime.utcnow()
        tools_called = []

        # Simulate each thinking step with delays
        for i, step in enumerate(conversation.get("thinking_steps", [])):
            if await request.is_disconnected():
                return

            tool_name = step.get("tool", "unknown")
            tool_args = step.get("args", {})
            delay_ms = step.get("delay_ms", 800)
            result_summary = step.get("result_summary", "Complete")
            tools_called.append(tool_name)

            # Emit tool_start
            yield f"data: {json.dumps({'type': 'tool_start', 'execution_id': execution_id, 'tool_name': tool_name, 'tool_args': tool_args, 'tool_id': f'demo_tool_{i}', 'timestamp': datetime.utcnow().isoformat()})}\n\n"

            # Simulate processing delay
            await asyncio.sleep(delay_ms / 1000.0)

            # Emit tool_complete
            yield f"data: {json.dumps({'type': 'tool_complete', 'execution_id': execution_id, 'tool_name': tool_name, 'duration': delay_ms / 1000.0, 'result_summary': result_summary, 'status': 'success', 'timestamp': datetime.utcnow().isoformat()})}\n\n"

        # Brief pause before final answer
        await asyncio.sleep(0.5)

        total_duration = (datetime.utcnow() - start_time).total_seconds()
        answer = conversation.get("answer", "")
        sources = conversation.get("sources", [])

        # Emit workflow_complete with the scripted answer
        yield f"data: {json.dumps({'type': 'workflow_complete', 'execution_id': execution_id, 'result': {'answer': answer, 'context_files': sources, 'tool_calls': len(tools_called), 'cited_documents': sources}, 'timestamp': datetime.utcnow().isoformat()})}\n\n"

        logger.info(f"Demo chat completed for {execution_id}: {len(tools_called)} simulated tools, {total_duration:.1f}s")

    except asyncio.CancelledError:
        logger.info(f"Demo chat streaming cancelled for {execution_id}")
        raise
    except Exception as e:
        logger.error(f"Demo chat streaming error for {execution_id}: {e}")
        yield f"data: {json.dumps({'type': 'error', 'execution_id': execution_id, 'error': str(e), 'timestamp': datetime.utcnow().isoformat()})}\n\n"


async def chat_event_generator(
    request: Request,
    execution_id: str,
    query: str,
    manual_context: list[str] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE events for chat processing"""

    try:
        # Add connection to SSE manager
        queue = await sse_manager.add_connection(execution_id)

        # Send initial connection event
        initial_event = {
            "type": "connected",
            "execution_id": execution_id,
            "timestamp": datetime.utcnow().isoformat(),
        }
        yield f"data: {json.dumps(initial_event)}\n\n"

        # Create ChatAgent and configure for streaming
        agent = ChatAgent()
        agent.configure_streaming(execution_id, emit_sse=True)

        # Start processing in background task
        async def process_chat():
            try:
                result = await agent.process_query(query, manual_context, conversation_history)

                # Emit final completion event if not already emitted
                if not result.get("error"):
                    await sse_manager.send_event(
                        execution_id,
                        "workflow_complete",
                        {
                            "result": {
                                "answer": result["answer"],
                                "context_files": result.get("context_files", []),
                                "tool_calls": len(result.get("tool_calls", [])),
                                "cited_documents": result.get("cited_documents", []),
                            }
                        },
                    )
                else:
                    await sse_manager.send_event(
                        execution_id,
                        "workflow_failed",
                        {
                            "error": result.get("error", "Unknown error")
                        },
                    )

            except Exception as e:
                logger.error(f"Chat processing error for {execution_id}: {str(e)}")
                await sse_manager.send_event(
                    execution_id,
                    "workflow_failed",
                    {
                        "error": str(e)
                    },
                )

        # Start processing task
        process_task = asyncio.create_task(process_chat())

        try:
            # Stream events from queue
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"Client disconnected for execution {execution_id}")
                    break

                try:
                    # Wait for new events with timeout
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"

                    # Check for completion events
                    if event.get("type") in [
                        "workflow_complete",
                        "workflow_failed",
                        "agent_complete",
                    ]:
                        # Wait a moment for any final events
                        await asyncio.sleep(0.1)
                        break

                except TimeoutError:
                    # Send keepalive
                    keepalive_event = {
                        "type": "keepalive",
                        "execution_id": execution_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    yield f"data: {json.dumps(keepalive_event)}\n\n"

        finally:
            # Always ensure processing task is properly cleaned up
            if not process_task.done():
                logger.info(f"Cancelling background task for execution {execution_id}")
                process_task.cancel()
                try:
                    await asyncio.wait_for(process_task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    logger.warning(
                        f"Background task cleanup completed for execution {execution_id}"
                    )
                except Exception as cleanup_error:
                    logger.error(
                        f"Error during task cleanup for execution {execution_id}: {cleanup_error}"
                    )

    except asyncio.CancelledError:
        logger.info(f"Chat streaming cancelled for execution {execution_id}")
        # Clean up connection on cancellation
        await sse_manager.remove_connection(execution_id)
        raise
    except Exception as e:
        logger.error(f"Chat streaming error for execution {execution_id}: {str(e)}")
        error_event = {
            "type": "error",
            "execution_id": execution_id,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }
        yield f"data: {json.dumps(error_event)}\n\n"
    finally:
        # Clean up connection - ensure this always happens
        try:
            await sse_manager.remove_connection(execution_id)
            logger.debug(f"Connection cleanup completed for execution {execution_id}")
        except Exception as cleanup_error:
            logger.error(
                f"Error during connection cleanup for execution {execution_id}: {cleanup_error}"
            )


@router.post("/api/chat/stream")
async def stream_chat(request_data: StreamingChatRequest, request: Request):
    """Stream ChatAgent processing with real-time SSE events"""

    try:
        # Generate unique execution ID
        execution_id = f"chat_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"Starting chat stream {execution_id} for query: {request_data.query[:100]}..."
        )

        # Check for demo mode scripted conversation
        if settings.DEMO_MODE:
            matched = _match_demo_conversation(request_data.query, request.app.state)
            if matched:
                logger.info(f"Demo chat match for {execution_id}: using scripted response")
                return StreamingResponse(
                    demo_chat_event_generator(
                        request=request,
                        execution_id=execution_id,
                        conversation=matched,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*",
                    },
                )

        # Convert conversation history to dicts for the agent
        history = None
        if request_data.conversation_history:
            history = [
                {"role": msg.role, "content": msg.content}
                for msg in request_data.conversation_history
            ]

        # Return streaming response
        return StreamingResponse(
            chat_event_generator(
                request=request,
                execution_id=execution_id,
                query=request_data.query,
                manual_context=request_data.manual_context,
                conversation_history=history,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
            },
        )

    except Exception as e:
        logger.error(f"Failed to start chat streaming: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to start chat streaming: {str(e)}"
        )


@router.get("/api/chat/stream/health")
async def chat_streaming_health():
    """Health check for chat streaming endpoint"""
    return {
        "status": "healthy",
        "service": "chat_streaming",
        "timestamp": datetime.utcnow().isoformat(),
        "active_connections": len(sse_manager.get_active_connections()),
    }
