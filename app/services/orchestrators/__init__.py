"""
Orchestrator Services Package

This package contains orchestrator classes that handle business logic previously
embedded in route handlers. Orchestrators coordinate complex operations across
multiple services while maintaining clean separation from HTTP concerns.

Design Principles:
- Routes handle HTTP validation, formatting, and error responses
- Orchestrators handle business logic, service coordination, and workflows
- Clear dependency hierarchy prevents circular imports
- Consistent error handling and logging patterns
- OpenTelemetry integration for observability

Available Orchestrators:
- BaseOrchestrator: Abstract base class with common patterns
- WebhookOrchestrator: GitHub webhook processing coordination
- ObjectivesOrchestrator: Agent objectives business logic
"""

from .base import BaseOrchestrator
from .objectives_orchestrator import ObjectivesOrchestrator
from .webhook_orchestrator import WebhookOrchestrator

__all__ = [
    "BaseOrchestrator",
    "ObjectivesOrchestrator",
    "WebhookOrchestrator",
]
