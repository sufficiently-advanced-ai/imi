import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Union

import yaml
from pydantic import BaseModel

from .config import settings
from .models import DocumentMetadata, File
from .utils.timeout import timeout


class Folder(BaseModel):
    """Model for folder structure"""

    path: str
    name: str
    is_dir: bool = True
    children: list[Union["Folder", str]] | None = None


class GitOperationError(Exception):
    """Custom exception for git operations with structured error details"""

    def __init__(self, operation: str, message: str, details: dict[str, Any] = None):
        self.operation = operation
        self.message = message
        self.details = details or {}
        super().__init__(message)


class GitRevisionReadError(Exception):
    """Raised by read_file_at_revision / get_revision_before on operational failures
    (subprocess crash, timeout, unexpected non-zero exit) that are NOT a simple
    'object not found'.  Callers that want observable degradation should catch this
    and log a warning, then proceed with prev=None."""


class GitOperations:
    def __init__(self, repo_path: str | None = None):
        # Per-tenant corpus (Phase 4.4) passes an explicit repo_path; the
        # single-tenant default keeps the original relative ``<app>/../repo``.
        self.repo_path = repo_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "repo"
        )
        self.git_lock = asyncio.Lock()  # Lock for git operations
        # Cache for read_markdown_files() results (the "read all" branch)
        self._markdown_files_cache: list | None = None
        self._markdown_files_cache_time: float = 0
        self._markdown_files_cache_ttl: float = 60.0  # seconds
        # Cache for _get_file_creation_timestamps. The git history walk is
        # expensive (full log scan), and creation timestamps only change when
        # new files are committed — safe to cache for several minutes.
        self._creation_ts_cache: dict[str, datetime] | None = None
        self._creation_ts_cache_time: float = 0
        self._creation_ts_cache_ttl: float = 300.0  # 5 minutes

    def invalidate_markdown_files_cache(self) -> None:
        """Clear the cached markdown files list."""
        self._markdown_files_cache = None
        self._markdown_files_cache_time = 0
        # New file commits invalidate creation timestamps too
        self._creation_ts_cache = None
        self._creation_ts_cache_time = 0

    def _get_file_creation_timestamps(self) -> dict[str, datetime]:
        """Batch-fetch creation timestamps for all files via a single git log call.

        Runs `git log --all --diff-filter=A --reverse` to find the earliest commit
        that added each file. Returns a dict mapping relative_path -> created_at.
        Falls back to empty dict on failure (callers use filesystem fallback).

        Result is cached for ``_creation_ts_cache_ttl`` seconds. The full-history
        scan is bounded by repo activity (can be 1-5s on a busy repo); without
        the cache, every read_markdown_files(paths=[...]) call would pay it.
        """
        now = time.time()
        if (
            self._creation_ts_cache is not None
            and (now - self._creation_ts_cache_time) < self._creation_ts_cache_ttl
        ):
            return self._creation_ts_cache

        try:
            git_binary = shutil.which("git")
            if not git_binary:
                raise FileNotFoundError("git binary not found in PATH")
            result = subprocess.run(
                [
                    git_binary,
                    "log",
                    "--all",
                    "--diff-filter=A",
                    "--format=%ct",
                    "--name-only",
                    "--reverse",
                ],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            timestamps: dict[str, datetime] = {}
            current_ts: int | None = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.isdigit():
                    current_ts = int(line)
                elif current_ts is not None and line not in timestamps:
                    # First appearance (--reverse) = earliest commit = file creation
                    timestamps[line] = datetime.fromtimestamp(current_ts)
            self._creation_ts_cache = timestamps
            self._creation_ts_cache_time = now
            return timestamps
        except Exception as e:
            self._log_operation(
                "get_file_creation_timestamps",
                {"status": "failed", "reason": str(e)},
            )
            return {}

    @timeout(seconds=30)  # 30 second timeout for git commands
    def _run_git_command(
        self, cmd: list[str], check: bool = True, **kwargs
    ) -> subprocess.CompletedProcess:
        """Run a git command with timeout protection."""
        return subprocess.run(cmd, cwd=self.repo_path, check=check, **kwargs)

    async def ensure_clean_working_directory(
        self, operation: str = "generic_operation"
    ) -> bool:
        """Ensure the working directory is clean before git operations."""
        try:
            # Check if repo exists
            if not os.path.isdir(os.path.join(self.repo_path, ".git")):
                self._log_operation(
                    operation,
                    {
                        "stage": "clean_check",
                        "status": "failed",
                        "message": "No repo to clean, which is both a problem and not a problem",
                    },
                )
                return False

            # Check if repo is dirty
            status_output = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

            if status_output:
                # Directory is dirty, try to stash changes
                self._log_operation(
                    operation,
                    {
                        "stage": "clean_check",
                        "status": "dirty",
                        "message": "Working directory has uncommitted changes",
                    },
                )

                try:
                    # Try to stash changes
                    subprocess.run(
                        [
                            "git",
                            "stash",
                            "save",
                            f"Auto-stash before {operation} {datetime.utcnow().isoformat()}",
                        ],
                        cwd=self.repo_path,
                        check=True,
                    )

                    self._log_operation(
                        operation,
                        {
                            "stage": "clean_check",
                            "action": "stash",
                            "status": "success",
                            "message": "Tucked away those messy changes for later",
                        },
                    )
                except Exception:
                    # If stash fails, go nuclear with reset
                    subprocess.run(
                        ["git", "reset", "--hard", "HEAD"],
                        cwd=self.repo_path,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "clean", "-fd"], cwd=self.repo_path, check=True
                    )

                    self._log_operation(
                        operation,
                        {
                            "stage": "clean_check",
                            "action": "hard_reset",
                            "status": "success",
                            "message": "Changes were beyond saving, so we went nuclear",
                        },
                    )

            return True

        except Exception as e:
            self._log_operation(
                operation,
                {
                    "stage": "clean_check",
                    "status": "failed",
                    "message": "Failed to clean working directory, chaos reigns",
                },
                e,
            )
            return False

    def _log_operation(
        self, operation: str, details: dict[str, Any], error: Exception | None = None
    ) -> None:
        """Log git operations to stderr with structured format."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "operation": operation,
            "status": "error" if error else "success",
            "details": details,
        }
        if error:
            log_entry["error"] = str(error)
        print(json.dumps(log_entry), file=sys.stderr)

    def _is_markdown_file(self, path: str) -> bool:
        """Check if a file is a markdown file."""
        return path.lower().endswith(".md")

    def _get_modified_markdown_files(
        self, added: list[str], modified: list[str]
    ) -> set[str]:
        """Get set of markdown files that were added or modified."""
        all_changes = set(added) | set(modified)
        return {path for path in all_changes if self._is_markdown_file(path)}

    async def pull_changes(self) -> None:
        """Pull latest changes from remote repository with attitude."""
        operation = "pull_changes"

        try:
            self._log_operation(
                operation,
                {
                    "branch": settings.GIT_BRANCH,
                    "status": "starting",
                    "message": "Taking control of this situation",
                },
            )

            # Check if repo exists and is initialized
            if not os.path.isdir(os.path.join(self.repo_path, ".git")):
                error = GitOperationError(operation, "No git repository found")
                self._log_operation(operation, {"status": "failed"}, error)
                raise error

            # Stash any local changes first
            stashed = False

            # Check for uncommitted changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            status = result.stdout.strip()

            if status:
                try:
                    # Try to stash changes
                    subprocess.run(
                        [
                            "git",
                            "stash",
                            "save",
                            f"Auto-stash before pull {datetime.utcnow().isoformat()}",
                        ],
                        cwd=self.repo_path,
                        check=True,
                    )
                    stashed = True
                except Exception:
                    # If stash fails, go nuclear with reset
                    subprocess.run(
                        ["git", "reset", "--hard", "HEAD"],
                        cwd=self.repo_path,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "clean", "-fd"], cwd=self.repo_path, check=True
                    )

            # Pull those changes like we mean it
            subprocess.run(
                ["git", "pull", "origin", settings.GIT_BRANCH, "--force"],
                cwd=self.repo_path,
                check=True,
                timeout=300,  # Use a longer timeout for pull operations
            )

            # Try to recover stashed changes, but we won't cry about it
            if stashed:
                try:
                    subprocess.run(
                        ["git", "stash", "pop"], cwd=self.repo_path, check=True
                    )
                except Exception:
                    self._log_operation(
                        operation,
                        {
                            "stage": "stash_pop",
                            "status": "failed",
                            "message": "Stash pop failed but moving on with life",
                        },
                    )

            self._log_operation(
                operation,
                {
                    "branch": settings.GIT_BRANCH,
                    "status": "completed",
                    "message": "Showed those changes who is boss",
                },
            )

        except Exception as e:
            error = GitOperationError(operation, f"Git really tried it: {str(e)}")
            self._log_operation(
                operation,
                {
                    "branch": settings.GIT_BRANCH,
                    "status": "failed",
                    "message": "Git threw attitude, but we handled it",
                },
                error,
            )
            raise error

    async def commit_and_push(
        self, files: list[str], message: str, force_push: bool = False
    ) -> None:
        """Commit and push changes with optimized git operations."""
        operation = "commit_and_push"

        try:
            # Start performance tracking
            start_time = datetime.utcnow()

            self._log_operation(
                operation,
                {
                    "files": files,
                    "message": message,
                    "force_push": force_push,
                    "status": "starting",
                },
            )

            # Verify repo exists
            if not os.path.isdir(os.path.join(self.repo_path, ".git")):
                error = GitOperationError(
                    operation, "Cannot push to a repo that does not exist"
                )
                self._log_operation(operation, {"status": "failed"}, error)
                raise error

            # Add files individually
            for file in files:
                file_path = os.path.join(self.repo_path, file)
                if os.path.exists(file_path):
                    # Use git command directly
                    import subprocess

                    subprocess.run(["git", "add", file], cwd=self.repo_path, check=True)

            # Check if there are changes to commit
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            status = result.stdout.strip()

            if status:
                # Use standard commit approach
                subprocess.run(
                    ["git", "commit", "-m", message], cwd=self.repo_path, check=True
                )

                # Get the commit hash
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                commit_hash = result.stdout.strip()

                # Calculate operation time for add+commit
                add_commit_time = (datetime.utcnow() - start_time).total_seconds()

                self._log_operation(
                    operation,
                    {
                        "stage": "commit",
                        "commit_hash": commit_hash,
                        "status": "completed",
                        "duration_seconds": add_commit_time,
                    },
                )

                # Push changes (skipped in local-only mode — no remote exists;
                # the local commit above is the durable copy)
                if self.local_only:
                    self._log_operation(
                        operation,
                        {
                            "stage": "push",
                            "status": "skipped",
                            "reason": "local-only corpus",
                        },
                    )
                    return

                push_command = ["git", "push", "origin", settings.GIT_BRANCH]
                if force_push:
                    push_command.append("--force")

                try:
                    push_start = datetime.utcnow()
                    subprocess.run(
                        push_command, cwd=self.repo_path, check=True, timeout=300
                    )  # 5 minute timeout for push
                    push_time = (datetime.utcnow() - push_start).total_seconds()

                    self._log_operation(
                        operation,
                        {
                            "stage": "push",
                            "branch": settings.GIT_BRANCH,
                            "force": force_push,
                            "status": "completed",
                            "duration_seconds": push_time,
                        },
                    )
                except Exception as push_error:
                    self._log_operation(
                        operation,
                        {
                            "stage": "push",
                            "status": "failed",
                            "error": str(push_error),
                            "recovery": "attempting pull --rebase then retry"
                            if not force_push
                            else "no recovery option",
                        },
                    )

                    # If regular push fails and force_push wasn't already true,
                    # pull with rebase to incorporate remote changes, then retry push.
                    # NEVER force push — in multi-instance environments, a rejected push
                    # means another instance wrote first and force-pushing destroys their work.
                    if not force_push:
                        try:
                            self._log_operation(
                                operation,
                                {
                                    "stage": "push_recovery",
                                    "status": "starting",
                                    "message": "Attempting recovery with pull --rebase",
                                },
                            )

                            recovery_start = datetime.utcnow()

                            # Pull with rebase to incorporate remote changes
                            rebase_result = subprocess.run(
                                [
                                    "git",
                                    "pull",
                                    "--rebase",
                                    "origin",
                                    settings.GIT_BRANCH,
                                ],
                                cwd=self.repo_path,
                                capture_output=True,
                                text=True,
                                timeout=300,
                            )

                            if rebase_result.returncode != 0:
                                # Rebase failed (likely conflict) — abort and raise
                                subprocess.run(
                                    ["git", "rebase", "--abort"],
                                    cwd=self.repo_path,
                                    capture_output=True,
                                    timeout=30,
                                )
                                raise GitOperationError(
                                    operation,
                                    f"Pull rebase recovery failed: {rebase_result.stderr.strip()}",
                                )

                            # Rebase succeeded — retry push
                            subprocess.run(
                                [
                                    "git",
                                    "push",
                                    "origin",
                                    settings.GIT_BRANCH,
                                ],
                                cwd=self.repo_path,
                                check=True,
                                timeout=300,
                            )

                            recovery_time = (
                                datetime.utcnow() - recovery_start
                            ).total_seconds()

                            self._log_operation(
                                operation,
                                {
                                    "stage": "push_recovery",
                                    "status": "completed",
                                    "message": "Recovered with pull --rebase",
                                    "duration_seconds": recovery_time,
                                },
                            )
                        except GitOperationError:
                            raise
                        except Exception as recovery_error:
                            self._log_operation(
                                operation,
                                {
                                    "stage": "push_recovery",
                                    "status": "failed",
                                    "error": str(recovery_error),
                                },
                            )
                            raise GitOperationError(
                                operation,
                                f"Pull rebase recovery failed: {str(recovery_error)}",
                            )
                    else:
                        # No recovery option available if force push already failed
                        raise GitOperationError(
                            operation, f"Force push failed: {str(push_error)}"
                        )
            else:
                self._log_operation(
                    operation,
                    {
                        "stage": "commit",
                        "status": "skipped",
                        "message": "No changes to commit - working tree clean",
                    },
                )

        except Exception as e:
            if not isinstance(e, GitOperationError):
                e = GitOperationError(operation, f"Git command failed: {str(e)}")
            self._log_operation(
                operation,
                {
                    "files": files,
                    "message": message,
                    "status": "failed",
                    "error": str(e),
                },
                e,
            )
            raise e

    @property
    def local_only(self) -> bool:
        """True when no git remote is configured (community/local-corpus mode).

        With no GIT_REPO_URL the corpus lives only in the local working dir:
        initialize() must not wipe it, and commits must not attempt a push.
        """
        return not (settings.GIT_REPO_URL or "").strip()

    async def initialize(self) -> None:
        """Initialize the corpus repo: clone the remote, or init locally.

        Remote mode (GIT_REPO_URL set): fresh clone, remote is authoritative.
        Local-only mode: `git init` once and PRESERVE existing contents across
        restarts — the local directory is the only copy of the corpus.
        """
        operation = "initialize"
        try:
            if self.local_only:
                os.makedirs(self.repo_path, exist_ok=True)
                if not os.path.isdir(os.path.join(self.repo_path, ".git")):
                    subprocess.run(
                        ["git", "init", "-b", settings.GIT_BRANCH],
                        cwd=self.repo_path,
                        check=True,
                        timeout=60,
                    )
                self._log_operation(
                    operation,
                    {
                        "action": "init-local",
                        "path": self.repo_path,
                        "status": "completed",
                    },
                )
            else:
                # Set up auth if needed
                repo_url = settings.GIT_REPO_URL
                if settings.GITHUB_TOKEN:
                    auth_url = f"https://{settings.GITHUB_TOKEN}@github.com/"
                    repo_url = settings.GIT_REPO_URL.replace(
                        "https://github.com/", auth_url
                    )

                # Always clean up existing directory
                if os.path.exists(self.repo_path):
                    # Clean up all contents including .git
                    shutil.rmtree(self.repo_path, ignore_errors=True)

                # Create directory if it doesn't exist
                os.makedirs(self.repo_path, exist_ok=True)

                self._log_operation(
                    operation,
                    {
                        "action": "cleanup",
                        "path": self.repo_path,
                        "status": "completed",
                    },
                )

                # Clone into existing directory
                subprocess.run(
                    ["git", "clone", repo_url, ".", "--branch", settings.GIT_BRANCH],
                    cwd=self.repo_path,
                    check=True,
                    timeout=300,  # Cloning can take longer, especially for large repos
                )

            # Mark directory as safe (fixes dubious ownership issues in containers)
            subprocess.run(
                [
                    "git",
                    "config",
                    "--global",
                    "--add",
                    "safe.directory",
                    self.repo_path,
                ],
                check=True,
            )

            # Configure Git identity
            subprocess.run(
                [
                    "git",
                    "config",
                    "user.email",
                    settings.GIT_USER_EMAIL or "imi-bot@example.com",
                ],
                cwd=self.repo_path,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", settings.GIT_USER_NAME or "imi Bot"],
                cwd=self.repo_path,
                check=True,
            )

            self._log_operation(
                operation,
                {
                    "action": "clone",
                    "branch": settings.GIT_BRANCH,
                    "status": "completed",
                },
            )

        except Exception as e:
            error = GitOperationError(operation, f"Git operation failed: {str(e)}")
            self._log_operation(operation, {"status": "failed"}, error)
            raise error

    async def _commit_exists(self, commit_hash: str) -> bool:
        """Check if a commit exists in the repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"{commit_hash}^{{commit}}"],
                cwd=self.repo_path,
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def get_changed_files_between_commits(
        self, before_commit: str, after_commit: str
    ) -> list[str]:
        """Get list of changed files between two commits using git diff-tree.

        Handles special cases:
        - Initial repository state (before_commit is all zeros)
        - Missing commit objects
        - Empty repositories
        """
        operation = "get_changed_files_between_commits"

        try:
            self._log_operation(
                operation,
                {"before": before_commit, "after": after_commit, "status": "starting"},
            )

            # Handle initial commit case
            if before_commit == "0" * 40:
                self._log_operation(
                    operation,
                    {
                        "status": "initial_commit_detected",
                        "message": "Processing initial repository commit",
                    },
                )

                try:
                    # For initial commit, get all files in the tree
                    result = subprocess.run(
                        ["git", "ls-tree", "-r", "--name-only", after_commit],
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    changed_files = [
                        line.strip()
                        for line in result.stdout.splitlines()
                        if line.strip()
                    ]

                    self._log_operation(
                        operation,
                        {
                            "files": changed_files,
                            "count": len(changed_files),
                            "status": "completed",
                            "method": "ls_tree",
                        },
                    )
                    return changed_files
                except Exception as e:
                    self._log_operation(
                        operation,
                        {
                            "status": "tree_traversal_failed",
                            "error": str(e),
                            "fallback": "empty_list",
                        },
                    )
                    return []

            # Validate commit existence
            if not await self._commit_exists(before_commit):
                self._log_operation(
                    operation,
                    {
                        "status": "commit_not_found",
                        "commit": before_commit,
                        "message": "Before commit not found in repository",
                    },
                )
                raise GitOperationError(
                    operation,
                    f"Before commit {before_commit} not found in repository",
                    {"commit": before_commit, "type": "before_commit_missing"},
                )

            if not await self._commit_exists(after_commit):
                self._log_operation(
                    operation,
                    {
                        "status": "commit_not_found",
                        "commit": after_commit,
                        "message": "After commit not found in repository",
                    },
                )
                raise GitOperationError(
                    operation,
                    f"After commit {after_commit} not found in repository",
                    {"commit": after_commit, "type": "after_commit_missing"},
                )

            # Use git diff-tree to get changed files
            result = subprocess.run(
                [
                    "git",
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    before_commit,
                    after_commit,
                ],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            # Split output into list of files
            changed_files = [f for f in result.stdout.splitlines() if f]

            self._log_operation(
                operation,
                {
                    "files": changed_files,
                    "count": len(changed_files),
                    "status": "completed",
                    "method": "diff_tree",
                },
            )

            return changed_files

        except Exception as e:
            error = GitOperationError(
                operation, f"Failed to get changed files: {str(e)}"
            )
            self._log_operation(
                operation,
                {"before": before_commit, "after": after_commit, "status": "failed"},
                error,
            )
            raise error

    async def get_status(self) -> str:
        """Check git repository status."""
        operation = "get_status"

        try:
            # Check if repo directory and .git directory exist
            if not (
                os.path.exists(self.repo_path)
                and os.path.isdir(os.path.join(self.repo_path, ".git"))
            ):
                status = "disconnected"
                self._log_operation(
                    operation, {"status": status, "repo_initialized": False}
                )
                return status

            # Check if repo is bare
            config_result = subprocess.run(
                ["git", "config", "--get", "core.bare"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            is_bare = config_result.stdout.strip().lower() == "true"

            # Get current branch and head commit
            branch = "unknown"
            head_commit = "unknown"

            if not is_bare:
                try:
                    branch_result = subprocess.run(
                        ["git", "symbolic-ref", "--short", "HEAD"],
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if branch_result.returncode == 0:
                        branch = branch_result.stdout.strip()

                    commit_result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if commit_result.returncode == 0:
                        head_commit = commit_result.stdout.strip()
                except Exception:
                    pass

            status = "connected" if not is_bare else "bare"
            self._log_operation(
                operation,
                {
                    "status": status,
                    "repo_initialized": True,
                    "is_bare": is_bare,
                    "current_branch": branch if not is_bare else None,
                    "head_commit": head_commit if not is_bare else None,
                },
            )
            return status

        except Exception as e:
            status = "error"
            self._log_operation(operation, {"status": status}, e)
            return status

    def _extract_frontmatter(self, content: str) -> tuple[dict | None, str]:
        """Extract YAML frontmatter from markdown content."""
        pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = pattern.match(content)

        if not match:
            return None, content

        try:
            frontmatter = yaml.safe_load(match.group(1))
            remaining_content = content[match.end() :]
            return frontmatter, remaining_content
        except yaml.YAMLError:
            return None, content

    def _update_frontmatter(self, content: str, metadata: dict) -> str:
        """Update or add frontmatter to markdown content."""
        # Extract existing frontmatter to preserve necessary fields
        existing_frontmatter, _ = self._extract_frontmatter(content)

        if existing_frontmatter:
            # Always update modified date to current time for updates
            metadata["modified"] = datetime.utcnow().isoformat()

            # If no created date specified in update, preserve existing created date
            if "created" not in metadata and "created" in existing_frontmatter:
                metadata["created"] = existing_frontmatter["created"]

            # Preserve other existing frontmatter fields that aren't being updated
            for key, value in existing_frontmatter.items():
                if key not in metadata:
                    metadata[key] = value

        yaml_str = yaml.dump(metadata, default_flow_style=False)

        # Remove existing frontmatter if present
        content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

        return f"---\n{yaml_str}---\n{content}"

    async def get_document_metadata(self, path: str) -> DocumentMetadata | None:
        """Get metadata for a specific document."""
        operation = "get_document_metadata"
        try:
            self._log_operation(operation, {"path": path, "status": "starting"})

            files = await self.read_markdown_files([path])
            if not files:
                self._log_operation(
                    operation,
                    {"path": path, "status": "failed", "reason": "file_not_found"},
                )
                return None

            content = files[0].content
            frontmatter, _ = self._extract_frontmatter(content)

            if not frontmatter:
                self._log_operation(
                    operation,
                    {"path": path, "status": "failed", "reason": "no_frontmatter"},
                )
                return None

            metadata = DocumentMetadata(**frontmatter)
            self._log_operation(
                operation,
                {
                    "path": path,
                    "metadata_keys": list(frontmatter.keys()),
                    "status": "success",
                },
            )
            return metadata

        except Exception as e:
            self._log_operation(operation, {"path": path, "status": "error"}, e)
            return None

    async def update_document_metadata(self, path: str, metadata: dict) -> bool:
        """Update metadata for a specific document."""
        operation = "update_document_metadata"
        try:
            files = await self.read_markdown_files([path])
            if not files:
                return False

            file = files[0]

            # Create new frontmatter
            yaml_str = yaml.dump(metadata, default_flow_style=False)
            new_content = f"---\n{yaml_str}---\n\n"

            # Append original content without frontmatter
            _, content = self._extract_frontmatter(file.content)
            new_content += content

            # Write updated file
            full_path = os.path.join(self.repo_path, path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return True

        except Exception as e:
            self._log_operation(operation, {"path": path, "status": "failed"}, e)
            return False

    async def read_file(self, path: str) -> str:
        """Read a file from the git repository."""
        operation = "read_file"
        try:
            self._log_operation(operation, {"path": path, "status": "starting"})

            full_path = os.path.join(self.repo_path, path)
            if not os.path.exists(full_path):
                error = GitOperationError(operation, f"File not found: {path}")
                self._log_operation(
                    operation,
                    {"path": path, "status": "failed", "reason": "file_not_found"},
                    error,
                )
                raise error

            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            self._log_operation(
                operation, {"path": path, "size": len(content), "status": "completed"}
            )

            return content

        except Exception as e:
            error = GitOperationError(operation, f"Failed to read file: {str(e)}")
            self._log_operation(operation, {"path": path, "status": "failed"}, error)
            raise error

    async def read_file_at_revision(self, path: str, revision: str) -> str | None:
        """Content of *path* at *revision* (e.g. a sha or 'HEAD~1'); None if absent/invalid.

        Returns:
            File content string, or None when the path genuinely does not exist
            at that revision (git says 'does not exist', 'invalid object name', etc.).

        Raises:
            GitRevisionReadError: On operational failures (timeout, subprocess crash,
                unexpected non-zero exit unrelated to missing objects).  Callers
                should catch this and degrade gracefully (log warning, use None).
        """
        operation = "read_file_at_revision"
        # --- Input validation ---
        if not path or not path.strip():
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "[%s] empty path supplied; returning None", operation
            )
            return None
        if not revision or not revision.strip():
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "[%s] empty revision supplied; returning None", operation
            )
            return None

        try:
            self._log_operation(
                operation, {"path": path, "revision": revision, "status": "starting"}
            )
            result = subprocess.run(
                ["git", "show", f"{revision}:{path}"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                # True miss: git says the object/path doesn't exist
                _MISS_PATTERNS = (
                    "does not exist",
                    "exists on disk, but not in",
                    "invalid object name",
                    "unknown revision",
                    "bad revision",
                )
                if any(pat in stderr for pat in _MISS_PATTERNS):
                    self._log_operation(
                        operation,
                        {
                            "path": path,
                            "revision": revision,
                            "status": "not_found",
                            "stderr": stderr,
                        },
                    )
                    return None
                # Operational failure
                self._log_operation(
                    operation,
                    {
                        "path": path,
                        "revision": revision,
                        "status": "error",
                        "stderr": stderr,
                    },
                )
                raise GitRevisionReadError(
                    f"git show failed (rc={result.returncode}): {stderr}"
                )
            self._log_operation(
                operation,
                {
                    "path": path,
                    "revision": revision,
                    "size": len(result.stdout),
                    "status": "completed",
                },
            )
            return result.stdout
        except subprocess.TimeoutExpired as e:
            self._log_operation(
                operation,
                {"path": path, "revision": revision, "status": "timeout"},
                e,
            )
            raise GitRevisionReadError(
                f"git show timed out for {revision}:{path}"
            ) from e
        except GitRevisionReadError:
            raise
        except Exception as e:
            self._log_operation(
                operation, {"path": path, "revision": revision, "status": "error"}, e
            )
            raise GitRevisionReadError(
                f"Unexpected error in read_file_at_revision: {e}"
            ) from e

    async def get_revision_before(
        self, timestamp: str, path: str | None = None
    ) -> str | None:
        """Most recent commit sha at/before *timestamp* (optionally touching *path*); None if none.

        Returns:
            Commit SHA string, or None when no commits exist before *timestamp*
            (empty stdout — a true 'no result').

        Raises:
            GitRevisionReadError: On operational failures (timeout, subprocess crash,
                unexpected non-zero exit unrelated to an empty history window).
        """
        operation = "get_revision_before"
        # --- Input validation ---
        if not timestamp or not timestamp.strip():
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "[%s] empty timestamp supplied; returning None", operation
            )
            return None

        try:
            self._log_operation(
                operation,
                {"timestamp": timestamp, "path": path, "status": "starting"},
            )
            cmd = ["git", "rev-list", "-1", f"--before={timestamp}", "HEAD"]
            if path:
                cmd += ["--", path]
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            sha = result.stdout.strip()
            if result.returncode != 0:
                stderr = result.stderr.strip()
                _MISS_PATTERNS = (
                    "unknown revision",
                    "bad revision",
                    "does not exist",
                    "not a git repository",
                )
                if any(pat in stderr for pat in _MISS_PATTERNS):
                    self._log_operation(
                        operation,
                        {
                            "timestamp": timestamp,
                            "path": path,
                            "status": "not_found",
                            "stderr": stderr,
                        },
                    )
                    return None
                # Operational failure
                self._log_operation(
                    operation,
                    {
                        "timestamp": timestamp,
                        "path": path,
                        "status": "error",
                        "stderr": stderr,
                    },
                )
                raise GitRevisionReadError(
                    f"git rev-list failed (rc={result.returncode}): {stderr}"
                )
            # Empty stdout = no commits before timestamp (true miss)
            if not sha:
                self._log_operation(
                    operation,
                    {
                        "timestamp": timestamp,
                        "path": path,
                        "status": "not_found",
                        "stderr": "",
                    },
                )
                return None
            self._log_operation(
                operation,
                {
                    "timestamp": timestamp,
                    "path": path,
                    "sha": sha,
                    "status": "completed",
                },
            )
            return sha
        except subprocess.TimeoutExpired as e:
            self._log_operation(
                operation,
                {"timestamp": timestamp, "path": path, "status": "timeout"},
                e,
            )
            raise GitRevisionReadError(
                f"git rev-list timed out for timestamp={timestamp}"
            ) from e
        except GitRevisionReadError:
            raise
        except Exception as e:
            self._log_operation(
                operation,
                {"timestamp": timestamp, "path": path, "status": "error"},
                e,
            )
            raise GitRevisionReadError(
                f"Unexpected error in get_revision_before: {e}"
            ) from e

    async def commit_file(
        self, file_path: str, content: str, commit_message: str
    ) -> None:
        """Write content to a file and commit it to git."""
        operation = "commit_file"
        try:
            self._log_operation(
                operation,
                {
                    "file_path": file_path,
                    "commit_message": commit_message,
                    "status": "starting",
                },
            )

            # Write file to filesystem
            full_path = os.path.join(self.repo_path, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Commit the file
            await self.commit_and_push([file_path], commit_message)

            self._log_operation(
                operation,
                {"file_path": file_path, "size": len(content), "status": "completed"},
            )

        except Exception as e:
            error = GitOperationError(operation, f"Failed to commit file: {e!s}")
            self._log_operation(
                operation, {"file_path": file_path, "status": "failed"}, error
            )
            raise error from e

    async def read_markdown_files(
        self,
        paths: list[str] | None = None,
        folder: str | None = None,
        treat_missing_as_error: bool = False,
    ) -> list[File]:
        """Read markdown files from the repository.

        Args:
            paths: Optional list of specific file paths to read
            folder: Optional folder path to filter files by
            treat_missing_as_error: If True, log errors for missing files; if False, silently skip them
        """
        operation = "read_markdown_files"
        files = []

        def is_markdown(path: str) -> bool:
            return path.lower().endswith(".md")

        try:
            self._log_operation(
                operation, {"paths": paths if paths else "all", "status": "starting"}
            )

            # If specific paths are provided, read only those files. Batch
            # the git-history lookup into a single `git log` call up front
            # rather than spawning one subprocess per file (which previously
            # cost 50-200ms per path — devastating for any caller passing a
            # list of more than a couple of files).
            if paths:
                creation_timestamps = self._get_file_creation_timestamps()
                for path in paths:
                    if not is_markdown(path):
                        self._log_operation(
                            operation,
                            {"path": path, "skipped": True, "reason": "not_markdown"},
                        )
                        continue

                    full_path = os.path.join(self.repo_path, path)
                    if os.path.exists(full_path):
                        # Prefer the batched git creation time; fall back to
                        # filesystem ctime if the batch didn't include this
                        # path (e.g. file is brand new and not yet committed).
                        created_at = creation_timestamps.get(path)
                        if created_at is None:
                            try:
                                created_at = datetime.fromtimestamp(
                                    os.path.getctime(full_path)
                                )
                            except OSError:
                                # Use local time to match datetime.fromtimestamp()
                                # above; mixing utcnow() here would shift the
                                # fallback by the host's UTC offset.
                                created_at = datetime.now()

                        try:
                            modified_at = datetime.fromtimestamp(
                                os.path.getmtime(full_path)
                            )
                        except OSError:
                            modified_at = datetime.now()

                        with open(full_path, encoding="utf-8") as f:
                            content = f.read()
                            files.append(
                                File(
                                    path=path,
                                    content=content,
                                    created_at=created_at,
                                    modified_at=modified_at,
                                )
                            )
                            self._log_operation(
                                operation,
                                {
                                    "path": path,
                                    "status": "read_success",
                                    "size_bytes": len(content),
                                },
                            )
                    else:
                        # Only log as error if treat_missing_as_error is True
                        self._log_operation(
                            operation,
                            {
                                "path": path,
                                "status": "read_failed"
                                if treat_missing_as_error
                                else "skipped",
                                "reason": "file_not_found",
                            },
                        )
            else:
                # Read all markdown files in the repository, optionally filtered by folder

                # Return cached result if available and not expired (skip cache for folder-filtered requests)
                if (
                    not folder
                    and self._markdown_files_cache is not None
                    and (time.time() - self._markdown_files_cache_time)
                    < self._markdown_files_cache_ttl
                ):
                    self._log_operation(
                        operation,
                        {
                            "status": "cache_hit",
                            "cached_count": len(self._markdown_files_cache),
                        },
                    )
                    return list(self._markdown_files_cache)

                base_path = self.repo_path
                if folder:
                    folder_path = os.path.join(self.repo_path, folder)
                    if not os.path.isdir(folder_path):
                        self._log_operation(
                            operation,
                            {
                                "folder": folder,
                                "status": "skipped",
                                "reason": "folder_not_found",
                            },
                        )
                        return files
                    base_path = folder_path

                # Batch-fetch all creation timestamps in a single git log call
                creation_timestamps = self._get_file_creation_timestamps()

                for root, _, filenames in os.walk(base_path):
                    for filename in filenames:
                        if not is_markdown(filename):
                            continue
                        full_path = os.path.join(root, filename)
                        relative_path = os.path.relpath(full_path, self.repo_path)
                        try:
                            modified_at = datetime.fromtimestamp(
                                os.path.getmtime(full_path)
                            )

                            # Look up creation time from batch result, fallback to filesystem
                            created_at = creation_timestamps.get(relative_path)
                            if created_at is None:
                                created_at = datetime.fromtimestamp(
                                    os.path.getctime(full_path)
                                )

                            with open(full_path, encoding="utf-8") as f:
                                content = f.read()
                                files.append(
                                    File(
                                        path=relative_path,
                                        content=content,
                                        created_at=created_at,
                                        modified_at=modified_at,
                                    )
                                )
                                self._log_operation(
                                    operation,
                                    {
                                        "path": relative_path,
                                        "status": "read_success",
                                        "size_bytes": len(content),
                                    },
                                )
                        except Exception as e:
                            self._log_operation(
                                operation,
                                {
                                    "path": relative_path,
                                    "status": "read_failed",
                                    "reason": str(e),
                                },
                            )

            self._log_operation(
                operation, {"total_files_read": len(files), "status": "completed"}
            )

            # Cache result for "read all" requests (no specific paths or folder)
            if not paths and not folder:
                self._markdown_files_cache = list(files)
                self._markdown_files_cache_time = time.time()
                self._log_operation(
                    operation,
                    {"status": "cache_store", "cached_count": len(files)},
                )

            return files

        except Exception as e:
            error = GitOperationError(operation, f"Failed to read files: {str(e)}")
            self._log_operation(
                operation,
                {"paths": paths if paths else "all", "status": "failed"},
                error,
            )
            raise error

    async def create_folder(self, folder_path: str) -> bool:
        """Create a new folder in the repository.

        Args:
            folder_path: Relative path of the folder to create

        Returns:
            bool: True if folder was created successfully, False otherwise
        """
        operation = "create_folder"
        try:
            self._log_operation(
                operation, {"folder_path": folder_path, "status": "starting"}
            )

            # Ensure the folder path doesn't contain invalid characters
            if any(c in folder_path for c in ["\0", ":", "?", "<", ">", "|", "*", '"']):
                self._log_operation(
                    operation,
                    {
                        "folder_path": folder_path,
                        "status": "failed",
                        "reason": "invalid_characters",
                    },
                )
                return False

            # Create the full path
            full_path = os.path.join(self.repo_path, folder_path)

            # Check if it already exists
            if os.path.exists(full_path):
                if os.path.isdir(full_path):
                    self._log_operation(
                        operation,
                        {
                            "folder_path": folder_path,
                            "status": "skipped",
                            "reason": "folder_already_exists",
                        },
                    )
                    return True
                else:
                    self._log_operation(
                        operation,
                        {
                            "folder_path": folder_path,
                            "status": "failed",
                            "reason": "path_exists_as_file",
                        },
                    )
                    return False

            # Create the folder
            os.makedirs(full_path, exist_ok=True)

            # Create a .gitkeep file to ensure the folder is tracked by git
            gitkeep_path = os.path.join(full_path, ".gitkeep")
            with open(gitkeep_path, "w") as f:
                f.write("")

            # Stage the .gitkeep file
            subprocess.run(
                ["git", "add", os.path.join(folder_path, ".gitkeep")],
                cwd=self.repo_path,
                check=True,
            )

            self._log_operation(
                operation, {"folder_path": folder_path, "status": "completed"}
            )
            return True

        except Exception as e:
            error = GitOperationError(operation, f"Failed to create folder: {str(e)}")
            self._log_operation(
                operation, {"folder_path": folder_path, "status": "failed"}, error
            )
            return False

    async def list_folders(self, base_path: str = "") -> list[Folder]:
        """List folders in the repository.

        Args:
            base_path: Optional base path to list folders from

        Returns:
            List[Folder]: List of folder objects representing the folder structure
        """
        operation = "list_folders"
        try:
            self._log_operation(
                operation, {"base_path": base_path, "status": "starting"}
            )

            # Create the full path
            full_base_path = os.path.join(self.repo_path, base_path)

            # Check if the base path exists
            if not os.path.exists(full_base_path) or not os.path.isdir(full_base_path):
                self._log_operation(
                    operation,
                    {
                        "base_path": base_path,
                        "status": "failed",
                        "reason": "path_not_found_or_not_dir",
                    },
                )
                return []

            # Create the root folder structure
            root_name = os.path.basename(full_base_path) if base_path else "root"
            root_folder = Folder(path=base_path, name=root_name, children=[])

            # Recursively build the folder structure
            for item in os.listdir(full_base_path):
                # Skip hidden files and folders
                if item.startswith("."):
                    continue

                item_path = os.path.join(full_base_path, item)
                relative_path = os.path.relpath(item_path, self.repo_path)

                if os.path.isdir(item_path):
                    # Add subdirectories
                    subfolder = Folder(path=relative_path, name=item, children=[])

                    # Count markdown files in this folder
                    markdown_count = 0
                    for _root, _, filenames in os.walk(item_path):
                        markdown_count += sum(
                            1 for f in filenames if f.lower().endswith(".md")
                        )

                    # Only include folders that have markdown files or subfolders
                    if markdown_count > 0 or any(
                        os.path.isdir(os.path.join(item_path, child))
                        for child in os.listdir(item_path)
                        if not child.startswith(".")
                    ):
                        root_folder.children.append(subfolder)
                elif item.lower().endswith(".md"):
                    # Include markdown files directly in this folder
                    root_folder.children.append(relative_path)

            # Sort children: folders first, then files, both alphabetically
            if root_folder.children:
                root_folder.children.sort(
                    key=lambda x: (
                        0 if isinstance(x, Folder) else 1,
                        x.name.lower() if isinstance(x, Folder) else x.lower(),
                    )
                )

            self._log_operation(
                operation,
                {
                    "base_path": base_path,
                    "folders_count": sum(
                        1 for c in root_folder.children if isinstance(c, Folder)
                    ),
                    "files_count": sum(
                        1 for c in root_folder.children if isinstance(c, str)
                    ),
                    "status": "completed",
                },
            )

            return [root_folder]

        except Exception as e:
            error = GitOperationError(operation, f"Failed to list folders: {str(e)}")
            self._log_operation(
                operation, {"base_path": base_path, "status": "failed"}, error
            )
            return []


# Tenant-scoped global (Phase 4.1). `git_ops` is a proxy that forwards to the
# current tenant's GitOperations (resolved at call time). In single-tenant mode
# this is the one default container's instance, so `from app.git_ops import
# git_ops; git_ops.<method>(...)` behaves exactly as before — no call site changes.
from app.core.tenancy.proxy import _ContainerProxy  # noqa: E402

git_ops = _ContainerProxy(lambda c: c.git_ops, "git_ops")
