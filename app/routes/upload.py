import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.git_ops import git_ops
from app.models import ProcessingStatus, TaskStatus, UploadResponse
from app.services.task_queue import global_task_queue
from app.services.upload import UploadService

router = APIRouter()
logger = logging.getLogger(__name__)

# Create upload service
upload_service = UploadService()

# Track upload tasks and their current stage
upload_tasks: dict[str, dict[str, Any]] = {}


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(
        "", description="Optional folder path to store the file in"
    ),
):
    """
    Upload a file without waiting for processing

    Args:
        file: The file to upload
        folder_path: Optional folder path to store the file in (e.g., "meetings", "people/team-a")
    """
    try:
        # Generate a unique upload ID
        upload_id = str(uuid.uuid4())

        # Read file content
        content = await file.read()
        content_str = content.decode("utf-8")

        # Basic validation only
        if len(content_str) > 25 * 1024:  # 25KB
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=file.filename,
                path="",
                errors=["File exceeds 25KB limit"],
                message="File too large",
            )

        # Simple file type validation
        if not (file.filename.endswith(".md") or file.filename.endswith(".txt")):
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=file.filename,
                path="",
                errors=["Invalid file type"],
                message="Only markdown and text files are supported",
            )

        # Determine the file path with folder
        if folder_path:
            # Clean up folder path (remove leading/trailing slashes)
            folder_path = folder_path.strip("/")

            # Create the final path
            final_path = os.path.join(folder_path, file.filename)

            # Create folder if needed
            full_folder_path = os.path.join(git_ops.repo_path, folder_path)
            os.makedirs(full_folder_path, exist_ok=True)
        else:
            # Use special folder detection based on filename patterns
            final_path = get_appropriate_path(file.filename)

        # Save file to repository
        full_file_path = os.path.join(git_ops.repo_path, final_path)
        with open(full_file_path, "w", encoding="utf-8") as f:
            f.write(content_str)

        # Start a background task for processing (don't wait for result)
        asyncio.create_task(process_file_background(final_path, upload_id))

        # Initialize task tracking for this upload
        upload_tasks[upload_id] = {
            "filename": file.filename,
            "path": file.filename,
            "stage": "validating",
            "status": "pending",
            "timestamp": datetime.utcnow().isoformat(),
            "errors": [],
        }

        # Make sure the task queue is running
        if not global_task_queue.running:
            await global_task_queue.start()

        # Add file processing to the task queue
        task_id = global_task_queue.enqueue(
            process_file_background,
            file.filename,
            upload_id,
            priority=10,  # High priority for uploads
        )

        # Link the task ID to our upload tracking
        upload_tasks[upload_id]["task_id"] = task_id

        # Return immediate success with upload_id for status checking
        return UploadResponse(
            status=ProcessingStatus.SUCCESS,
            filename=file.filename,
            path=final_path,
            errors=[],
            message="File uploaded successfully, processing started",
            upload_id=upload_id,
        )

    except Exception as e:
        logger.exception(f"Upload failed: {str(e)}")
        return UploadResponse(
            status=ProcessingStatus.FAILED,
            filename=file.filename,
            path="",
            errors=[str(e)],
            message="Upload failed",
        )


def get_appropriate_path(filename: str) -> str:
    """
    Determine the appropriate folder path based on filename pattern.

    Args:
        filename: The filename to check

    Returns:
        The appropriate path including any folder structure
    """
    # Check for meeting files
    if filename.startswith("meeting-"):
        return os.path.join("meetings", filename)

    # Check for person files
    elif filename.startswith("person-"):
        return os.path.join("people", filename)

    # Check for digest files
    elif filename.startswith("digest-") or filename.startswith("digest20"):
        return os.path.join("digests", filename)

    # Default: return filename (root location)
    return filename


