"""
GitHub Webhook Processing Orchestrator

Handles the complete GitHub webhook processing workflow by coordinating
multiple service operations. Extracted from the WebhookProcessor class
to separate business logic from HTTP route handling.

Responsibilities:
- Webhook request validation and signature verification
- Git operations coordination (initialize, pull, get changes)
- Document processing pipeline orchestration
- Background task scheduling and management
- Entity extraction and profile updates
- Meeting file processing
- Cache invalidation coordination
- Telemetry and metrics recording

This orchestrator maintains all existing functionality while providing
better separation of concerns and testability.
"""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any

from ...config import settings
from ...core.dependencies import get_domain_config_service
from ...git_ops import GitOperationError, git_ops
from ...github_client import GitHubClient
from ...models import File, WebhookResponse
from ...services.domain_aware_entity_extractor import DomainAwareEntityExtractor
from ...services.domain_aware_entity_processor import DomainAwareEntityProcessor
from ...services.metadata import analyze_metadata
from ...services.pattern_detection_service import PatternDetectionService
from ...services.task_queue import global_task_queue
from .base import BaseOrchestrator

logger = logging.getLogger(__name__)

# Global variable to track recently changed files
MAX_RECENT_FILES = 50
recently_changed_files = deque(maxlen=MAX_RECENT_FILES)

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


