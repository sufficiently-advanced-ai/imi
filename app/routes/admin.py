import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.domain.entities.services import EntityService

from ..git_ops import git_ops
from ..services.digest import DigestBrain
from ..services.frontmatter import frontmatter
from ..services.metadata import analyze_metadata
from ..services.task_queue import global_task_queue

# Mounted under /api like the other JSON API routers — the docs and
# .env.example reference /api/admin/* paths.
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _normalize_person_name(person_id: str) -> str:
    """Extract clean person name without role information.

    'Adam Robles (Recruiter)' -> 'Adam Robles'
    """
    return re.sub(r"\s*\([^)]*\)", "", person_id).strip()


def _log_admin(
    operation: str, details: dict[str, Any], error: Exception = None
) -> None:
    """Log admin operations to stderr with structured format."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "component": "admin",
        "operation": operation,
        "status": "error" if error else "success",
        "details": details,
    }
    if error:
        log_entry["error"] = str(error)
    print(json.dumps(log_entry), file=sys.stderr)


async def _process_metadata_regeneration(task_id: str):
    """Process metadata regeneration for all markdown files."""
    try:
        _log_admin("metadata_regeneration_start", {"task_id": task_id})

        # Get all markdown files
        all_files = await git_ops.read_markdown_files()

        _log_admin(
            "metadata_files_found", {"task_id": task_id, "file_count": len(all_files)}
        )

        # Process each file
        processed_files = []
        for file in all_files:
            try:
                result = await analyze_metadata(file.path)
                if result:
                    processed_files.append(file.path)
                    _log_admin(
                        "metadata_file_processed",
                        {"task_id": task_id, "file": file.path},
                    )
            except Exception as e:
                _log_admin(
                    "metadata_file_error",
                    {"task_id": task_id, "file": file.path},
                    error=e,
                )

        # Commit all changes
        if processed_files:
            commit_message = (
                f"[bot] Regenerated metadata for {len(processed_files)} files"
            )
            await git_ops.commit_and_push(processed_files, commit_message)
            _log_admin(
                "metadata_regeneration_complete",
                {
                    "task_id": task_id,
                    "processed_count": len(processed_files),
                    "commit_message": commit_message,
                },
            )

        return {
            "processed_files": processed_files,
            "total_files": len(all_files),
            "status": "completed",
        }

    except Exception as e:
        _log_admin("metadata_regeneration_error", {"task_id": task_id}, error=e)
        raise


async def _process_people_regeneration(task_id: str):
    """Process people profile regeneration using batch mode."""
    try:
        _log_admin("people_regeneration_start", {"task_id": task_id})

        # Get all markdown files
        all_files = await git_ops.read_markdown_files()

        # Extract people mentions from frontmatter
        # Use normalized names as keys to group all mentions of the same person
        normalized_person_to_files: dict[str, list[str]] = defaultdict(list)
        normalized_to_full_names: dict[str, set[str]] = defaultdict(set)

        for file in all_files:
            try:
                metadata = frontmatter.extract_all(file.content)
                if (
                    metadata
                    and "summary" in metadata
                    and "participants" in metadata["summary"]
                ):
                    participants = metadata["summary"]["participants"]
                    if isinstance(participants, list):
                        for person in participants:
                            if isinstance(person, str):
                                # Normalize the person name to group variants together
                                normalized_name = _normalize_person_name(person)
                                if normalized_name:  # Skip empty names
                                    normalized_person_to_files[normalized_name].append(
                                        file.path
                                    )
                                    normalized_to_full_names[normalized_name].add(
                                        person
                                    )
            except Exception as e:
                _log_admin(
                    "people_extraction_error", {"file": file.path, "error": str(e)}
                )

        _log_admin(
            "people_extraction_complete",
            {
                "task_id": task_id,
                "unique_people": len(normalized_person_to_files),
                "total_mentions": sum(
                    len(files) for files in normalized_person_to_files.values()
                ),
                "deduplicated_from": sum(
                    len(names) for names in normalized_to_full_names.values()
                ),
            },
        )

        # Process each person with their files
        entity_brain = EntityService()
        all_updated_files = []

        # Process using normalized names to avoid duplicates
        for normalized_name, trigger_file_paths in normalized_person_to_files.items():
            try:
                # Read the actual trigger files
                trigger_files = await git_ops.read_markdown_files(
                    paths=trigger_file_paths, treat_missing_as_error=False
                )

                if not trigger_files:
                    continue

                updated_files = await entity_brain.process_person_updates_from_triggers(
                    person_id=normalized_name,  # Use normalized name
                    trigger_files=trigger_files,
                    batch_mode=True,
                    force_refresh=True,
                )
                all_updated_files.extend(updated_files)

                # Log with details about deduplication
                full_names = list(normalized_to_full_names[normalized_name])
                _log_admin(
                    "person_processed",
                    {
                        "task_id": task_id,
                        "person": normalized_name,
                        "trigger_files": len(trigger_files),
                        "updated_files": len(updated_files),
                        "consolidated_from": full_names
                        if len(full_names) > 1
                        else None,
                    },
                )
            except Exception as e:
                _log_admin(
                    "person_processing_error",
                    {"task_id": task_id, "person": normalized_name},
                    error=e,
                )

        # Commit all changes
        if all_updated_files:
            unique_files = list(set(all_updated_files))
            commit_message = (
                f"[bot] Regenerated {len(normalized_person_to_files)} person profiles"
            )
            await git_ops.commit_and_push(unique_files, commit_message)
            _log_admin(
                "people_regeneration_complete",
                {
                    "task_id": task_id,
                    "people_processed": len(normalized_person_to_files),
                    "files_updated": len(unique_files),
                    "commit_message": commit_message,
                },
            )

        return {
            "people_processed": len(normalized_person_to_files),
            "files_updated": len(all_updated_files),
            "status": "completed",
        }

    except Exception as e:
        _log_admin("people_regeneration_error", {"task_id": task_id}, error=e)
        raise


async def _process_digests_regeneration(task_id: str):
    """Process digest regeneration for all dates with content."""
    try:
        _log_admin("digests_regeneration_start", {"task_id": task_id})

        # Get all markdown files
        all_files = await git_ops.read_markdown_files()

        # Group files by date
        date_to_files: dict[str, list[str]] = defaultdict(list)

        for file in all_files:
            try:
                metadata = frontmatter.extract_all(file.content)
                if metadata:
                    # Check for created or modified date
                    date_str = metadata.get("created") or metadata.get("modified")
                    if date_str:
                        # Extract just the date part (YYYY-MM-DD)
                        if isinstance(date_str, str):
                            date_part = date_str.split("T")[0]
                            date_to_files[date_part].append(file.path)
            except Exception as e:
                _log_admin(
                    "date_extraction_error", {"file": file.path, "error": str(e)}
                )

        _log_admin(
            "date_extraction_complete",
            {
                "task_id": task_id,
                "unique_dates": len(date_to_files),
                "total_files": sum(len(files) for files in date_to_files.values()),
            },
        )

        # Process each date
        digest_brain = DigestBrain()
        all_digest_files = []

        for date_str in date_to_files.keys():
            try:
                # Convert date format from YYYY-MM-DD to YYYYMMDD
                date_no_dash = date_str.replace("-", "")

                # Process digest for this date
                result = await digest_brain.process_digest(
                    date=date_no_dash, force_refresh=True, batch_mode=True
                )
                if result and result.file_path:
                    all_digest_files.append(result.file_path)
                    _log_admin(
                        "digest_processed",
                        {
                            "task_id": task_id,
                            "date": date_str,
                            "relevant_files": len(date_to_files[date_str]),
                        },
                    )
            except Exception as e:
                _log_admin(
                    "digest_processing_error",
                    {"task_id": task_id, "date": date_str},
                    error=e,
                )

        # Commit all changes
        if all_digest_files:
            commit_message = f"[bot] Regenerated {len(all_digest_files)} daily digests"
            await git_ops.commit_and_push(all_digest_files, commit_message)
            _log_admin(
                "digests_regeneration_complete",
                {
                    "task_id": task_id,
                    "digests_created": len(all_digest_files),
                    "commit_message": commit_message,
                },
            )

        return {
            "dates_processed": len(date_to_files),
            "digests_created": len(all_digest_files),
            "status": "completed",
        }

    except Exception as e:
        _log_admin("digests_regeneration_error", {"task_id": task_id}, error=e)
        raise


@router.post("/backfill-signal-index")
async def backfill_signal_index():
    """Re-index every persisted signal for the current tenant (issue #951).

    Restart recovery / post-migration backfill for the semantic search index.
    Runs on the task queue (embedding hundreds of signals takes minutes); the
    tenant context propagates with the task.
    """
    try:
        from app.services.signal_indexing import backfill_signals, vector_stack_available

        if not vector_stack_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vector stack is not initialized",
            )

        task_id = f"backfill_signal_index_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        async def _run(_task_id: str):
            total, indexed, skipped = backfill_signals()
            _log_admin(
                "backfill_signal_index_done",
                {"task_id": _task_id, "total": total, "indexed": indexed, "skipped": skipped},
            )

        global_task_queue.enqueue(_run, task_id, task_id=task_id, priority=5)
        return {"status": "queued", "task_id": task_id}

    except HTTPException:
        raise
    except Exception as e:
        _log_admin("backfill_signal_index_error", {}, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/backfill-memory-index")
async def backfill_memory_index():
    """Re-index every governed memory record: signals, captures, agent memories.

    One-command recovery for the vector index — after switching
    VECTOR_BACKEND (e.g. onto the sqlite default), cloning an existing
    corpus, or any period when index-on-write was unavailable. Idempotent on
    upsert-by-id backends (sqlite, pgvector). Runs on the task queue
    (embedding the corpus takes minutes).
    """
    try:
        from app.services.signal_indexing import (
            backfill_agent_memories,
            backfill_captures,
            backfill_signals,
            vector_stack_available,
        )

        if not vector_stack_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vector stack is not initialized",
            )

        task_id = f"backfill_memory_index_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        async def _run(_task_id: str):
            # Each kind runs independently: one failing backfill must not
            # abort the others or suppress the summary log.
            backfills = {
                "signals": backfill_signals,
                "captures": backfill_captures,
                "agent_memories": backfill_agent_memories,
            }
            summary: dict = {"task_id": _task_id}
            for kind, backfill in backfills.items():
                try:
                    t, i, s = backfill()
                    summary[kind] = {"total": t, "indexed": i, "skipped": s}
                except Exception as e:
                    summary[kind] = {"error": str(e)}
            _log_admin("backfill_memory_index_done", summary)

        global_task_queue.enqueue(_run, task_id, task_id=task_id, priority=5)
        return {"status": "queued", "task_id": task_id}

    except HTTPException:
        raise
    except Exception as e:
        _log_admin("backfill_memory_index_error", {}, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e


@router.post("/regenerate/metadata")
async def regenerate_metadata():
    """Regenerate metadata for all markdown files in the repository."""
    try:
        # Create task ID
        task_id = f"regenerate_metadata_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Enqueue the task
        global_task_queue.enqueue(
            _process_metadata_regeneration, task_id, task_id=task_id, priority=5
        )

        # Get file count for estimation
        all_files = await git_ops.read_markdown_files()
        file_count = len(all_files)

        # Return custom response format
        return {
            "status": "queued",
            "task_id": task_id,
            "message": f"Regenerating metadata for {file_count} files",
            "estimated_targets": file_count,
        }

    except Exception as e:
        _log_admin("metadata_endpoint_error", {}, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/regenerate/people")
async def regenerate_people():
    """Regenerate all person profiles based on file mentions."""
    try:
        # Create task ID
        task_id = f"regenerate_people_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Quick scan to estimate people count (using normalized names)
        all_files = await git_ops.read_markdown_files()
        unique_normalized_people: set[str] = set()

        for file in all_files:
            try:
                metadata = frontmatter.extract_all(file.content)
                if (
                    metadata
                    and "summary" in metadata
                    and "participants" in metadata["summary"]
                ):
                    participants = metadata["summary"]["participants"]
                    if isinstance(participants, list):
                        for p in participants:
                            if isinstance(p, str):
                                normalized_name = _normalize_person_name(p)
                                if normalized_name:
                                    unique_normalized_people.add(normalized_name)
            except Exception:
                pass

        # Enqueue the task
        global_task_queue.enqueue(
            _process_people_regeneration, task_id, task_id=task_id, priority=5
        )

        # Return custom response format
        return {
            "status": "queued",
            "task_id": task_id,
            "message": f"Regenerating {len(unique_normalized_people)} person profiles from {len(all_files)} files",
            "estimated_targets": len(unique_normalized_people),
        }

    except Exception as e:
        _log_admin("people_endpoint_error", {}, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/regenerate/digests")
async def regenerate_digests():
    """Regenerate all daily digests based on file dates."""
    try:
        # Create task ID
        task_id = f"regenerate_digests_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Quick scan to estimate date count
        all_files = await git_ops.read_markdown_files()
        unique_dates: set[str] = set()

        for file in all_files:
            try:
                metadata = frontmatter.extract_all(file.content)
                if metadata:
                    date_str = metadata.get("created") or metadata.get("modified")
                    if date_str and isinstance(date_str, str):
                        date_part = date_str.split("T")[0]
                        unique_dates.add(date_part)
            except Exception:
                pass

        # Enqueue the task
        global_task_queue.enqueue(
            _process_digests_regeneration, task_id, task_id=task_id, priority=5
        )

        # Return custom response format
        return {
            "status": "queued",
            "task_id": task_id,
            "message": f"Regenerating digests for {len(unique_dates)} dates",
            "estimated_targets": len(unique_dates),
        }

    except Exception as e:
        _log_admin("digests_endpoint_error", {}, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
