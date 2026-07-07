"""Regression: GITHUB_ACCESS_TOKEN must populate GITHUB_TOKEN.

`.env.example`, `docker-compose.yml` and `docker-compose.dev-hot.yml` expose the
corpus token as ``GITHUB_ACCESS_TOKEN``, but the git clone/push path
(`app.git_ops`) reads ``settings.GITHUB_TOKEN``. Without an alias a private
``GIT_REPO_URL`` never authenticates. See
``app.config.Settings._resolve_github_token_alias``.
"""

import os
from unittest.mock import patch

from app.config import JSONConfigSettings, Settings


def _settings() -> Settings:
    # Isolate from any .env / config/app_config.json so only patched env
    # vars feed the Settings under test.
    with patch.object(JSONConfigSettings, "_load_json_config", return_value={}):
        return Settings(_env_file=None)


def test_github_access_token_populates_github_token():
    """The documented GITHUB_ACCESS_TOKEN name feeds the code's GITHUB_TOKEN."""
    with patch.dict(os.environ, {"GITHUB_ACCESS_TOKEN": "ghp_from_alias"}, clear=True):
        assert _settings().GITHUB_TOKEN == "ghp_from_alias"


def test_github_token_still_read_directly():
    """The canonical GITHUB_TOKEN name keeps working unchanged."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_canonical"}, clear=True):
        assert _settings().GITHUB_TOKEN == "ghp_canonical"


def test_github_token_wins_when_both_set():
    """When both are set the canonical GITHUB_TOKEN takes precedence."""
    with patch.dict(
        os.environ,
        {"GITHUB_TOKEN": "ghp_canonical", "GITHUB_ACCESS_TOKEN": "ghp_alias"},
        clear=True,
    ):
        assert _settings().GITHUB_TOKEN == "ghp_canonical"


def test_absent_token_stays_empty():
    """Neither var set → GITHUB_TOKEN stays empty (no accidental default)."""
    with patch.dict(os.environ, {}, clear=True):
        assert _settings().GITHUB_TOKEN == ""
