"""Tests for GitOperations.read_file_at_revision and get_revision_before.

Sprint 3, Task S3-2: git file-at-revision reads (weekly digest substrate).

These tests build a REAL throwaway git repo in tmp_path so that actual git
subprocess calls are exercised — no mocking of git itself.

CodeRabbit hardening (PR #964):
- Input validation (empty path / revision / timestamp → None, no raise)
- Operational-failure semantics (non-miss stderr → GitRevisionReadError)
- timeout=30 kwarg forwarded to subprocess.run
- Weekly digest degrades gracefully on GitRevisionReadError (no 500)
"""

import subprocess
import unittest.mock
import pytest
from datetime import datetime, timezone, timedelta

from app.git_ops import GitOperations, GitRevisionReadError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a git command in *cwd*, raise on non-zero exit."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def build_two_commit_repo(tmp_path) -> tuple[str, str, str]:
    """Create a repo with two commits on 'f.md' and return (repo_path, sha1, sha2).

    Commit timestamps are spaced 60 s apart so get_revision_before tests have
    an unambiguous "between" point.
    """
    repo = str(tmp_path / "repo")
    import os

    os.makedirs(repo)

    # Init & configure identity
    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test User"], cwd=repo)

    # First commit — t0
    t0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    (tmp_path / "repo" / "f.md").write_text("version 1\n")
    _git(["add", "f.md"], cwd=repo)
    env_t0 = {
        **os.environ,
        "GIT_AUTHOR_DATE": t0.isoformat(),
        "GIT_COMMITTER_DATE": t0.isoformat(),
    }
    subprocess.run(
        ["git", "commit", "-m", "first"],
        cwd=repo,
        env=env_t0,
        capture_output=True,
        text=True,
        check=True,
    )
    sha1 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Second commit — t0 + 60 s
    t1 = t0 + timedelta(seconds=60)
    (tmp_path / "repo" / "f.md").write_text("version 2\n")
    _git(["add", "f.md"], cwd=repo)
    env_t1 = {
        **os.environ,
        "GIT_AUTHOR_DATE": t1.isoformat(),
        "GIT_COMMITTER_DATE": t1.isoformat(),
    }
    subprocess.run(
        ["git", "commit", "-m", "second"],
        cwd=repo,
        env=env_t1,
        capture_output=True,
        text=True,
        check=True,
    )
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return repo, sha1, sha2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_and_shas(tmp_path):
    repo_path, sha1, sha2 = build_two_commit_repo(tmp_path)
    ops = GitOperations(repo_path=repo_path)
    return ops, sha1, sha2


# ---------------------------------------------------------------------------
# read_file_at_revision tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_at_first_revision_returns_v1(repo_and_shas):
    """read_file_at_revision returns historic content while HEAD holds v2."""
    ops, sha1, _sha2 = repo_and_shas
    content = await ops.read_file_at_revision("f.md", sha1)
    assert content == "version 1\n"


@pytest.mark.asyncio
async def test_read_file_at_revision_path_not_present_returns_none(repo_and_shas):
    """A path that never existed at a real revision returns None (no raise)."""
    ops, sha1, _sha2 = repo_and_shas
    result = await ops.read_file_at_revision("nonexistent/ghost.md", sha1)
    assert result is None


@pytest.mark.asyncio
async def test_read_file_at_revision_garbage_revision_returns_none(repo_and_shas):
    """A completely invalid revision string returns None (no raise)."""
    ops, _sha1, _sha2 = repo_and_shas
    result = await ops.read_file_at_revision(
        "f.md", "deadbeefdeadbeefdeadbeefdeadbeef00000000"
    )
    assert result is None


# ---------------------------------------------------------------------------
# get_revision_before tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_revision_before_between_commits_returns_sha1(repo_and_shas):
    """Timestamp between the two commits resolves to the first commit sha."""
    ops, sha1, _sha2 = repo_and_shas
    # t0 = 2024-01-01T12:00:00Z, t1 = t0+60s; midpoint = t0+30s
    between = "2024-01-01T12:00:30+00:00"
    result = await ops.get_revision_before(between, path="f.md")
    assert result == sha1


@pytest.mark.asyncio
async def test_get_revision_before_before_all_commits_returns_none(repo_and_shas):
    """Timestamp before any commits returns None."""
    ops, _sha1, _sha2 = repo_and_shas
    before_all = "2023-01-01T00:00:00+00:00"
    result = await ops.get_revision_before(before_all, path="f.md")
    assert result is None


@pytest.mark.asyncio
async def test_get_revision_before_after_all_commits_returns_sha2(repo_and_shas):
    """Timestamp after both commits resolves to the second (latest) sha."""
    ops, _sha1, sha2 = repo_and_shas
    after_all = "2025-01-01T00:00:00+00:00"
    result = await ops.get_revision_before(after_all, path="f.md")
    assert result == sha2


