import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from ..git_ops import git_ops
from ..models import File

logger = logging.getLogger(__name__)


class FileCache:
    """In-memory cache for file content with intelligent invalidation."""

    def __init__(self, max_size: int = 200, ttl: int = 300):
        self.cache: dict[
            str, tuple[File, datetime, str]
        ] = {}  # path -> (file, timestamp, hash)
        self.max_size = max_size  # Maximum number of files to cache
        self.ttl = ttl  # Time to live in seconds
        self.lock = asyncio.Lock()  # For thread safety
        self.access_counts: dict[str, int] = {}  # For LRU tracking

    async def get_file(self, path: str) -> File | None:
        """Get a file from cache or disk, with cache management.

        The lock is held only for cache bookkeeping (lookup, hit-validation
        prep, and final insert). The actual disk read runs WITHOUT the lock
        so concurrent reads of different files can proceed in parallel.
        Previously every read held the lock through the entire async I/O,
        serializing every cache miss across the whole process.
        """
        now = datetime.utcnow()

        # Phase 1: cache lookup (under lock — fast)
        async with self.lock:
            cached = self.cache.get(path)
            if cached is not None:
                self.access_counts[path] = self.access_counts.get(path, 0) + 1

        # Phase 2: validate cache hit (no lock needed — it's read-only on
        # the captured tuple, plus a stat() on disk).
        if cached is not None:
            file, timestamp, _ = cached
            if now - timestamp < timedelta(seconds=self.ttl):
                if await self._is_file_unchanged(path):
                    return file

        # Phase 3: cache miss — read from disk WITHOUT the lock so other
        # tasks can read other files concurrently.
        try:
            content = await git_ops.read_file(path)
        except Exception as e:
            logger.error(f"Error reading file {path}: {e}")
            return None

        if content is None:
            return None

        file_obj = File(path=path, content=content)
        file_hash = self._compute_hash(content)

        # Phase 4: insert + LRU eviction (under lock — fast)
        async with self.lock:
            self.cache[path] = (file_obj, now, file_hash)
            self.access_counts[path] = self.access_counts.get(path, 0) + 1
            await self._clean_cache_if_needed()

        return file_obj

    async def get_files(self, paths: list[str]) -> list[File]:
        """Get multiple files at once, using batch operations where possible."""
        result = []
        for path in paths:
            file = await self.get_file(path)
            if file:
                result.append(file)
        return result

    async def get_all_markdown_files(self) -> list[File]:
        """Get all markdown files, using cache where possible."""
        # First get list of all markdown files from git_ops (fast path operation)
        all_files = await git_ops.read_markdown_files()
        all_paths = [file.path for file in all_files]
        return await self.get_files(all_paths)

    async def invalidate(self, path: str) -> None:
        """Invalidate a specific file in the cache."""
        async with self.lock:
            if path in self.cache:
                del self.cache[path]
                if path in self.access_counts:
                    del self.access_counts[path]

    async def invalidate_multiple(self, paths: list[str]) -> None:
        """Invalidate multiple files in the cache."""
        async with self.lock:
            for path in paths:
                if path in self.cache:
                    del self.cache[path]
                    if path in self.access_counts:
                        del self.access_counts[path]

    async def clear(self) -> None:
        """Clear the entire cache."""
        async with self.lock:
            self.cache.clear()
            self.access_counts.clear()

    async def _clean_cache_if_needed(self) -> None:
        """Clean the cache if it's larger than max_size using LRU policy."""
        if len(self.cache) <= self.max_size:
            return

        # Sort paths by access count (least recently used first)
        sorted_paths = sorted(
            self.access_counts.keys(), key=lambda p: self.access_counts[p]
        )

        # Remove oldest entries until we're under max size
        to_remove = len(self.cache) - self.max_size
        for i in range(to_remove):
            if i < len(sorted_paths):
                path = sorted_paths[i]
                if path in self.cache:
                    del self.cache[path]
                if path in self.access_counts:
                    del self.access_counts[path]

    async def _is_file_unchanged(self, path: str) -> bool:
        """Check if file on disk matches cached version."""
        try:
            full_path = os.path.join(git_ops.repo_path, path)
            if not os.path.exists(full_path):
                return False

            # Check if modification time is newer than our cache
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
            cache_time = self.cache[path][1]

            # If file is newer than cache, it changed
            if mtime > cache_time:
                return False

            # For extra certainty, check hash if file was modified recently
            if datetime.utcnow() - mtime < timedelta(minutes=5):
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()
                new_hash = self._compute_hash(content)
                old_hash = self.cache[path][2]
                return new_hash == old_hash

            return True
        except Exception:
            # If any error occurs, assume file changed
            return False

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute a hash of file content."""
        return hashlib.md5(content.encode("utf-8")).hexdigest()


class FolderCache:
    """In-memory cache for folder contents with TTL-based invalidation."""

    def __init__(self, max_size: int = 50, ttl: int = 300):
        self.cache: dict[str, tuple[Any, datetime]] = {}  # key -> (data, timestamp)
        self.max_size = max_size  # Maximum number of folders to cache
        self.ttl = ttl  # Time to live in seconds
        self.lock = asyncio.Lock()  # For thread safety
        self.access_counts: dict[str, int] = {}  # For LRU tracking

    def get(self, key: str) -> Any | None:
        """Get folder data from cache if not expired."""
        now = datetime.utcnow()

        if key in self.cache:
            data, timestamp = self.cache[key]
            self.access_counts[key] = self.access_counts.get(key, 0) + 1

            # Check if still valid (not expired)
            if now - timestamp < timedelta(seconds=self.ttl):
                return data

        return None

    def set(self, key: str, data: Any) -> None:
        """Add folder data to cache with current timestamp."""
        now = datetime.utcnow()
        self.cache[key] = (data, now)
        self.access_counts[key] = self.access_counts.get(key, 0) + 1

        # Clean cache if needed
        self._clean_cache_if_needed()

    def invalidate(self, key: str) -> None:
        """Invalidate a specific folder in the cache."""
        if key in self.cache:
            del self.cache[key]
            if key in self.access_counts:
                del self.access_counts[key]

    def invalidate_by_prefix(self, prefix: str) -> None:
        """Invalidate all folders that start with the given prefix."""
        keys_to_remove = [k for k in self.cache.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            self.invalidate(key)

    def clear(self) -> None:
        """Clear the entire cache."""
        self.cache.clear()
        self.access_counts.clear()

    def _clean_cache_if_needed(self) -> None:
        """Clean the cache if it's larger than max_size using LRU policy."""
        if len(self.cache) <= self.max_size:
            return

        # Sort keys by access count (least recently used first)
        sorted_keys = sorted(
            self.access_counts.keys(), key=lambda p: self.access_counts[p]
        )

        # Remove oldest entries until we're under max size
        to_remove = len(self.cache) - self.max_size
        for i in range(to_remove):
            if i < len(sorted_keys):
                key = sorted_keys[i]
                if key in self.cache:
                    del self.cache[key]
                if key in self.access_counts:
                    del self.access_counts[key]


# Tenant-scoped globals (Phase 4.1). These proxies forward to the current
# tenant's caches (resolved at call time). In single-tenant mode each resolves
# to one container-owned instance, so existing imports behave exactly as before.
from app.core.tenancy.proxy import _ContainerProxy  # noqa: E402

file_cache = _ContainerProxy(lambda c: c.file_cache, "file_cache")
folder_cache = _ContainerProxy(lambda c: c.folder_cache, "folder_cache")
