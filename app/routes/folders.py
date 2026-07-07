import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..git_ops import git_ops
from ..services.auth import get_current_user
from ..services.digest import DIGEST_FOLDER

# Literal value of MEETINGS_FOLDER — avoids importing meeting_brain (meeting-stack
# is hosted-only; importing it in kb mode would violate the open-core seam).
# Keep in sync with app.services.meeting_brain.MEETINGS_FOLDER.
MEETINGS_FOLDER = "meetings"


# Define models for API
class FolderInfo(BaseModel):
    path: str
    name: str
    is_directory: bool = True


class FolderContents(BaseModel):
    path: str
    folders: list[FolderInfo] = []
    files: list[str] = []


class FolderCreateRequest(BaseModel):
    path: str


class FolderCreateResponse(BaseModel):
    path: str
    message: str
    success: bool


# Create API router
router = APIRouter(
    prefix="/folders",
    tags=["folders"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", response_model=list[FolderInfo])
async def list_root_folders(user: dict = Depends(get_current_user)):
    """List the root folders in the repository.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} listing root folders")
    try:
        # Standard folders that should always exist
        standard_folders = [
            FolderInfo(path=MEETINGS_FOLDER, name=MEETINGS_FOLDER),
            FolderInfo(path="people", name="people"),
            FolderInfo(path="projects", name="projects"),
            FolderInfo(path="teams", name="teams"),
            FolderInfo(path=DIGEST_FOLDER, name=DIGEST_FOLDER),
        ]

        # Get additional folders from repository
        folders = await git_ops.list_folders()

        # Filter out the standard folders we already added
        standard_paths = {folder.path for folder in standard_folders}
        additional_folders = [
            FolderInfo(path=folder.path, name=folder.name)
            for folder in folders
            if folder.path not in standard_paths and folder.path != "root"
        ]

        # Combine standard and additional folders
        all_folders = standard_folders + additional_folders

        # Sort alphabetically
        all_folders.sort(key=lambda x: x.name)

        return all_folders

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list folders: {str(e)}")


class FileInfo(BaseModel):
    """File information including path and optional metadata"""

    path: str
    name: str
    is_directory: bool = False
    size: int | None = None
    last_modified: datetime | None = None
    metadata: dict[str, Any] | None = None


class FolderContentsWithMetadata(FolderContents):
    """Extended folder contents with additional metadata for files"""

    files_info: list[FileInfo] = []
    cache_time: datetime | None = None


@router.get("/{path:path}", response_model=FolderContentsWithMetadata)
async def get_folder_contents(
    path: str = "",
    include_metadata: bool = False,
    from_cache: bool = True,
    folders_only: bool = False,
    user: dict = Depends(get_current_user),
):
    """
    Get contents of a folder with optional file metadata.

    Requires authentication.

    Args:
        path: The folder path to get contents for
        include_metadata: Whether to include file metadata
        from_cache: Whether to allow serving from cache
        folders_only: Whether to only return folders (skip file processing)
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"User {user.get('email', 'unknown')} accessing folder contents: {path}"
    )
    try:
        from ..services.file_cache import folder_cache

        # Normalize path (remove trailing slash)
        normalized_path = path.rstrip("/")
        cache_key = f"{normalized_path}:{include_metadata}:{folders_only}"

        # Try to get from cache first if allowed
        if from_cache:
            cached_result = folder_cache.get(cache_key)
            if cached_result:
                return cached_result

        # List folders in the repository
        folders = await git_ops.list_folders(normalized_path)

        if not folders:
            raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

        # Process the root folder returned by list_folders
        root_folder = folders[0]

        # Extract folders and files
        folder_infos = []
        file_paths = []
        files_info = []

        if root_folder.children:
            for child in root_folder.children:
                if isinstance(child, str):
                    # This is a file
                    if not folders_only:
                        # Only process files if not folders_only mode
                        full_path = os.path.join(git_ops.repo_path, child)
                        file_paths.append(child)

                        # Extract file name from path
                        file_name = os.path.basename(child)

                        # Create basic file info
                        file_info = FileInfo(
                            path=child, name=file_name, is_directory=False
                        )

                        # Add size and modification time
                        if os.path.exists(full_path):
                            file_info.size = os.path.getsize(full_path)
                            file_info.last_modified = datetime.fromtimestamp(
                                os.path.getmtime(full_path)
                            )

                        # Add metadata if requested
                        if include_metadata and child.lower().endswith(".md"):
                            try:
                                from ..services.frontmatter import extract_frontmatter

                                with open(full_path, encoding="utf-8") as f:
                                    content = f.read()
                                    frontmatter, _ = extract_frontmatter(content)
                                    if frontmatter:
                                        file_info.metadata = frontmatter
                            except Exception:
                                # Silently continue if metadata extraction fails
                                pass

                        files_info.append(file_info)
                else:
                    # This is a folder
                    folder_infos.append(FolderInfo(path=child.path, name=child.name))

        # Create response with cache timestamp
        result = FolderContentsWithMetadata(
            path=normalized_path,
            folders=folder_infos,
            files=file_paths,
            files_info=files_info,
            cache_time=datetime.utcnow(),
        )

        # Store in cache for future requests
        folder_cache.set(cache_key, result)

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get folder contents: {str(e)}"
        )


@router.post("/", response_model=FolderCreateResponse)
async def create_folder(
    request: FolderCreateRequest, user: dict = Depends(get_current_user)
):
    """Create a new folder in the repository.

    Requires authentication.
    """
    # Log user action
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"User {user.get('email', 'unknown')} creating folder: {request.path}")
    try:
        # Normalize path (remove trailing slash)
        path = request.path.rstrip("/")

        # Create the folder
        success = await git_ops.create_folder(path)

        if success:
            # Commit the .gitkeep file to ensure folder is tracked
            await git_ops.commit_and_push(
                [f"{path}/.gitkeep"],
                f"{settings.BOT_COMMIT_PREFIX} Create folder {path}",
            )

            return FolderCreateResponse(
                path=path, message=f"Folder {path} created successfully", success=True
            )
        else:
            return FolderCreateResponse(
                path=path, message=f"Failed to create folder {path}", success=False
            )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create folder: {str(e)}"
        )