@pytest.mark.asyncio
async def test_get_revision_before_no_path_variant(repo_and_shas):
    """path=None (any-file variant) also works — returns a valid sha."""
    ops, sha1, sha2 = repo_and_shas
    between = "2024-01-01T12:00:30+00:00"
    result = await ops.get_revision_before(between, path=None)
    assert result == sha1


# ---------------------------------------------------------------------------
# Input validation tests (CodeRabbit hardening, PR #964)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_at_revision_empty_path_returns_none(repo_and_shas):
    """Empty path string returns None immediately (no subprocess call)."""
    ops, sha1, _sha2 = repo_and_shas
    result = await ops.read_file_at_revision("", sha1)
    assert result is None


@pytest.mark.asyncio
async def test_read_file_at_revision_whitespace_path_returns_none(repo_and_shas):
    """Whitespace-only path returns None immediately."""
    ops, sha1, _sha2 = repo_and_shas
    result = await ops.read_file_at_revision("   ", sha1)
    assert result is None


@pytest.mark.asyncio
async def test_read_file_at_revision_empty_revision_returns_none(repo_and_shas):
    """Empty revision string returns None immediately (no subprocess call)."""
    ops, _sha1, _sha2 = repo_and_shas
    result = await ops.read_file_at_revision("f.md", "")
    assert result is None


@pytest.mark.asyncio
async def test_get_revision_before_empty_timestamp_returns_none(repo_and_shas):
    """Empty timestamp returns None immediately (no subprocess call)."""
    ops, _sha1, _sha2 = repo_and_shas
    result = await ops.get_revision_before("", path="f.md")
    assert result is None


@pytest.mark.asyncio
async def test_get_revision_before_whitespace_timestamp_returns_none(repo_and_shas):
    """Whitespace-only timestamp returns None immediately."""
    ops, _sha1, _sha2 = repo_and_shas
    result = await ops.get_revision_before("   ", path="f.md")
    assert result is None


# ---------------------------------------------------------------------------
# Failure semantics: non-miss stderr → GitRevisionReadError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_at_revision_operational_failure_raises(repo_and_shas):
    """Non-miss stderr (e.g. permission error) raises GitRevisionReadError."""
    ops, sha1, _sha2 = repo_and_shas

    fake_result = unittest.mock.MagicMock()
    fake_result.returncode = 128
    fake_result.stdout = ""
    fake_result.stderr = "fatal: permission denied"

    with unittest.mock.patch("subprocess.run", return_value=fake_result):
        with pytest.raises(GitRevisionReadError):
            await ops.read_file_at_revision("f.md", sha1)


@pytest.mark.asyncio
async def test_get_revision_before_operational_failure_raises(repo_and_shas):
    """Non-miss stderr in rev-list raises GitRevisionReadError."""
    ops, _sha1, _sha2 = repo_and_shas

    fake_result = unittest.mock.MagicMock()
    fake_result.returncode = 128
    fake_result.stdout = ""
    fake_result.stderr = "fatal: permission denied"

    with unittest.mock.patch("subprocess.run", return_value=fake_result):
        with pytest.raises(GitRevisionReadError):
            await ops.get_revision_before("2024-01-01T12:00:00+00:00", path="f.md")


# ---------------------------------------------------------------------------
# timeout=30 forwarded to subprocess.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_at_revision_passes_timeout(repo_and_shas):
    """subprocess.run is called with timeout=30."""
    ops, sha1, _sha2 = repo_and_shas
    captured_kwargs: list[dict] = []

    original_run = subprocess.run

    def capturing_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return original_run(*args, **kwargs)

    with unittest.mock.patch("subprocess.run", side_effect=capturing_run):
        await ops.read_file_at_revision("f.md", sha1)

    assert any(
        kw.get("timeout") == 30 for kw in captured_kwargs
    ), f"Expected timeout=30 in subprocess.run kwargs, got: {captured_kwargs}"


@pytest.mark.asyncio
async def test_get_revision_before_passes_timeout(repo_and_shas):
    """subprocess.run is called with timeout=30."""
    ops, _sha1, _sha2 = repo_and_shas
    captured_kwargs: list[dict] = []

    original_run = subprocess.run

    def capturing_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return original_run(*args, **kwargs)

    with unittest.mock.patch("subprocess.run", side_effect=capturing_run):
        await ops.get_revision_before("2024-01-01T12:00:30+00:00", path="f.md")

    assert any(
        kw.get("timeout") == 30 for kw in captured_kwargs
    ), f"Expected timeout=30 in subprocess.run kwargs, got: {captured_kwargs}"


# ---------------------------------------------------------------------------
# weekly digest degrades gracefully on GitRevisionReadError
# ---------------------------------------------------------------------------


