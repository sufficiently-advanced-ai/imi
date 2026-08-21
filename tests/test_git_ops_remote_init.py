"""Remote-mode initialize(): refresh a persistent checkout in place, never wipe."""

import subprocess

import pytest

import app.git_ops as git_ops_module
from app.git_ops import GitOperationError, GitOperations


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def origin(tmp_path):
    """A bare 'origin' with one commit on main, plus a work clone to push from."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _git("init", "--bare", "-b", "main", cwd=bare)
    work = tmp_path / "work"
    _git("clone", str(bare), str(work), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "a.md").write_text("one\n")
    _git("add", "a.md", cwd=work)
    _git("commit", "-m", "one", cwd=work)
    _git("push", "origin", "main", cwd=work)
    return bare, work


@pytest.fixture
def remote_ops(tmp_path, monkeypatch, origin):
    bare, _ = origin
    monkeypatch.setattr(git_ops_module.settings, "GIT_REPO_URL", str(bare), raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GITHUB_TOKEN", "", raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GIT_BRANCH", "main", raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GIT_USER_EMAIL", "bot@example.com", raising=False)
    monkeypatch.setattr(git_ops_module.settings, "GIT_USER_NAME", "bot", raising=False)
    ops = GitOperations(repo_path=str(tmp_path / "repo"))
    # don't touch the developer's global git config
    monkeypatch.setattr(git_ops_module.subprocess, "run", _no_global_config(git_ops_module.subprocess.run))
    return ops


def _no_global_config(real_run):
    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "config", "--global"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *args, **kwargs)
    return run


@pytest.mark.asyncio
async def test_fresh_clone_into_empty_dir(remote_ops):
    await remote_ops.initialize()
    assert (remote_ops.repo_path + "/a.md") and open(remote_ops.repo_path + "/a.md").read() == "one\n"
    assert not remote_ops.local_only


@pytest.mark.asyncio
async def test_existing_checkout_is_refreshed_not_wiped(remote_ops, origin):
    _, work = origin
    await remote_ops.initialize()

    # Something git has never seen — the class of file the old rm -rf destroyed
    untracked = open(remote_ops.repo_path + "/memory-record.json", "w")
    untracked.write("{}")
    untracked.close()

    # origin moves on
    (work / "b.md").write_text("two\n")
    _git("add", "b.md", cwd=work)
    _git("commit", "-m", "two", cwd=work)
    _git("push", "origin", "main", cwd=work)

    await remote_ops.initialize()

    assert open(remote_ops.repo_path + "/memory-record.json").read() == "{}"
    assert open(remote_ops.repo_path + "/b.md").read() == "two\n"
    assert await remote_ops.get_head_commit() == _git("rev-parse", "HEAD", cwd=work).stdout.strip()


@pytest.mark.asyncio
async def test_diverged_local_history_is_kept(remote_ops, origin):
    _, work = origin
    await remote_ops.initialize()
    repo = remote_ops.repo_path
    _git("config", "user.email", "bot@example.com", cwd=repo)
    _git("config", "user.name", "bot", cwd=repo)
    open(repo + "/local.md", "w").write("local\n")
    _git("add", "local.md", cwd=repo)
    _git("commit", "-m", "local only", cwd=repo)
    local_head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    (work / "c.md").write_text("three\n")
    _git("add", "c.md", cwd=work)
    _git("commit", "-m", "three", cwd=work)
    _git("push", "origin", "main", cwd=work)

    await remote_ops.initialize()  # cannot fast-forward → must not raise, must not reset

    assert _git("rev-parse", "HEAD", cwd=repo).stdout.strip() == local_head
    assert open(repo + "/local.md").read() == "local\n"


@pytest.mark.asyncio
async def test_non_empty_non_git_dir_fails_closed(remote_ops):
    import os
    os.makedirs(remote_ops.repo_path, exist_ok=True)
    open(remote_ops.repo_path + "/precious.md", "w").write("keep me")
    with pytest.raises(GitOperationError):
        await remote_ops.initialize()
    assert open(remote_ops.repo_path + "/precious.md").read() == "keep me"


@pytest.mark.asyncio
async def test_github_token_is_never_persisted_in_git_config(remote_ops, monkeypatch, origin):
    bare, _ = origin
    monkeypatch.setattr(git_ops_module.settings, "GITHUB_TOKEN", "ghp_supersecret", raising=False)
    await remote_ops.initialize()
    await remote_ops.initialize()  # refresh path too
    config = open(remote_ops.repo_path + "/.git/config").read()
    assert "ghp_supersecret" not in config
    assert str(bare) in config
    # credentials only apply to github.com HTTPS remotes, and travel in the
    # environment (GIT_CONFIG_*), never in argv
    assert remote_ops._git_env(str(bare)) is None
    env = remote_ops._git_env("https://github.com/org/repo.git")
    assert env["GIT_CONFIG_COUNT"] == "1" and env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "ghp_supersecret" not in env["GIT_CONFIG_VALUE_0"]


def test_failed_authenticated_git_command_does_not_leak_the_credential(tmp_path, monkeypatch):
    """A failing git command renders argv into CalledProcessError (→ logs,
    /health/startup failures); the credential must not be in there."""
    monkeypatch.setattr(git_ops_module.settings, "GITHUB_TOKEN", "ghp_supersecret", raising=False)
    ops = GitOperations(repo_path=str(tmp_path))
    env = ops._git_env("https://github.com/org/repo.git")
    argv = ["git", "clone", "https://github.com/org/repo.git", "."]
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.run(
            ["python3", "-c", "import sys; sys.exit(1)", *argv],
            env=env, check=True, capture_output=True, text=True, timeout=30,
        )
    rendered = str(excinfo.value) + repr(excinfo.value.cmd)
    assert "ghp_supersecret" not in rendered
    assert "Authorization" not in rendered
    assert env["GIT_CONFIG_VALUE_0"].split()[-1] not in rendered  # the base64 value either


@pytest.mark.asyncio
async def test_initialised_repo_without_commits_is_pointed_at_origin(remote_ops, origin):
    """`git init` + remote, no commits (unborn HEAD): must end up on origin/main."""
    import os

    bare, _ = origin
    os.makedirs(remote_ops.repo_path)
    _git("init", "-b", "main", cwd=remote_ops.repo_path)
    _git("remote", "add", "origin", str(bare), cwd=remote_ops.repo_path)
    open(remote_ops.repo_path + "/untracked.json", "w").write("{}")
    await remote_ops.initialize()
    assert open(remote_ops.repo_path + "/a.md").read() == "one\n"
    assert open(remote_ops.repo_path + "/untracked.json").read() == "{}"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=remote_ops.repo_path).stdout.strip() == "main"
