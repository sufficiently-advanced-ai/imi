import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Configure logging
logger = logging.getLogger(__name__)

# Constants for input validation
MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10MB
MAX_YAML_BLOCK_SIZE = 1 * 1024 * 1024  # 1MB


class FrontmatterService:
    """Service for handling markdown frontmatter operations"""

    @staticmethod
    def extract_dates(content: str) -> tuple[datetime | None, datetime | None]:
        """Extract created and modified dates from YAML frontmatter.

        Args:
            content: Markdown content with YAML frontmatter

        Returns:
            Tuple of (created_date, modified_date), None for missing dates
        """
        try:
            # Extract YAML between --- markers
            if not content.startswith("---\n"):
                return None, None
            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                return None, None
            yaml_content = content[4:end_idx]

            # Parse YAML
            metadata = yaml.safe_load(yaml_content)
            if not metadata:
                return None, None

            # Extract dates
            created = (
                datetime.fromisoformat(str(metadata.get("created", "")))
                if metadata.get("created")
                else None
            )
            modified = (
                datetime.fromisoformat(str(metadata.get("modified", "")))
                if metadata.get("modified")
                else None
            )

            return created, modified
        except (ValueError, yaml.YAMLError):
            return None, None

    @staticmethod
    def extract_all(content: str) -> dict[str, Any] | None:
        """Extract all frontmatter metadata from markdown content.

        Args:
            content: Markdown content with YAML frontmatter

        Returns:
            Dict of metadata or None if no valid frontmatter
        """
        try:
            if not content.startswith("---\n"):
                return None
            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                return None

            yaml_content = content[4:end_idx]
            return yaml.safe_load(yaml_content)
        except yaml.YAMLError:
            return None

    @staticmethod
    def update(content: str, updates: dict[str, Any]) -> str:
        """Update frontmatter in markdown content.

        Args:
            content: Original markdown content
            updates: Dict of metadata updates to apply

        Returns:
            Updated markdown content with new frontmatter
        """
        try:
            # Extract existing frontmatter
            if not content.startswith("---\n"):
                # No existing frontmatter, create new
                yaml_str = yaml.dump(updates, default_flow_style=False)
                return f"---\n{yaml_str}---\n\n{content}"

            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                # Invalid frontmatter, create new
                yaml_str = yaml.dump(updates, default_flow_style=False)
                return f"---\n{yaml_str}---\n\n{content}"

            # Update existing frontmatter
            existing = yaml.safe_load(content[4:end_idx])
            if existing:
                existing.update(updates)
            else:
                existing = updates

            # Reconstruct document
            yaml_str = yaml.dump(existing, default_flow_style=False)
            return f"---\n{yaml_str}---\n{content[end_idx + 5:]}"

        except yaml.YAMLError:
            # On error, preserve original content
            return content

    def has_correct_frontmatter_format(self, content: str) -> bool:
        """Check if content has correct frontmatter format with --- delimiters.

        Args:
            content: Markdown content to check

        Returns:
            True if content has correct frontmatter format
        """
        if not content.strip():
            return False

        # Size validation
        if len(content) > MAX_CONTENT_SIZE:
            logger.warning(
                f"Content size {len(content)} exceeds maximum {MAX_CONTENT_SIZE}"
            )
            return False

        # Check if starts with ---
        if not content.startswith("---\n"):
            return False

        # Check if has closing ---
        end_idx = content.find("\n---\n", 4)
        if end_idx == -1:
            return False

        # Verify it's valid YAML
        try:
            yaml_content = content[4:end_idx]
            metadata = yaml.safe_load(yaml_content)

            # Validate structure if metadata exists
            if metadata and not self._validate_yaml_structure(metadata):
                return False

            return True
        except yaml.YAMLError:
            return False

    def has_incorrect_frontmatter_format(self, content: str) -> bool:
        """Check if content has incorrect frontmatter format with ```yaml blocks.

        Args:
            content: Markdown content to check

        Returns:
            True if content has incorrect frontmatter format
        """
        if not content.strip():
            return False

        # Size validation
        if len(content) > MAX_CONTENT_SIZE:
            logger.warning(
                f"Content size {len(content)} exceeds maximum {MAX_CONTENT_SIZE}"
            )
            return False

        # Check if starts with ```yaml
        return content.strip().startswith("```yaml")

    def extract_frontmatter_from_incorrect_format(
        self, content: str
    ) -> dict[str, Any] | None:
        """Extract frontmatter data from incorrect format with ```yaml blocks.

        Args:
            content: Markdown content with incorrect format

        Returns:
            Dict of metadata or None if extraction fails
        """
        if not content.strip().startswith("```yaml"):
            return None

        # Size validation before regex
        if len(content) > MAX_CONTENT_SIZE:
            logger.warning(f"Content too large for extraction: {len(content)} bytes")
            return None

        # Use limited search to prevent ReDoS
        try:
            # Limit search to first part of content
            search_limit = min(len(content), MAX_YAML_BLOCK_SIZE)
            limited_content = content[:search_limit]

            match = re.search(
                r"^```yaml$\n(.*?)^```$", limited_content, re.MULTILINE | re.DOTALL
            )
            if not match:
                return None

            yaml_content = match.group(1)
            metadata = yaml.safe_load(yaml_content)

            # Validate structure
            if metadata and not self._validate_yaml_structure(metadata):
                logger.warning("Invalid YAML structure detected")
                return None

            return metadata
        except yaml.YAMLError as e:
            logger.warning(f"YAML parsing error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during extraction: {e}")
            return None

    def convert_to_correct_format(self, content: str) -> str:
        """Convert content from incorrect to correct frontmatter format.

        Args:
            content: Markdown content potentially with incorrect format

        Returns:
            Content with correct frontmatter format
        """
        if not content:
            return content

        # Size validation
        if len(content) > MAX_CONTENT_SIZE:
            logger.error(f"Content too large to convert: {len(content)} bytes")
            return content

        # If already has correct format, return as-is
        if self.has_correct_frontmatter_format(content):
            return content

        # If has incorrect format, convert it
        if self.has_incorrect_frontmatter_format(content):
            try:
                # Limit search to prevent ReDoS
                search_limit = min(len(content), MAX_YAML_BLOCK_SIZE)
                limited_content = content[:search_limit]

                # Extract the YAML content
                match = re.search(
                    r"^```yaml$\n(.*?)^```$", limited_content, re.MULTILINE | re.DOTALL
                )
                if match:
                    yaml_content = match.group(1)
                    # Get the content after the yaml block
                    rest_of_content = content[match.end() :].lstrip("\n")

                    # Handle empty yaml content
                    if not yaml_content.strip():
                        return f"---\n---\n\n{rest_of_content}"

                    # Validate YAML before conversion
                    try:
                        metadata = yaml.safe_load(yaml_content)
                        if metadata and not self._validate_yaml_structure(metadata):
                            logger.warning(
                                "Skipping conversion due to invalid YAML structure"
                            )
                            return content
                    except yaml.YAMLError:
                        logger.warning("Skipping conversion due to invalid YAML")
                        return content

                    # Reconstruct with proper delimiters
                    # YAML content should not have trailing newline before closing ---
                    yaml_content_cleaned = yaml_content.rstrip("\n")
                    return f"---\n{yaml_content_cleaned}\n---\n\n{rest_of_content}"
            except Exception as e:
                logger.error(f"Error during conversion: {e}")
                return content

        # Return original content if no conversion needed
        return content

    def _validate_yaml_structure(self, metadata: dict[str, Any]) -> bool:
        """Validate YAML structure for safety and correctness.

        Args:
            metadata: Parsed YAML metadata

        Returns:
            True if structure is valid
        """

        # Check for excessively deep nesting
        def check_depth(obj, depth=0, max_depth=10):
            if depth > max_depth:
                return False
            if isinstance(obj, dict):
                return all(check_depth(v, depth + 1, max_depth) for v in obj.values())
            elif isinstance(obj, list):
                return all(check_depth(item, depth + 1, max_depth) for item in obj)
            return True

        if not check_depth(metadata):
            logger.warning("YAML structure too deeply nested")
            return False

        # Could add more validation here (e.g., required fields, data types)
        return True


