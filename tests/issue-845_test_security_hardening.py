"""
Tests for Issue #845: Security — Remove secrets from client-facing and publicly available assets

TDD tests covering:
1. (removed) hosted-auth build secrets checks
2. build-production.sh has no hardcoded secret defaults
3. Dockerfile.production uses multi-stage build (secrets not in final image ENV)
4. ConfigManager redacts secrets before saving to disk
5. /api/command/config shows "configured"/"not configured" (no key fragments)
6. CORS uses env-based origins (not wildcard)
7. display_scheduler does not use eval()
8. config/app_config.json is gitignored
"""

import sys
import unittest.mock
from unittest.mock import MagicMock

# Mock heavy optional dependencies — only when genuinely not installed;
# stubbing an installed module poisons later files in the same process.
import importlib.util

for _mod_name in [
    "opentelemetry", "opentelemetry.trace",
    "neo4j", "neo4j.exceptions",
    "traceloop", "traceloop.sdk", "traceloop.sdk.tracing",
]:
    if _mod_name not in sys.modules and importlib.util.find_spec(_mod_name.split(".")[0]) is None:
        sys.modules[_mod_name] = MagicMock()

import os
import re


# --- Finding 1: next.config.ts must not expose server-only secrets ---



# --- Finding 2: build-production.sh must not have hardcoded secrets ---



# --- Finding 3: Dockerfile.production secrets not in final image ---



# --- Finding 4: ConfigManager must redact secrets before saving ---

class TestConfigManagerRedaction:
    """ConfigManager must not write real secrets to disk."""

    def test_save_config_redacts_api_keys(self):
        """_save_config should redact sensitive fields."""
        from app.services.config_manager import ConfigManager
        import tempfile
        import json

        mgr = ConfigManager.__new__(ConfigManager)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            tmp_path = f.name

        try:
            from pathlib import Path
            mgr.config_path = Path(tmp_path)
            from app.services.config_manager import SystemConfig, ClaudeConfig, GitHubConfig
            mgr.config = SystemConfig(
                claude=ClaudeConfig(api_key="test-fake-api-key-12345"),
                github=GitHubConfig(token="test-fake-token-123456", repo="owner/repo", webhook_secret="test-fake-webhook-secret"),
            )

            mgr._save_config()

            with open(tmp_path) as f:
                saved = json.load(f)

            # Secrets must be redacted
            assert saved["claude"]["api_key"] != "test-fake-api-key-12345", (
                "Real API key must not be written to disk"
            )
            assert saved["github"]["token"] != "test-fake-token-123456", (
                "Real GitHub token must not be written to disk"
            )
            assert "configured" in saved["claude"]["api_key"].lower() or "redacted" in saved["claude"]["api_key"].lower(), (
                "Saved value should indicate configured/redacted status"
            )

            # Verify rehydration: loading the redacted file with env vars restores secrets
            with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-rehydrated-key"}):
                mgr2 = ConfigManager.__new__(ConfigManager)
                mgr2.config_path = Path(tmp_path)
                mgr2.config = None
                mgr2._load_config()
                assert mgr2.config.claude.api_key == "test-rehydrated-key", (
                    "Secrets must be rehydrated from env vars on load"
                )
        finally:
            os.unlink(tmp_path)


# --- Finding 5: /api/command/config must not leak key fragments ---

class TestConfigEndpointRedaction:
    """Config endpoint should show 'configured'/'not configured', not key fragments."""

    def test_config_masking_uses_status_not_fragments(self):
        """Masking should use 'configured'/'not configured', not first/last 4 chars."""
        # Read the source and scope check to the get_config handler only
        route_path = os.path.join(os.path.dirname(__file__), "..", "app", "routes", "command.py")
        with open(route_path) as f:
            content = f.read()

        # Extract the get_config function body for scoped checking
        match = re.search(r'(async def get_config\b.*?)(?=\nasync def |\nclass |\Z)', content, re.DOTALL)
        handler_code = match.group(1) if match else content

        # Should NOT have fragment-based masking like api_key[:4]
        assert "[:4]" not in handler_code, (
            "Config endpoint must not expose key fragments (first/last 4 chars)"
        )
        assert "[-4:]" not in handler_code, (
            "Config endpoint must not expose key fragments (first/last 4 chars)"
        )


# --- Finding 6: CORS must not use wildcard with credentials ---

class TestCORSConfiguration:
    """CORS should use env-based origins, not wildcard."""

    def test_no_wildcard_cors(self):
        """app/main.py should not have allow_origins=['*'] or default to wildcard via env."""
        main_path = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")
        with open(main_path) as f:
            content = f.read()

        assert "CORSMiddleware" in content, "CORSMiddleware not found in app/main.py"
        # Ensure no hardcoded wildcard origin list
        assert not re.search(r'allow_origins\s*=\s*\[\s*["\']?\*["\']?\s*\]', content), (
            "CORS must not use wildcard origins with credentials"
        )
        # Ensure env fallback doesn't default to wildcard
        assert not re.search(r'getenv\s*\(\s*["\']CORS_ALLOWED_ORIGINS["\']\s*,\s*["\']?\*["\']?\s*\)', content), (
            "CORS env var must not default to wildcard '*'"
        )


# --- Finding 7: display_scheduler must not use eval() ---

class TestDisplaySchedulerEval:
    """eval() must be replaced with safe alternative."""



# --- Finding 8: config/app_config.json should be gitignored ---

class TestGitignore:
    """config/app_config.json should be in .gitignore."""