# Background processing function
async def process_file_background(file_path: str, upload_id: str):
    """
    Process uploaded file in the background with stage tracking for UI updates

    Args:
        file_path: Path to the uploaded file
        upload_id: The ID for tracking this upload's status
    """
    try:
        # Update upload task status to running
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["status"] = "processing"

        # 1. Process metadata
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["stage"] = "metadata"

        from app.services.metadata import analyze_metadata

        metadata = await analyze_metadata(file_path)

        # Store metadata in upload task info
        if upload_id in upload_tasks and metadata:
            metadata_dict = metadata.dict()
            # Ensure type field exists
            if "type" not in metadata_dict:
                metadata_dict["type"] = "document"
            upload_tasks[upload_id]["metadata"] = metadata_dict

        # 1b. Embed document via Semantica (non-blocking)
        try:
            from app.services.graph.factory import get_semantica_knowledge
            sk = get_semantica_knowledge()
            if sk:
                content = await git_ops.read_file(file_path)
                if content:
                    # Index document for vector search
                    doc_name = file_path.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").title()
                    await sk.search.index_entity(
                        entity_id=f"doc-{file_path.replace('/', '-').replace('.', '-')}",
                        name=doc_name,
                        entity_type="document",
                        attributes={"content_preview": content[:500], "source": "upload"},
                        file_path=file_path,
                    )
                    # Extract and index entities from content
                    entities = await sk.extract_entities(content)
                    for entity in entities:
                        await sk.add_entity(
                            entity_id=entity["id"],
                            entity_type=entity["type"],
                            name=entity["name"],
                            properties=entity.get("metadata", {}),
                            file_path=file_path,
                        )
                    logger.info(f"Semantica: indexed document + {len(entities)} entities from {file_path}")
        except Exception as sem_err:
            logger.warning(f"Semantica document embedding failed (non-fatal): {sem_err}")

        # 2. Process person updates
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["stage"] = "profiles"

        from app.services.person_brain import PersonBrain

        brain = PersonBrain()
        await brain.update_profiles_from_content(file_path)

        # 3. Process digest updates
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["stage"] = "digest"

        from app.services.digest import DigestBrain

        date_str = datetime.now().strftime("%Y%m%d")
        digest_brain = DigestBrain()

        # Make sure to force refresh and add logger output for debugging
        logger.info(f"Processing digest for date {date_str} with force_refresh=True")
        digest_result = await digest_brain.process_digest(date_str, force_refresh=True)

        # Log the digest processing results
        if digest_result and digest_result.digest_file:
            logger.info(f"Digest generated successfully: {digest_result.digest_file}")
            logger.info(f"Processed files for digest: {digest_result.processed_files}")
        else:
            logger.warning(f"No digest file was generated for date {date_str}")
            if upload_id in upload_tasks:
                upload_tasks[upload_id]["errors"].append(
                    f"Failed to generate digest for date {date_str}"
                )

        # 4. Commit changes to git
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["stage"] = "commit"

        # Add git commit logic
        commit_message = f"Add {file_path} via upload API"
        modified_files = [file_path]  # Start with the uploaded file

        # Add person profile files if they were created/updated
        from app.services.file_cache import file_cache

        all_files = await file_cache.get_all_markdown_files()
        for file in all_files:
            if file.path.startswith("person-") and file.path not in modified_files:
                modified_files.append(file.path)

        # Add digest file if it was created/updated
        if date_str and digest_brain:
            digest_path = f"digest-{date_str}.md"
            if os.path.exists(os.path.join(git_ops.repo_path, digest_path)):
                modified_files.append(digest_path)

        # Commit and push all modified files
        try:
            await git_ops.commit_and_push(modified_files, commit_message)
            upload_tasks[upload_id]["commit_status"] = "success"
        except Exception as e:
            upload_tasks[upload_id]["commit_status"] = "failed"
            upload_tasks[upload_id]["errors"].append(f"Git commit failed: {str(e)}")

        # 5. Mark as complete
        if upload_id in upload_tasks:
            upload_tasks[upload_id]["stage"] = "complete"
            upload_tasks[upload_id]["status"] = "completed"

        # 6. Add to recently changed files for tracking
        from app.routes.webhook import MAX_RECENT_FILES, recently_changed_files

        # Add to start of list (most recent first)
        recently_changed_files.insert(
            0,
            {
                "path": file_path,
                "timestamp": datetime.utcnow().isoformat(),
                "upload_id": upload_id,
            },
        )

        # Trim list to prevent memory issues
        if len(recently_changed_files) > MAX_RECENT_FILES:
            recently_changed_files = recently_changed_files[:MAX_RECENT_FILES]

        logger.info(f"File {file_path} processed successfully (upload_id: {upload_id})")

    except Exception as e:
        # Update error state for status tracking
        logger.exception(f"Background processing error for {file_path}: {str(e)}")

        if upload_id in upload_tasks:
            upload_tasks[upload_id]["status"] = "failed"
            upload_tasks[upload_id]["errors"].append(str(e))

        # Log errors
        print(f"Background processing error for {file_path}: {str(e)}", file=sys.stderr)


@router.get("/upload/{upload_id}/status", response_model=UploadResponse)
async def get_upload_status(upload_id: str):
    """
    Get the current status of a file upload and its processing

    Args:
        upload_id: The ID returned from the original upload request

    Returns:
        UploadResponse with current status information
    """
    # Check if upload exists
    if upload_id not in upload_tasks:
        raise HTTPException(
            status_code=404, detail=f"Upload with ID {upload_id} not found"
        )

    # Get current status
    task_info = upload_tasks[upload_id]

    # Map status from our tracking to the response model
    status_map = {
        "pending": ProcessingStatus.SUCCESS,  # Still processing but ok
        "processing": ProcessingStatus.SUCCESS,  # Processing but ok
        "completed": ProcessingStatus.SUCCESS,
        "failed": ProcessingStatus.FAILED,
    }

    # Check task status from task queue if we have a task_id
    task_status = None
    if "task_id" in task_info:
        task = global_task_queue.get_task(task_info["task_id"])
        if task:
            task_status = task.status

    # Get ProcessingStatus enum value
    status = status_map.get(
        task_info.get("status", "pending"), ProcessingStatus.PARTIAL
    )

    # If task failed but we don't have local error, get it from task
    errors = task_info.get("errors", [])
    if task_status == TaskStatus.FAILED and not errors and "task_id" in task_info:
        task = global_task_queue.get_task(task_info["task_id"])
        if task and task.error:
            errors.append(str(task.error))

    # Generate appropriate message based on stage and status
    message = f"Processing file: {task_info.get('stage', 'validating')}"
    if task_info.get("status") == "completed":
        message = "File processed successfully"
    elif task_info.get("status") == "failed":
        message = "Processing failed"

    # Build response
    response_data = {
        "status": status,
        "filename": task_info.get("filename", ""),
        "path": task_info.get("path", ""),
        "errors": errors,
        "message": message,
        "upload_id": upload_id,
    }

    # Only include metadata if it's valid (has the required 'type' field)
    if (
        "metadata" in task_info
        and isinstance(task_info["metadata"], dict)
        and "type" in task_info["metadata"]
    ):
        response_data["metadata"] = task_info["metadata"]

    return UploadResponse(**response_data)
