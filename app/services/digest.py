import os
import sys
from datetime import datetime

from anthropic import APIConnectionError, APIStatusError, RateLimitError
from fastapi import HTTPException

from ..config import settings
from ..git_ops import git_ops
from ..models import DigestResponse, File
from .claude_client import get_claude_client
from .frontmatter import frontmatter

# Constants
DIGEST_FOLDER = "digests"


class DigestBrain:
    def __init__(self):
        pass

    async def process_digest(
        self, date: str, force_refresh: bool = False, batch_mode: bool = False
    ) -> DigestResponse:
        """Process and generate knowledge digest for a given date.

        Args:
            date: Date string in YYYYMMDD format
            force_refresh: Whether to regenerate even if digest exists
            batch_mode: If True, don't commit changes. Caller is responsible for committing.
        """
        try:
            # Get relevant files
            files = await self._get_relevant_files(date)
            print(f"Found {len(files)} relevant files for date {date}", file=sys.stderr)

            # If no files found, return an empty response instead of raising an error
            # This makes the function more resilient for callers
            if not files:
                print(
                    f"No files found for date {date}, returning empty response",
                    file=sys.stderr,
                )
                return DigestResponse(digest_file="", processed_files=[], created=False)

            # Check for existing digest in both formats
            existing_digest = await self.get_digest_for_date(date)

            # Only return existing if not force_refresh
            if existing_digest and not force_refresh:
                print(f"Using existing digest {existing_digest.path}", file=sys.stderr)
                return DigestResponse(
                    digest_file=existing_digest.path,
                    processed_files=[f.path for f in files],
                    created=False,
                )

            # Generate new digest
            print(
                f"Generating new digest for {date} with {len(files)} files",
                file=sys.stderr,
            )
            content = await self._generate_digest(files, date)

            # Save digest
            saved_path = await self._save_digest(content, date, batch_mode)
            print(f"Saved digest to {saved_path}", file=sys.stderr)

            return DigestResponse(
                digest_file=saved_path,
                processed_files=[f.path for f in files],
                created=True,
            )

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Digest generation failed: {str(e)}"
            )

    async def _get_relevant_files(self, date: str) -> list[File]:
        """Get markdown files relevant for the given date."""
        try:
            # Import file cache
            from ..services.file_cache import file_cache

            # Get all markdown files using cache
            all_files = await file_cache.get_all_markdown_files()

            # Filter out digest files (both old and new format, in all locations)
            filtered_files = [
                f
                for f in all_files
                if not (
                    f.path.startswith("digest-")
                    or f.path.startswith("digest")
                    or f.path.startswith(f"{DIGEST_FOLDER}/digest")
                )
            ]

            # Filter files by date
            target_date = datetime.strptime(date, "%Y%m%d")
            relevant_paths = set()
            relevant_files = []

            for file in filtered_files:
                # Skip if already processed
                if file.path in relevant_paths:
                    continue

                # Check frontmatter dates
                created, modified = frontmatter.extract_dates(file.content)

                # Add file if it matches any criteria
                if (
                    (created and created.date() == target_date.date())
                    or (modified and modified.date() == target_date.date())
                    or date in os.path.basename(file.path)
                ):
                    relevant_paths.add(file.path)
                    relevant_files.append(file)

            print(
                f"Found {len(relevant_files)} relevant files for date {date}",
                file=sys.stderr,
            )
            return relevant_files

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to get relevant files: {str(e)}"
            )

    async def _generate_digest(self, files: list[File], date: str) -> str:
        """Generate digest content using Claude."""
        try:
            # Load digest prompt template (use the correct import)
            from ..services.prompts import format_prompt, load_prompt_template

            template = load_prompt_template("digest")

            # Format date for the prompt
            parsed_date = datetime.strptime(date, "%Y%m%d")
            formatted_date = parsed_date.strftime("%Y-%m-%d")

            # Create question that includes the date
            question = f"Create a knowledge digest for {formatted_date}"

            # Format prompt with files and date context
            prompt = format_prompt(template, files, question)

            # Estimate token count (~ 4 chars per token)
            estimated_tokens = len(prompt) // 4

            # Get response from Claude using the new client
            print(
                f"Sending digest prompt for {date} with {estimated_tokens} estimated tokens",
                file=sys.stderr,
            )

            # Record start time for performance tracking
            start_time = datetime.utcnow()

            claude_client = get_claude_client()
            message = await claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_SONNET_MODEL,
                max_tokens=4096,
                estimate_token_count=estimated_tokens,
                request_id=f"digest_{date}",
                operation="digest_generation",
                temperature=0.3,  # Lower temperature for more consistent output
            )

            # Log generation time
            generation_time = (datetime.utcnow() - start_time).total_seconds()
            print(
                f"Generated digest for {date} in {generation_time:.2f} seconds",
                file=sys.stderr,
            )

            return message.content[0].text if message and message.content else ""

        except APIConnectionError:
            raise HTTPException(
                status_code=503, detail="Failed to connect to Anthropic API"
            )
        except RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except APIStatusError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Digest generation failed: {str(e)}"
            )

    async def _save_digest(
        self, content: str, date: str, batch_mode: bool = False
    ) -> str:
        """Save generated digest to file and process metadata."""
        try:
            # Create digest path in digests directory with hyphen
            digest_path = f"{DIGEST_FOLDER}/digest-{date}.md"
            full_path = os.path.join(git_ops.repo_path, digest_path)

            # Create directories if they don't exist (including digests folder)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # Clean the content to remove acknowledgment text before frontmatter
            cleaned_content = self._clean_digest_content(content)
            print(f"Generated digest content (length: {len(content)})", file=sys.stderr)
            print(
                f"Cleaned digest content (length: {len(cleaned_content)})",
                file=sys.stderr,
            )

            # Write digest file with cleaned content
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(cleaned_content)

            # Only commit if not in batch mode
            if not batch_mode:
                try:
                    # Stage and commit the digest
                    parsed_date = datetime.strptime(date, "%Y%m%d")
                    formatted_date = parsed_date.strftime("%Y-%m-%d")
                    await git_ops.commit_and_push(
                        [digest_path],
                        f"{settings.BOT_COMMIT_PREFIX} Add knowledge digest for {formatted_date}",
                    )
                except Exception as commit_error:
                    print(
                        f"Error committing digest: {str(commit_error)}", file=sys.stderr
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to commit digest: {str(commit_error)}",
                    )

            # Process metadata for the digest file using direct function call
            try:
                # Import necessary functions
                from ..services.frontmatter import frontmatter
                from ..services.metadata import analyze_metadata

                # First, generate standard metadata
                await analyze_metadata(digest_path)
                print(f"Metadata generated for {digest_path}", file=sys.stderr)

                # Then update the created date to match the digest date
                digest_content = await git_ops.read_file(digest_path)
                parsed_date = datetime.strptime(date, "%Y%m%d")
                iso_date = parsed_date.replace(
                    hour=12, minute=0, second=0
                ).isoformat()  # Use noon for consistency

                # Extract existing metadata
                metadata = frontmatter.extract_all(digest_content)
                if metadata:
                    # Update created and modified dates
                    metadata["created"] = iso_date
                    metadata["modified"] = iso_date

                    # Update temporal_reasoning to reflect explicit date
                    formatted_date = parsed_date.strftime("%Y-%m-%d")
                    metadata["temporal_reasoning"] = (
                        f"Explicit digest date from filename: {formatted_date}"
                    )

                    # Update document with new dates
                    updated_content = frontmatter.update(digest_content, metadata)
                    full_path = os.path.join(git_ops.repo_path, digest_path)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(updated_content)

                    print(f"Updated digest dates to {iso_date}", file=sys.stderr)

            except Exception as metadata_error:
                # Log metadata generation error without blocking
                print(
                    f"Error processing digest metadata: {str(metadata_error)}",
                    file=sys.stderr,
                )

            return digest_path

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to save digest: {str(e)}"
            )

    async def get_digest_for_date(self, date: str) -> File | None:
        """Get digest for a specific date, trying both naming formats and locations.

        Args:
            date: Date string in YYYYMMDD format

        Returns:
            File object if digest exists, None otherwise
        """
        # Try both formats and locations
        formats = [
            # New format in digests folder
            f"{DIGEST_FOLDER}/digest-{date}.md",
            # Old formats in root for backward compatibility
            f"digest-{date}.md",
            f"digest{date}.md",
        ]

        # Get all markdown files first to avoid file-not-found errors
        all_files = await git_ops.read_markdown_files()

        # Filter to find matching digest files
        for format in formats:
            matching_files = [f for f in all_files if f.path == format]
            if matching_files:
                return matching_files[0]

        return None

    def _clean_digest_content(self, content: str) -> str:
        """Clean digest content by removing acknowledgment text and ensuring single frontmatter.

        The LLM sometimes adds acknowledgment text like "I'll create a comprehensive daily digest..."
        before the frontmatter. This method ensures we start directly with the frontmatter.
        """
        # Strip any leading whitespace
        content = content.lstrip()

        # If content already starts with frontmatter delimiter, return as is
        if content.startswith("---\n"):
            return content

        # Find the first frontmatter delimiter
        frontmatter_start = content.find("---\n")
        if frontmatter_start != -1:
            # Return only from the frontmatter start onwards
            return content[frontmatter_start:]

        # If no frontmatter found, return the original content
        return content
