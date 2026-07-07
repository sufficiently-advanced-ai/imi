"""
Enrichment Job Manager - Handles async bulk enrichment jobs.

This service manages background enrichment jobs with progress tracking.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from ..git_ops import git_ops
from ..services.entity_enrichment import get_entity_enrichment_service

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EnrichmentJob:
    """Represents an enrichment job"""

    def __init__(self, job_id: str, entity_ids: list[str], options: dict[str, Any]):
        self.job_id = job_id
        self.entity_ids = entity_ids
        self.options = options
        self.status = JobStatus.PENDING
        self.progress = 0
        self.total_entities = len(entity_ids)
        self.processed_entities = 0
        self.failed_entities = 0
        self.results = []
        self.created_at = datetime.utcnow()
        self.started_at = None
        self.completed_at = None
        self.error = None

    def to_dict(self) -> dict[str, Any]:
        """Convert job to dictionary for storage/API response"""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "total_entities": self.total_entities,
            "processed_entities": self.processed_entities,
            "failed_entities": self.failed_entities,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "error": self.error,
        }


class EnrichmentJobManager:
    """Manages async enrichment jobs"""

    def __init__(self):
        self.jobs: dict[str, EnrichmentJob] = {}
        self.job_storage_path = os.path.join(git_ops.repo_path, ".enrichment_jobs")
        os.makedirs(self.job_storage_path, exist_ok=True)

        # Background task tracking
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def create_job(self, entity_ids: list[str], options: dict[str, Any]) -> str:
        """Create a new enrichment job and start processing"""
        job_id = str(uuid4())
        job = EnrichmentJob(job_id, entity_ids, options)

        # Store job
        self.jobs[job_id] = job
        self._save_job_state(job)

        # Start background processing
        task = asyncio.create_task(self._process_job(job))
        self._running_tasks[job_id] = task

        logger.info(f"Created enrichment job {job_id} for {len(entity_ids)} entities")

        return job_id

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get status of a job"""
        # Check in-memory jobs first
        if job_id in self.jobs:
            return self.jobs[job_id].to_dict()

        # Check persisted jobs
        job_file = os.path.join(self.job_storage_path, f"{job_id}.json")
        if os.path.exists(job_file):
            with open(job_file) as f:
                return json.load(f)

        return None

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job"""
        if job_id in self._running_tasks:
            task = self._running_tasks[job_id]
            if not task.done():
                task.cancel()

                # Update job status
                if job_id in self.jobs:
                    job = self.jobs[job_id]
                    job.status = JobStatus.CANCELLED
                    job.completed_at = datetime.utcnow()
                    self._save_job_state(job)

                logger.info(f"Cancelled enrichment job {job_id}")
                return True

        return False

    async def _process_job(self, job: EnrichmentJob):
        """Process enrichment job in background"""
        try:
            job.status = JobStatus.PROCESSING
            job.started_at = datetime.utcnow()
            self._save_job_state(job)

            enrichment_service = get_entity_enrichment_service()

            # Process entities in batches
            batch_size = 10
            for i in range(0, len(job.entity_ids), batch_size):
                if job.status == JobStatus.CANCELLED:
                    break

                batch = job.entity_ids[i : i + batch_size]

                # Process batch
                for entity_id in batch:
                    try:
                        # Enrich entity
                        result = await enrichment_service.enrich_entity(
                            entity_id,
                            sources=job.options.get("sources", []),
                            fields=job.options.get("fields", []),
                            confidence_threshold=job.options.get(
                                "confidence_threshold", 0.7
                            ),
                        )

                        job.results.append(
                            {
                                "entity_id": entity_id,
                                "success": True,
                                "enriched_fields": result.get("new_fields", []),
                                "confidence_boost": result.get("confidence_boost", 0),
                            }
                        )

                        job.processed_entities += 1

                    except Exception as e:
                        logger.error(f"Failed to enrich entity {entity_id}: {e}")
                        job.results.append(
                            {"entity_id": entity_id, "success": False, "error": str(e)}
                        )
                        job.failed_entities += 1

                # Update progress
                job.progress = int(
                    (job.processed_entities + job.failed_entities)
                    / job.total_entities
                    * 100
                )
                self._save_job_state(job)

                # Small delay to prevent overwhelming the system
                await asyncio.sleep(0.1)

            # Mark as completed
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            self._save_job_state(job)

            logger.info(
                f"Completed enrichment job {job.job_id}: {job.processed_entities} succeeded, {job.failed_entities} failed"
            )

        except asyncio.CancelledError:
            # Job was cancelled
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.utcnow()
            self._save_job_state(job)
            raise

        except Exception as e:
            logger.error(f"Enrichment job {job.job_id} failed: {e}")
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.utcnow()
            self._save_job_state(job)

        finally:
            # Clean up task reference
            self._running_tasks.pop(job.job_id, None)

    def _save_job_state(self, job: EnrichmentJob):
        """Save job state to disk"""
        job_file = os.path.join(self.job_storage_path, f"{job.job_id}.json")

        # Include results in saved state
        state = job.to_dict()
        state["results"] = job.results[:100]  # Limit saved results

        with open(job_file, "w") as f:
            json.dump(state, f, indent=2)

    def get_all_jobs(self) -> list[dict[str, Any]]:
        """Get all jobs (recent first)"""
        all_jobs = []

        # Add in-memory jobs
        for job in self.jobs.values():
            all_jobs.append(job.to_dict())

        # Add persisted jobs not in memory
        for filename in os.listdir(self.job_storage_path):
            if filename.endswith(".json"):
                job_id = filename[:-5]
                if job_id not in self.jobs:
                    job_file = os.path.join(self.job_storage_path, filename)
                    with open(job_file) as f:
                        all_jobs.append(json.load(f))

        # Sort by created_at descending
        all_jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)

        return all_jobs

    def cleanup_old_jobs(self, days: int = 7):
        """Clean up job files older than specified days"""
        cutoff = datetime.utcnow().timestamp() - (days * 24 * 60 * 60)

        for filename in os.listdir(self.job_storage_path):
            if filename.endswith(".json"):
                job_file = os.path.join(self.job_storage_path, filename)
                if os.path.getmtime(job_file) < cutoff:
                    os.remove(job_file)
                    logger.info(f"Cleaned up old job file: {filename}")


# Global job manager instance
_job_manager = None


def get_job_manager() -> EnrichmentJobManager:
    """Get or create the global job manager instance"""
    global _job_manager
    if _job_manager is None:
        _job_manager = EnrichmentJobManager()
    return _job_manager
