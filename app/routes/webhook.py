import hashlib
import hmac
import json
import logging
import os
import sys
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)

from ..config import settings
from ..git_ops import git_ops
from ..github_client import GitHubClient
from ..models import ReinitializeResponse, TaskResponse, WebhookResponse
from ..services.auth import get_current_user
from ..services.orchestrators import WebhookOrchestrator
from ..services.orchestrators.webhook_orchestrator import recently_changed_files
from ..services.task_queue import global_task_queue

# Import metrics for backward compatibility in helper functions
try:
    from ..metrics import record_github_commit_analysis, record_github_webhook_processing
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    record_github_webhook_processing = None
    record_github_commit_analysis = None

logger = logging.getLogger(__name__)

# Reuse the orchestrator's deque so both layers stay in sync
MAX_RECENT_FILES = recently_changed_files.maxlen

# GitHub client will be initialized lazily
_github_client = None


def get_github_client():
    """Get GitHub client, initializing it lazily if needed."""
    global _github_client
    if _github_client is None:
        if not settings.GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN not configured")
        _github_client = GitHubClient(
            settings.GITHUB_TOKEN, settings.REPO_NAME, settings.WEBHOOK_SECRET
        )
    return _github_client


def _log_webhook(
    operation: str, details: dict[str, Any], error: Exception = None
) -> None:
    """Log webhook operations to stderr with structured format."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "component": "webhook",
        "operation": operation,
        "status": "error" if error else "success",
        "details": details,
    }
    if error:
        log_entry["error"] = str(error)
    print(json.dumps(log_entry), file=sys.stderr)


# WebhookProcessor class has been moved to services/orchestrators/webhook_orchestrator.py
# This maintains backward compatibility by delegating to the orchestrator

# Legacy WebhookProcessor methods have been moved to WebhookOrchestrator
# The functionality is now handled by the orchestrator pattern

router = APIRouter()


# Initialize task queue on startup
@router.on_event("startup")
async def startup_event():
    await global_task_queue.start()


# Shutdown task queue on shutdown
@router.on_event("shutdown")
async def shutdown_event():
    await global_task_queue.stop()


# Values that mean "a secret is configured elsewhere", never a usable key:
# ConfigManager persists secrets to app_config.json as "[CONFIGURED]", and the
# example config carries an unexpanded "${GITHUB_WEBHOOK_SECRET}" placeholder.
_SECRET_PLACEHOLDERS = ("[CONFIGURED]", "[REDACTED]")

# Bounded memory of handled X-GitHub-Delivery ids. In-process only: a restart
# forgets them, which at worst re-runs an idempotent re-ingest.
MAX_SEEN_DELIVERIES = 1000
_seen_deliveries: "OrderedDict[str, None]" = OrderedDict()
_seen_lock = threading.Lock()

_unverified_warning_logged = False


def verify_github_signature(
    secret: str, body: bytes, signature_header: str | None
) -> bool:
    """Return True iff ``signature_header`` is GitHub's HMAC-SHA256 of ``body``.

    GitHub sends ``X-Hub-Signature-256: sha256=<hexdigest>`` computed over the raw
    request body with the webhook secret. The comparison is constant-time.
    """
    if not secret or not signature_header:
        return False
    expected = (
        "sha256="
        + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header.strip())


def _is_placeholder(value: str) -> bool:
    return value in _SECRET_PLACEHOLDERS or (
        value.startswith("${") and value.endswith("}")
    )


def get_webhook_secret() -> str:
    """Resolve the effective GitHub webhook secret ("" when none is configured).

    ``settings.WEBHOOK_SECRET`` (env ``WEBHOOK_SECRET`` or app_config.json
    ``github.webhook_secret``) wins. An empty value or a redacted/unexpanded
    placeholder falls back to the ``GITHUB_WEBHOOK_SECRET`` env var, the name
    the config layer documents.
    """
    secret = (settings.WEBHOOK_SECRET or "").strip()
    if not secret or _is_placeholder(secret):
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
        if _is_placeholder(secret):
            secret = ""
    return secret


def _warn_unverified_once() -> None:
    global _unverified_warning_logged
    if not _unverified_warning_logged:
        _unverified_warning_logged = True
        logger.warning(
            "GitHub webhook signature verification is DISABLED: no webhook secret "
            "is configured. Set GITHUB_WEBHOOK_SECRET (or WEBHOOK_SECRET) whenever "
            "/api/webhook/github is reachable beyond loopback."
        )


def _delivery_seen(delivery_id: str) -> bool:
    with _seen_lock:
        return delivery_id in _seen_deliveries


def _remember_delivery(delivery_id: str) -> None:
    with _seen_lock:
        _seen_deliveries[delivery_id] = None
        _seen_deliveries.move_to_end(delivery_id)
        while len(_seen_deliveries) > MAX_SEEN_DELIVERIES:
            _seen_deliveries.popitem(last=False)


@router.post("/webhook/github", response_model=WebhookResponse)
async def github_webhook(
    request: Request,
    event: str = Header(..., alias="X-GitHub-Event"),
    signature: str | None = Header(None, alias="X-Hub-Signature-256"),
    delivery_id: str | None = Header(None, alias="X-GitHub-Delivery"),
) -> WebhookResponse:
    """Handle incoming GitHub webhooks.

    With a webhook secret configured, ``X-Hub-Signature-256`` is verified over the
    raw body before anything is parsed; a missing or wrong signature is a 401.
    Without one, deliveries are accepted (loopback deployments) and a warning is
    logged once per process. A repeated ``X-GitHub-Delivery`` id is acknowledged
    without re-running the pipeline.
    """
    body = await request.body()

    secret = get_webhook_secret()
    if secret:
        if not verify_github_signature(secret, body, signature):
            _log_webhook(
                "webhook_signature_rejected",
                {
                    "event": event,
                    "delivery_id": delivery_id,
                    "signature_present": bool(signature),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Hub-Signature-256",
            )
    else:
        _warn_unverified_once()

    if delivery_id and _delivery_seen(delivery_id):
        _log_webhook(
            "webhook_duplicate_delivery", {"event": event, "delivery_id": delivery_id}
        )
        return WebhookResponse(status="already_processed")

    try:
        payload = json.loads(body)

        # Create orchestrator and process webhook
        orchestrator = WebhookOrchestrator(payload, event)
        result = await orchestrator.process()

    except Exception as e:
        error = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
        _log_webhook(
            "webhook_handler_failed",
            {"stage": "initialization", "error": str(e)},
            error,
        )
        raise error

    # Remember only handled deliveries, so GitHub's redelivery of a failed
    # attempt is processed rather than short-circuited.
    if delivery_id:
        _remember_delivery(delivery_id)
    return result








@router.post("/repository/reinitialize", response_model=ReinitializeResponse)
async def reinitialize_repository(
    user: dict = Depends(get_current_user),
) -> ReinitializeResponse:
    """Reinitialize the git repository with a fresh clone.

    Use this endpoint when switching to a different repository or when
    the local repository state needs to be reset.

    Requires authentication.
    """
    operation = "reinitialize_repository"

    # Log user action
    logger.info(f"User {user.get('email', 'unknown')} reinitializing repository")

    try:
        _log_webhook(
            operation, {"status": "starting", "repository": settings.REPO_NAME}
        )

        # Perform fresh clone
        await git_ops.initialize()
        git_ops.invalidate_markdown_files_cache()

        _log_webhook(
            operation, {"status": "completed", "repository": settings.REPO_NAME}
        )

        return ReinitializeResponse(
            status="success",
            message="Repository reinitialized successfully",
            repository=settings.REPO_NAME,
        )

    except Exception as e:
        error = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
        _log_webhook(
            operation,
            {"status": "failed", "repository": settings.REPO_NAME, "error": str(e)},
            error,
        )
        raise error


@router.get("/recent-changes")
async def get_recent_changes():
    """Get recently changed files with their associated task status if available."""
    global recently_changed_files

    # Add task status to recently changed files
    enriched_changes = []
    for file_info in recently_changed_files:
        enriched_file = dict(file_info)

        # Add task status if task ID exists
        if "task_id" in file_info:
            task = global_task_queue.get_task(file_info["task_id"])
            if task:
                enriched_file["task_status"] = task.status

        enriched_changes.append(enriched_file)

    return {"files": enriched_changes}


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(task_id: str):
    """Get status of a specific task."""
    task = global_task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        error=str(task.error) if task.error else None,
    )


@router.get("/tasks")
async def get_tasks():
    """Get overview of all tasks and queue status."""
    return {
        "queue_stats": global_task_queue.get_queue_stats(),
        "tasks": [task.to_dict() for task in global_task_queue.tasks.values()],
    }


@router.get("/api/client-stats")
async def get_client_stats():
    """Get Claude client statistics."""
    from ..services.claude_client import claude_client

    return claude_client.get_stats()
