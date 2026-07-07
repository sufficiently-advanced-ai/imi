"""Local-only corpus mode (no GIT_REPO_URL) — community edition.

With no remote configured: initialize() must `git init` and PRESERVE the
existing working dir across restarts (it is the only copy of the corpus),
and commit_and_push()/commit_file() must commit locally without pushing.
"""

import subprocess

import pytest

import app.git_ops as git_ops_module
from app.git_ops import GitOperations


@pytest.fixture
def local_ops(tmp_path, monkeypatch):
    monkeypatch.setattr(git_ops_module.settings, "GIT_REPO_URL", "", raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GITHUB_TOKEN", "", raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GIT_BRANCH", "main", raising=False)
    return GitOperations(repo_path=str(tmp_path / "corpus"))


@pytest.mark.asyncio
async def test_initialize_inits_local_repo_without_remote(local_ops):
    assert local_ops.local_only is True
    await local_ops.initialize()
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=local_ops.repo_path,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "true"


@pytest.mark.asyncio
async def test_initialize_preserves_existing_corpus(local_ops):
    await local_ops.initialize()
    marker = f"{local_ops.repo_path}/meetings"
    import os

    os.makedirs(marker, exist_ok=True)
    with open(f"{marker}/keep.md", "w") as f:
        f.write("precious")

    # A restart re-runs initialize(); the corpus must survive.
    await local_ops.initialize()
    with open(f"{marker}/keep.md") as f:
        assert f.read() == "precious"


@pytest.mark.asyncio
async def test_commit_file_works_without_remote(local_ops):
    await local_ops.initialize()
    await local_ops.commit_file("signals/meeting-x.json", '{"ok": true}', "test commit")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=local_ops.repo_path,
        capture_output=True,
        text=True,
    )
    assert "test commit" in log.stdout
    remotes = subprocess.run(
        ["git", "remote"], cwd=local_ops.repo_path, capture_output=True, text=True
    )
    assert remotes.stdout.strip() == ""
