import logging
import os
import sys

from app.config import settings
from app.domain.entities.services import EntityService
from app.models import DocumentMetadata, ProcessingStatus, UploadResponse
from app.services.digest import DigestBrain
from app.services.frontmatter import frontmatter as frontmatter_service

logger = logging.getLogger(__name__)


class UploadService:
    """Service for handling file uploads and processing"""

    def __init__(self):
        from app.git_ops import git_ops

        self.git_ops = git_ops
        self.entity_brain = EntityService()
        self.digest_brain = DigestBrain()

    async def simple_upload(self, filename: str, content: str) -> UploadResponse:
        """
        Simplified upload method that just saves the file without complex processing

        Steps:
        1. Validate the file
        2. Save to repository
        3. Process metadata (minimal)
        4. No person profiles or digest updates
        5. No git commit

        Returns UploadResponse with status
        """
        # Log action
        print(f"Starting simple upload for {filename}", file=sys.stderr)

        # Validate file
        if not self._validate_file(filename, content):
            print(f"File validation failed for {filename}", file=sys.stderr)
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=filename,
                path="",
                errors=["Invalid file: must be markdown/text and under 25KB"],
                message="File validation failed",
            )

        # Save file to repository
        repo_path = self._save_file(filename, content)
        if not repo_path:
            print(f"File save failed for {filename}", file=sys.stderr)
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=filename,
                path="",
                errors=["Failed to save file to repository"],
                message="File save failed",
            )

        print(f"File saved successfully: {repo_path}", file=sys.stderr)

        # Return simple success response
        return UploadResponse(
            status=ProcessingStatus.SUCCESS,
            filename=filename,
            path=repo_path,
            errors=[],
            message=f"File {filename} uploaded successfully",
        )

    async def process_upload(
        self, filename: str, content: str, progress_callback=None
    ) -> UploadResponse:
        """
        Process an uploaded file through the entire pipeline

        Steps:
        1. Validate the file
        2. Save to repository
        3. Process metadata
        4. Update person profiles
        5. Update digests
        6. Commit all changes

        Returns UploadResponse with status and metadata
        """
        errors = []
        modified_files = []

        # Validate file
        logger.info(f"Validating file {filename}")
        if not self._validate_file(filename, content):
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=filename,
                path="",
                errors=["Invalid file: must be markdown/text and under 25KB"],
                message="File validation failed",
            )

        # Save file to repository
        logger.info(f"Saving file {filename} to repository")
        repo_path = self._save_file(filename, content)
        if not repo_path:
            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=filename,
                path="",
                errors=["Failed to save file to repository"],
                message="File save failed",
            )

        # Add the original file to the list of modified files
        modified_files.append(repo_path)

        try:
            # Generate or update metadata
            logger.info(f"Processing metadata for {repo_path}")
            if progress_callback:
                progress_callback("metadata", f"Generating metadata for {filename}")

            metadata_result = await self._process_metadata(repo_path)
            if metadata_result[0]:
                # If metadata was updated, the file was modified
                modified_files.append(repo_path)
                if progress_callback:
                    progress_callback("metadata", f"Metadata generated for {filename}")

            # Update person profiles
            logger.info(f"Updating person profiles for {repo_path}")
            if progress_callback:
                progress_callback(
                    "profiles", f"Updating person profiles for {filename}"
                )

            person_files = await self._update_person_profiles(
                repo_path, metadata_result[0]
            )
            if person_files:
                modified_files.extend(person_files)
                logger.info(f"Updated profiles: {', '.join(person_files)}")
                if progress_callback:
                    progress_callback(
                        "profiles", f"Updated {len(person_files)} person profiles"
                    )

            # Update digests if applicable
            if metadata_result[0] and metadata_result[0].created:
                date_str = metadata_result[0].created.strftime("%Y%m%d")
                logger.info(f"Updating digest for date {date_str}")
                if progress_callback:
                    progress_callback("digest", f"Updating digest for {date_str}")

                digest_path = await self._update_digests(date_str)
                if digest_path:
                    modified_files.append(digest_path)
                    logger.info(f"Updated digest: {digest_path}")
                    if progress_callback:
                        progress_callback("digest", f"Digest updated: {digest_path}")

            # Remove duplicates from modified_files
            modified_files = list(set(modified_files))

            # Commit all changes
            commit_message = (
                f"{settings.BOT_COMMIT_PREFIX} Add {filename} via upload API"
            )
            logger.info(
                f"Committing {len(modified_files)} files with message: {commit_message}"
            )

            if progress_callback:
                progress_callback("commit", f"Committing {len(modified_files)} files")

            # Log all files being committed
            for file in modified_files:
                logger.info(f"  - {file}")

            # Enable the commit functionality with all modified files
            await self.git_ops.commit_and_push(modified_files, commit_message)

            if progress_callback:
                progress_callback("complete", f"Upload complete: {filename}")

            return UploadResponse(
                status=ProcessingStatus.SUCCESS
                if not errors
                else ProcessingStatus.PARTIAL,
                filename=filename,
                path=repo_path,
                metadata=metadata_result[0],
                errors=errors,
                message="File processed successfully"
                if not errors
                else "File processed with some issues",
            )

        except Exception as e:
            logger.error(f"Error processing upload: {str(e)}")
            errors.append(f"Processing error: {str(e)}")

            return UploadResponse(
                status=ProcessingStatus.FAILED,
                filename=filename,
                path=repo_path,
                errors=errors,
                message="File upload failed during processing",
            )

    def _validate_file(self, filename: str, content: str) -> bool:
        """
        Validate that the file meets requirements:
        - Markdown or text file
        - Under 25KB in size
        """
        # Check file size (25KB limit)
        if len(content.encode("utf-8")) > 25 * 1024:
            return False

        # Check file extension for markdown/text
        _, ext = os.path.splitext(filename)
        valid_extensions = [".md", ".txt", ".markdown"]

        return ext.lower() in valid_extensions

    def _save_file(self, filename: str, content: str) -> str:
        """
        Save file to repository
        Returns the repository path where the file was saved

        Handles special file types:
        - meeting files: saved in meetings/ folder
        - person files: saved in people/ folder
        - digest files: saved in digests/ folder
        """
        try:
            # Determine appropriate folder based on filename pattern
            base_path = ""

            # Check if it's a meeting file
            if filename.startswith("meeting-"):
                base_path = "meetings"
                logger.info(f"Detected meeting file: {filename}")

            # Check if it's a person file
            elif filename.startswith("person-"):
                base_path = "people"
                logger.info(f"Detected person file: {filename}")

            # Check if it's a digest file
            elif filename.startswith("digest-") or filename.startswith("digest"):
                base_path = "digests"
                logger.info(f"Detected digest file: {filename}")

            # Construct the full repository path
            if base_path:
                # Ensure the target directory exists
                target_dir = os.path.join(self.git_ops.repo_path, base_path)
                os.makedirs(target_dir, exist_ok=True)

                # Construct the final path with proper folder
                repo_path = os.path.join(base_path, filename)
                full_path = os.path.join(self.git_ops.repo_path, repo_path)
            else:
                # For regular files, just use the filename in the root
                repo_path = filename
                full_path = os.path.join(self.git_ops.repo_path, filename)

            # Write the file content
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            return repo_path  # Return relative path including folder
        except Exception as e:
            logger.error(f"Error saving file: {str(e)}")
            return ""

    async def _process_metadata(
        self, file_path: str
    ) -> tuple[DocumentMetadata | None, bool]:
        """
        Process metadata for the file
        Returns tuple of (metadata, is_new)
        """
        try:
            # Check if file already has frontmatter
            file_content = await self.git_ops.read_file(file_path)
            existing_metadata = frontmatter_service.extract_all(file_content)

            if existing_metadata:
                # File already has frontmatter, no need to generate
                logger.info(f"Using existing frontmatter for {file_path}")
                try:
                    metadata_obj = DocumentMetadata(**existing_metadata)
                    return metadata_obj, False
                except Exception as parse_error:
                    logger.error(f"Error parsing existing metadata: {str(parse_error)}")
                    logger.info(f"Will regenerate metadata for {file_path}")
                    # Continue to generate new metadata

            # Generate metadata for file without frontmatter
            from app.services.metadata import analyze_metadata

            # Log that we're generating metadata
            logger.info(f"Generating metadata for file: {file_path}")

            try:
                metadata_response = await analyze_metadata(file_path)

                # Record document processing metric
                from app.metrics import record_document_processed

                record_document_processed("upload")

                if metadata_response and metadata_response.metadata:
                    logger.info(f"Metadata generated successfully for {file_path}")
                    # Log some details about the metadata for debugging
                    metadata_obj = metadata_response.metadata
                    logger.info(f"  - Type: {metadata_obj.type}")
                    if hasattr(metadata_obj, "created") and metadata_obj.created:
                        logger.info(f"  - Created: {metadata_obj.created}")
                    logger.info(
                        f"  - Summary keys: {', '.join(metadata_obj.summary.keys() if metadata_obj.summary else [])}"
                    )
                    return metadata_response.metadata, True
                else:
                    logger.error(
                        f"Metadata generation returned empty response for {file_path}"
                    )
                    return None, False

            except Exception as metadata_error:
                logger.error(f"Metadata generation error: {str(metadata_error)}")
                # Continue without failing completely
                return None, False

        except Exception as e:
            logger.error(f"Error processing metadata: {str(e)}")
            return None, False

    async def _update_person_profiles(
        self, file_path: str, metadata: DocumentMetadata | None
    ) -> list[str]:
        """
        Update person profiles based on file content

        Returns a list of updated profile file paths
        """
        try:
            # Log that we're attempting to update profiles
            logger.info(f"Attempting to update person profiles for file: {file_path}")

            # Even if no participants are in metadata, analyze the file anyway
            # since the PersonBrain will extract people on its own
            try:
                updated_files = await self.entity_brain.update_profiles_from_content(
                    file_path
                )
                if updated_files:
                    logger.info(f"Updated profiles: {', '.join(updated_files)}")
                    return updated_files
                else:
                    logger.info(f"No person profiles updated for {file_path}")
                    return []
            except Exception as person_error:
                logger.error(f"Person brain error: {str(person_error)}")
                return []

        except Exception as e:
            logger.error(f"Error updating person profiles: {str(e)}")
            return []

    async def _update_digests(self, date_str: str) -> str | None:
        """
        Update digests based on file's date

        Returns the path of the updated digest file, or None if no digest was created/updated
        """
        try:
            # Log that we're updating digests
            logger.info(f"Attempting to update digest for date: {date_str}")

            try:
                # Process digests for the date
                result = await self.digest_brain.process_digest(
                    date_str, force_refresh=False, batch_mode=True
                )

                if result.digest_file:
                    logger.info(
                        f"Digest updated for date {date_str}: {result.digest_file}"
                    )
                    logger.info(
                        f"Processed files in digest: {', '.join(result.processed_files)}"
                    )
                    return result.digest_file
                else:
                    logger.info(f"No digest file created/updated for date {date_str}")
                    return None

            except Exception as digest_error:
                # Log specific error but don't fail the entire upload
                logger.error(f"Digest processing error: {str(digest_error)}")
                return None

        except Exception as e:
            logger.error(f"Error updating digests: {str(e)}")
            return None