class FrontmatterMigrationService:
    """Service for migrating files from incorrect to correct frontmatter format."""

    def __init__(self, git_ops=None):
        """Initialize migration service.

        Args:
            git_ops: GitOps instance for repository operations
        """
        self.git_ops = git_ops
        self.frontmatter_service = FrontmatterService()
        self.errors: list[dict[str, str]] = []

    def find_files_with_incorrect_format(
        self,
    ) -> tuple[list[Path], list[dict[str, str]]]:
        """Find all markdown files with incorrect frontmatter format.

        Returns:
            Tuple of (list of file paths with incorrect format, list of errors)
        """
        incorrect_files = []
        errors = []
        repo_path = Path(self.git_ops.repo_path if self.git_ops else ".")

        # Find all markdown files
        for md_file in repo_path.rglob("*.md"):
            if ".git" in md_file.parts:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
                if self.frontmatter_service.has_incorrect_frontmatter_format(content):
                    incorrect_files.append(md_file)
            except PermissionError:
                error_msg = f"Permission denied: {md_file}"
                logger.error(error_msg)
                errors.append({"file": str(md_file), "error": error_msg})
            except UnicodeDecodeError:
                error_msg = f"Unicode decode error: {md_file}"
                logger.error(error_msg)
                errors.append({"file": str(md_file), "error": error_msg})
            except Exception as e:
                error_msg = f"Failed to read {md_file}: {str(e)}"
                logger.error(error_msg)
                errors.append({"file": str(md_file), "error": error_msg})

        self.errors = errors
        return incorrect_files, errors

    def migrate_file(self, file_path: str | Path, commit: bool = False) -> bool:
        """Migrate a single file to correct frontmatter format.

        Args:
            file_path: Path to file to migrate
            commit: Whether to commit the change to git

        Returns:
            True if migration successful
        """
        file_path = Path(file_path)

        try:
            # Read file content
            content = file_path.read_text(encoding="utf-8")

            # Convert to correct format
            converted = self.frontmatter_service.convert_to_correct_format(content)

            # If no change needed, return success
            if content == converted:
                logger.info(f"No changes needed for {file_path}")
                return True

            # Write converted content
            file_path.write_text(converted, encoding="utf-8")
            logger.info(f"Successfully migrated {file_path}")

            # Commit if requested
            if commit and self.git_ops:
                self.git_ops.add_and_commit(
                    [str(file_path)],
                    f"fix(#35): Convert {file_path.name} to correct frontmatter format",
                )

            return True

        except PermissionError as e:
            error_msg = f"Permission denied when migrating {file_path}: {e}"
            logger.error(error_msg)
            self.errors.append({"file": str(file_path), "error": error_msg})
            return False
        except Exception as e:
            error_msg = f"Failed to migrate {file_path}: {e}"
            logger.error(error_msg)
            self.errors.append({"file": str(file_path), "error": error_msg})
            return False

    def _convert_file_content(self, content: str) -> str:
        """Internal method for content conversion (for testing)."""
        return self.frontmatter_service.convert_to_correct_format(content)

    def migrate_all_files(self, progress_callback=None) -> dict[str, Any]:
        """Migrate all files with incorrect format.

        Args:
            progress_callback: Optional callback(current, total, filename)

        Returns:
            Dict with migration statistics and errors
        """
        incorrect_files, find_errors = self.find_files_with_incorrect_format()
        total = len(incorrect_files)
        successful = 0
        failed = 0
        successfully_migrated_files = []

        for idx, file_path in enumerate(incorrect_files, 1):
            if progress_callback:
                progress_callback(idx, total, str(file_path))

            # Track original content for potential rollback
            original_content = None
            try:
                original_content = file_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to read {file_path} for backup: {e}")

            if self.migrate_file(file_path):
                # Verify the file was actually changed
                try:
                    new_content = file_path.read_text(encoding="utf-8")
                    if new_content != original_content:
                        successfully_migrated_files.append(file_path)
                        successful += 1
                    else:
                        # File wasn't actually changed
                        successful += 1
                except Exception as e:
                    logger.error(f"Failed to verify migration of {file_path}: {e}")
                    failed += 1
            else:
                failed += 1

        # Commit only successfully migrated files
        if self.git_ops and successfully_migrated_files:
            try:
                self.git_ops.add_and_commit(
                    [str(f) for f in successfully_migrated_files],
                    f"fix(#35): Migrate {len(successfully_migrated_files)} files to correct frontmatter format",
                )
            except Exception as e:
                logger.error(f"Failed to commit changes: {e}")

        return {
            "total_files": total,
            "successful": successful,
            "failed": failed,
            "errors": self.errors + find_errors,
            "migrated_files": [str(f) for f in successfully_migrated_files],
        }

    def dry_run(self) -> dict[str, Any]:
        """Perform a dry run to show what would be changed.

        Returns:
            Dict with files that would be changed and any errors
        """
        changes = []
        incorrect_files, errors = self.find_files_with_incorrect_format()

        for file_path in incorrect_files:
            changes.append({"file": str(file_path), "would_change": True})

        return {"changes": changes, "errors": errors}


frontmatter = FrontmatterService()
