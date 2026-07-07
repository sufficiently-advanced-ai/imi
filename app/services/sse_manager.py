"""Server-Sent Events (SSE) Manager Service for real-time status updates."""

import asyncio
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class SSEManager:
    """Manages SSE connections and status event broadcasting."""

    def __init__(self):
        self.connections: dict[str, asyncio.Queue] = {}
        self.execution_status: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def add_connection(self, execution_id: str) -> asyncio.Queue:
        """Add a new SSE connection for an execution."""
        async with self._lock:
            if execution_id not in self.connections:
                self.connections[execution_id] = asyncio.Queue()
                logger.info(f"Added SSE connection for execution {execution_id}")
            return self.connections[execution_id]

    async def remove_connection(self, execution_id: str):
        """Remove SSE connection for an execution."""
        async with self._lock:
            if execution_id in self.connections:
                del self.connections[execution_id]
                logger.info(f"Removed SSE connection for execution {execution_id}")

    async def send_event(self, execution_id: str, event_type: str, event_data: dict[str, Any]):
        """Send an event to a specific execution's SSE stream."""
        async with self._lock:
            if execution_id in self.connections:
                queue = self.connections[execution_id]

                # Add standard fields to event
                event = {
                    "type": event_type,
                    "timestamp": datetime.utcnow().isoformat(),
                    "execution_id": execution_id,
                    **event_data
                }

                try:
                    queue.put_nowait(event)
                    logger.debug(f"Sent SSE event {event_type} to execution {execution_id}")
                except asyncio.QueueFull:
                    logger.warning(f"SSE queue full for execution {execution_id}, dropping event")

    def store_execution_status(self, execution_id: str, status_data: dict[str, Any]):
        """Store status data for an execution."""
        self.execution_status[execution_id] = {
            "timestamp": datetime.utcnow().isoformat(),
            **status_data
        }

    def get_execution_status(self, execution_id: str) -> dict[str, Any] | None:
        """Get stored status for an execution."""
        return self.execution_status.get(execution_id)


# Global SSE manager instance
sse_manager = SSEManager()