class WebhookOrchestrator(BaseOrchestrator):
    """
    Orchestrates GitHub webhook processing workflows.

    This orchestrator handles the complete webhook processing pipeline,
    from initial validation through document processing and entity updates.
    It coordinates multiple services while maintaining proper error handling,
    logging, and telemetry.
    """

    def __init__(self, payload: dict[str, Any], event: str, github_client: GitHubClient = None):
        """
        Initialize the webhook orchestrator.

        Args:
            payload: The raw webhook payload from GitHub
            event: The GitHub event type from X-GitHub-Event header
            github_client: Optional GitHub client instance. If not provided, will create one lazily.
        """
        super().__init__()
        self.payload = payload
        self.event = event
        self._github_client = github_client
        self.processed_files: list[str] = []
        self.metadata_updates: int = 0
        self.trigger_files: list[File] = []
        self.background_tasks: list[str] = []  # List of background task IDs

        # Initialize pattern detection if available
        try:
            from ...services.claude_client import get_claude_client

            claude_client = get_claude_client()
            self.pattern_detection_service = PatternDetectionService(claude_client)
            logger.info("Pattern detection service initialized for webhook processing")
        except Exception as e:
            logger.warning(f"Pattern detection not available for webhook: {e}")
            self.pattern_detection_service = None

        # Initialize domain config service
        try:
            self.domain_config_service = get_domain_config_service()
            logger.info("Domain config service initialized for webhook processing")
        except Exception as e:
            logger.warning(f"Domain config service not available for webhook: {e}")
            self.domain_config_service = None

    async def process(self) -> WebhookResponse:
        """
        Orchestrate the webhook processing workflow.

        This method coordinates the entire webhook processing sequence,
        handling initial validation and setting up background tasks for
        the heavy processing work.

        Returns:
            WebhookResponse with status and processing details

        Raises:
            HTTPException: If processing fails at any stage
        """
        # Create span if OpenTelemetry is available
        span = self._create_telemetry_span("github_webhook.process")

        try:
            # Set basic span attributes
            self._set_span_attributes(span, {
                "github.event_type": self.event,
                "github.repository": self.payload.get("repository", {}).get("full_name", "unknown")
            })

            result = await self._process_with_telemetry(span)

            # Set final span attributes
            self._set_span_attributes(span, {
                "github.webhook_processing_status": result.status,
                "github.processed_files_count": len(result.processed_files),
                "github.background_tasks_count": len(result.background_tasks)
            })
            self._set_span_success(span)

            # Record metrics
            self._record_metrics(
                "webhook_processing",
                self.event,
                result.status,
                len(result.processed_files)
            )

            return result

        except Exception as e:
            # Set error attributes and record failed metrics
            await self._handle_orchestrator_error(
                "webhook_processing",
                e,
                {"event": self.event},
                span
            )
            self._record_metrics("webhook_processing", self.event, "error", 0)
            raise

    async def _process_with_telemetry(self, span=None) -> WebhookResponse:
        """Internal processing method with telemetry support."""
        operation = "process_webhook"

        try:
            # Start the task queue if it's not already running
            if not global_task_queue.running:
                await global_task_queue.start()

            # Step 1: Validate the request
            validated_payload = await self.validate_request()

            # Check if we should skip this commit (bot commit)
            if self._should_skip_bot_commit(validated_payload):
                return WebhookResponse(
                    status="skipped",
                    processed_files=[],
                    metadata_updates=0,
                    background_tasks=[],
                )

            # Step 2: Process git changes
            await self.process_git_changes()

            # Step 3: Get and filter markdown files
            changed_files = await self.filter_markdown_files(validated_payload)
            if not changed_files:
                return WebhookResponse(
                    status="skipped",
                    processed_files=[],
                    metadata_updates=0,
                    background_tasks=[],
                )

            # Step 4: Schedule background tasks for document processing
            batch_task_id = await self.schedule_document_processing(changed_files)
            self.background_tasks.append(batch_task_id)

            # Add all changed files to recently_changed_files for tracking
            global recently_changed_files
            for file_path in changed_files:
                recently_changed_files.appendleft(
                    {
                        "path": file_path,
                        "timestamp": datetime.utcnow().isoformat(),
                        "task_id": batch_task_id,
                    }
                )

            # Return early with background task IDs
            return WebhookResponse(
                status="processing",
                processed_files=changed_files,
                metadata_updates=0,  # Will be updated by background task
                background_tasks=self.background_tasks,
            )

        except Exception as e:
            await self._handle_orchestrator_error(
                operation,
                e,
                {"stage": "main_processing"},
                span
            )
            raise

    def _should_skip_bot_commit(self, payload: dict[str, Any]) -> bool:
        """
        Check if the commit should be skipped (bot commit).

        Args:
            payload: The validated webhook payload

        Returns:
            True if the commit should be skipped, False otherwise
        """
        head_commit = payload.get("head_commit", {})
        if not head_commit:
            return False

        commit_message = head_commit.get("message", "")
        committer = head_commit.get("committer", {}) or {}
        author = head_commit.get("author", {}) or {}

        # Check for bot commits by committer/author username
        committer_username = committer.get("username", "")
        author_username = author.get("username", "")
        is_bot_user = (committer_username.endswith("[bot]") or
                      author_username.endswith("[bot]"))

        # Check for bot commit prefix if configured
        has_bot_prefix = (settings.BOT_COMMIT_PREFIX and
                         commit_message.startswith(settings.BOT_COMMIT_PREFIX))

        # Check for known bot commit patterns (backwards compatibility)
        has_bot_pattern = "Update metadata" in commit_message

        if is_bot_user or has_bot_prefix or has_bot_pattern:
            self._log_operation(
                "skipping_bot_commit",
                {
                    "message": commit_message,
                    "committer": committer_username,
                    "author": author_username,
                    "is_bot_user": is_bot_user,
                    "has_bot_prefix": has_bot_prefix,
                    "has_bot_pattern": has_bot_pattern,
                    "status": "skipped"
                }
            )
            return True

        return False

    async def validate_request(self) -> dict[str, Any]:
        """
        Validate the webhook request.

        Checks headers, verifies signatures, and parses the payload.

        Returns:
            Validated payload dictionary

        Raises:
            HTTPException: If validation fails
        """
        span = self._create_telemetry_span("github_webhook.validate_request")

        try:
            # Set basic span attributes
            self._set_span_attributes(span, {
                "github.event_type": self.event,
                "github.payload_size": len(str(self.payload)),
                "github.commit_count": len(self.payload.get("commits", []))
            })

            # Validate the payload using the GitHub client
            github_client = self._github_client or get_github_client()
            validated_payload = github_client.validate_webhook_payload(
                self.payload, self.event
            )

            # Log the payload
            github_client.log_webhook_payload(validated_payload)
            self._log_operation(
                "payload_received",
                {
                    "event": self.event,
                    "commit_count": len(validated_payload.get("commits", [])),
                    "repository": validated_payload.get("repository", {}).get("full_name", "unknown"),
                },
            )

            self._set_span_success(span)
            return validated_payload

        except Exception as e:
            await self._handle_orchestrator_error(
                "payload_validation",
                e,
                {"event": self.event},
                span
            )
            raise

    async def process_git_changes(self) -> None:
        """
        Initialize repository and pull latest changes.

        Handles repository initialization and pulling the latest changes
        from the remote repository.

        Raises:
            HTTPException: If git operations fail
        """
        # Initialize/reinitialize repository if needed
        span_init = self._create_telemetry_span("github_webhook.git_initialize")
        try:
            self._set_span_attributes(span_init, {"github.git_operation": "initialize"})
            self._log_operation("git_init_starting", {})

            start_time = time.perf_counter()
            await git_ops.initialize()
            git_ops.invalidate_markdown_files_cache()
            duration = time.perf_counter() - start_time

            self._set_span_attributes(span_init, {"github.git_duration_seconds": duration})
            self._set_span_success(span_init)
            self._log_operation("git_init_complete", {})

        except Exception as e:
            await self._handle_orchestrator_error(
                "git_initialize",
                e,
                {"operation": "initialize"},
                span_init
            )
            raise

        # Pull latest changes
        span_pull = self._create_telemetry_span("github_webhook.git_pull")
        try:
            self._set_span_attributes(span_pull, {"github.git_operation": "pull"})
            self._log_operation("git_pull_starting", {})

            start_time = time.perf_counter()
            await git_ops.pull_changes()
            git_ops.invalidate_markdown_files_cache()
            duration = time.perf_counter() - start_time

            self._set_span_attributes(span_pull, {"github.git_duration_seconds": duration})
            self._set_span_success(span_pull)
            self._log_operation("git_pull_complete", {})

        except Exception as e:
            await self._handle_orchestrator_error(
                "git_pull",
                e,
                {"operation": "pull"},
                span_pull
            )
            raise

    async def filter_markdown_files(self, payload: dict[str, Any]) -> list[str]:
        """
        Get changed files and filter for markdown files.

        Args:
            payload: The validated webhook payload

        Returns:
            List of changed markdown file paths

        Raises:
            HTTPException: If getting changed files fails
        """
        span = self._create_telemetry_span("github_webhook.filter_markdown_files")

        # Get and validate commit hashes
        before_commit = payload.get("before")
        after_commit = payload.get("after")

        # Set span attributes
        self._set_span_attributes(span, {
            "github.commit_before": before_commit or "unknown",
            "github.commit_after": after_commit or "unknown"
        })

        if not before_commit or not after_commit:
            self._log_operation(
                "invalid_commit_hashes",
                {"before": before_commit, "after": after_commit, "status": "error"},
            )
            return []

        # Determine repository state
        is_initial_push = before_commit == "0" * 40
        self._set_span_attributes(span, {"github.is_initial_push": is_initial_push})

        self._log_operation(
            "repository_state",
            {
                "is_initial_push": is_initial_push,
                "before_commit": before_commit,
                "after_commit": after_commit,
                "commit_count": len(payload.get("commits", [])),
            },
        )

        # Get changed files
        changed_files: list[str] = []
        try:
            # Get changed files using git diff-tree
            changed_files = await git_ops.get_changed_files_between_commits(
                before_commit, after_commit
            )
            self._set_span_attributes(span, {
                "github.file_detection_method": "git_diff_tree",
                "github.changed_files_count": len(changed_files)
            })

            self._log_operation(
                "changed_files_source",
                {"method": "git_diff_tree", "file_count": len(changed_files)},
            )
        except GitOperationError as e:
            self._log_operation(
                "git_diff_failed",
                {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "details": getattr(e, "details", {}),
                    "fallback": "using_payload_files",
                },
            )

            # Extract files from payload as fallback
            added_files: list[str] = []
            modified_files: list[str] = []
            removed_files: list[str] = []
            for commit in payload.get("commits", []):
                added_files.extend(commit.get("added", []))
                modified_files.extend(commit.get("modified", []))
                removed_files.extend(commit.get("removed", []))

            # Remove duplicates while preserving order
            changed_files = list(dict.fromkeys(added_files + modified_files + removed_files))
            self._set_span_attributes(span, {
                "github.file_detection_method": "webhook_payload_fallback",
                "github.changed_files_count": len(changed_files)
            })

        # Filter for markdown files (including various extensions and case-insensitive)
        markdown_exts = (".md", ".markdown")
        markdown_files = [f for f in changed_files if f.lower().endswith(markdown_exts)]

        self._set_span_attributes(span, {"github.filtered_markdown_files_count": len(markdown_files)})
        self._set_span_success(span)

        self._log_operation(
            "changed_files_detected",
            {
                "files": markdown_files,
                "count": len(markdown_files),
                "before_commit": before_commit,
                "after_commit": after_commit,
            },
        )

        return markdown_files

    async def schedule_document_processing(self, files: list[str]) -> str:
        """
        Schedule background processing for the specified files.

        Returns:
            Task ID for the batch processing task
        """
        # Enqueue the batch processing task
        batch_task_id = global_task_queue.enqueue(
            self._process_documents_batch,
            files,
            priority=10,  # Give webhook processing high priority
        )

        self._log_operation(
            "schedule_document_processing",
            {"task_id": batch_task_id, "file_count": len(files), "status": "scheduled"},
        )

        return batch_task_id

    async def _process_documents_batch(self, files: list[str]) -> dict[str, Any]:
        """
        Process a batch of documents through the standard analysis pipeline,
        then optionally enrich with Semantica embedding and entity extraction.

        For each markdown file:
        1. Run existing pipeline: analyze_metadata, domain entity extraction
        2. (Additive) If Semantica available: generate embeddings, extract entities via NER

        Args:
            files: List of file paths to process

        Returns:
            Summary of processing results
        """
        metadata_updates = 0
        person_files: list[str] = []
        digest_files: list[str] = []
        entity_count = 0
        embedded_count = 0

        _markdown_exts = (".md", ".markdown")

        # --- Step 1: Run existing analysis pipeline for every file ---
        for file_path in files:
            if not file_path.endswith(_markdown_exts):
                continue

            try:
                # Core metadata analysis (enrichment, frontmatter update, etc.)
                result = await analyze_metadata(file_path)
                if result:
                    metadata_updates += 1

                # Domain-aware entity extraction
                try:
                    domain_config = self.domain_config_service if self.domain_config_service else None
                    if domain_config:
                        from ...services.claude_client import get_claude_client
                        claude_client = get_claude_client()
                        extractor = DomainAwareEntityExtractor()
                        processor = DomainAwareEntityProcessor(claude_client)

                        from app.git_ops import git_ops as _git_ops
                        content = await _git_ops.read_file(file_path)
                        if content:
                            entities = await extractor.extract_entities_from_metadata(file_path, domain_config.get_domain_config() if hasattr(domain_config, 'get_domain_config') else None)
                            if entities:
                                await processor.process_entities(entities, file_path)

                            # Track person/digest files for downstream processing
                            lower_path = file_path.lower()
                            if "/people/" in lower_path or "/persons/" in lower_path:
                                person_files.append(file_path)
                            if "/digests/" in lower_path:
                                digest_files.append(file_path)
                except Exception as e:
                    logger.warning(f"Entity extraction failed for {file_path}: {e}")

            except Exception as e:
                logger.warning(f"Metadata analysis failed for {file_path}: {e}")

        # --- Step 2: Additive Semantica enrichment ---
        try:
            from app.services.graph.factory import get_semantica_knowledge
            sk = get_semantica_knowledge()
        except Exception as e:
            logger.error(f"Semantica initialization failed — enrichment skipped: {e}", exc_info=True)
            sk = None

        if sk:
            for file_path in files:
                if not file_path.endswith(_markdown_exts):
                    continue

                try:
                    from app.git_ops import git_ops as _git_ops
                    content = await _git_ops.read_file(file_path)
                    if not content:
                        # File was deleted — remove its document from the index
                        import hashlib
                        doc_hash = hashlib.sha256(file_path.encode()).hexdigest()[:16]
                        try:
                            await sk.delete_entity(f"doc-{doc_hash}")
                        except Exception:
                            pass
                        continue

                    # Index document for semantic search
                    doc_name = file_path.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").title()
                    import hashlib
                    doc_hash = hashlib.sha256(file_path.encode()).hexdigest()[:16]
                    await sk.search.index_entity(
                        entity_id=f"doc-{doc_hash}",
                        name=doc_name,
                        entity_type="document",
                        attributes={"content_preview": content[:500], "source": "webhook"},
                        file_path=file_path,
                    )
                    embedded_count += 1

                    # Extract entities from content via Semantica NER
                    entities = await sk.extract_entities(content)
                    for entity in entities:
                        await sk.add_entity(
                            entity_id=entity["id"],
                            entity_type=entity["type"],
                            name=entity["name"],
                            properties=entity.get("metadata", {}),
                            file_path=file_path,
                        )
                        entity_count += 1

                except Exception as e:
                    logger.warning(f"Semantica enrichment failed for {file_path}: {e}")

        logger.info(
            f"Batch processed {len(files)} files: "
            f"{metadata_updates} metadata updates, "
            f"{embedded_count} embedded, {entity_count} entities extracted"
        )

        return {
            "status": "completed",
            "processed_files": files,
            "metadata_updates": metadata_updates,
            "embedded_count": embedded_count,
            "entity_count": entity_count,
            "person_files": person_files,
            "digest_files": digest_files,
        }
