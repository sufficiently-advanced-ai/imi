"""Configuration management service for imi."""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

# Use an absolute path for the config file
BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
CONFIG_PATH = BASE_DIR / "config" / "app_config.json"

# Debug info
sys.stderr.write(f"Config path: {CONFIG_PATH}\n")
sys.stderr.write(f"Config directory exists: {CONFIG_PATH.parent.exists()}\n")
sys.stderr.flush()


class ServiceConfig(BaseModel):
    """Base model for service configuration."""

    name: str
    status: str = "unknown"
    error_message: str | None = None


class ClaudeConfig(ServiceConfig):
    """Claude API configuration."""

    name: str = "claude"
    api_key: str
    model: str = "claude-sonnet-4-5-20250929"


class GitHubConfig(ServiceConfig):
    """GitHub configuration."""

    name: str = "github"
    token: str
    repo: str
    webhook_secret: str | None = None


class SystemConfig(BaseModel):
    """Complete system configuration."""

    claude: ClaudeConfig
    github: GitHubConfig


class ConfigManager:
    """Manages system configuration."""

    def __init__(self):
        self.config_path = CONFIG_PATH
        self.config: SystemConfig | None = None
        self._ensure_config_dir()
        self._load_config()

    def _ensure_config_dir(self) -> None:
        """Ensure configuration directory exists."""
        config_dir = self.config_path.parent
        sys.stderr.write(f"Ensuring config directory exists: {config_dir}\n")
        try:
            if not config_dir.exists():
                sys.stderr.write(f"Creating config directory: {config_dir}\n")
                config_dir.mkdir(parents=True, exist_ok=True)
                sys.stderr.write(f"Config directory created: {config_dir.exists()}\n")
            else:
                sys.stderr.write("Config directory already exists\n")
        except Exception as e:
            sys.stderr.write(f"Error creating config directory: {str(e)}\n")
            # Fall back to a private per-process temp dir (mode 0700). A fixed
            # /tmp path could be pre-created or symlinked by another local user
            # to hijack config reads/writes.
            temp_dir = Path(tempfile.mkdtemp(prefix="imi-config-"))
            sys.stderr.write(f"Falling back to temporary directory: {temp_dir}\n")
            self.config_path = temp_dir / "app_config.json"
            sys.stderr.write(f"New config path: {self.config_path}\n")
        sys.stderr.flush()

    # Mapping from config field paths to environment variable names
    _ENV_MAPPING = {
        ("claude", "api_key"): "ANTHROPIC_API_KEY",
        ("github", "token"): "GITHUB_TOKEN",
        ("github", "webhook_secret"): "GITHUB_WEBHOOK_SECRET",
    }

    def _rehydrate_secrets(self, config_data: dict) -> dict:
        """Replace redacted placeholders with real values from environment."""
        for (section, key), env_var in self._ENV_MAPPING.items():
            if section in config_data and isinstance(config_data[section], dict):
                value = config_data[section].get(key, "")
                if isinstance(value, str) and value in ("[CONFIGURED]", "[REDACTED]"):
                    env_value = os.environ.get(env_var, "")
                    if env_value:
                        config_data[section][key] = env_value
                    else:
                        # Clear placeholder so it's not mistaken for a real credential
                        config_data[section][key] = ""
                        sys.stderr.write(
                            f"Warning: {section}.{key} is redacted but {env_var} not in environment\n"
                        )
        return config_data

    def _load_config(self) -> None:
        """Load configuration from file or environment variables."""
        sys.stderr.write(f"Loading configuration from: {self.config_path}\n")
        try:
            if self.config_path.exists():
                sys.stderr.write("Config file exists, loading...\n")
                with open(self.config_path) as f:
                    config_data = json.load(f)
                    config_data = self._rehydrate_secrets(config_data)
                    self.config = SystemConfig(**config_data)
                sys.stderr.write("Config loaded from file\n")
            else:
                sys.stderr.write(
                    "Config file does not exist, initializing from environment variables\n"
                )
                # Initialize from environment variables
                self.config = SystemConfig(
                    claude=ClaudeConfig(
                        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                        model=(
                            os.environ.get("CLAUDE_SONNET_MODEL")
                            or os.environ.get("CLAUDE_MODEL")
                            or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
                        ),
                    ),
                    github=GitHubConfig(
                        token=os.environ.get("GITHUB_TOKEN", ""),
                        repo=os.environ.get("GITHUB_REPO", ""),
                        webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
                    ),
                )
                sys.stderr.write("Saving initial config to file\n")
                self._save_config()
        except Exception as e:
            sys.stderr.write(f"Error loading configuration: {str(e)}\n")
            # Create a minimal default configuration
            self.config = SystemConfig(
                claude=ClaudeConfig(api_key="", model="claude-sonnet-4-5-20250929"),
                github=GitHubConfig(token="", repo=""),
            )
            sys.stderr.write("Created default configuration\n")
        sys.stderr.flush()

    # Fields that contain secrets and must be redacted before saving to disk
    _SECRET_FIELDS = {"api_key", "token", "webhook_secret", "cookie_password"}

    def _redact_secrets(self, data: dict) -> dict:
        """Replace secret values with a redacted placeholder."""
        redacted = {}
        for key, value in data.items():
            if isinstance(value, dict):
                redacted[key] = self._redact_secrets(value)
            elif key in self._SECRET_FIELDS and isinstance(value, str) and value:
                redacted[key] = "[CONFIGURED]"
            else:
                redacted[key] = value
        return redacted

    def _save_config(self) -> None:
        """Save configuration to file with secrets redacted."""
        if self.config:
            sys.stderr.write(f"Saving configuration to: {self.config_path}\n")
            try:
                safe_data = self._redact_secrets(self.config.model_dump())
                with open(self.config_path, "w") as f:
                    json.dump(safe_data, f, indent=2)
                sys.stderr.write("Configuration saved successfully (secrets redacted)\n")
            except Exception as e:
                sys.stderr.write(f"Error saving configuration: {str(e)}\n")
            sys.stderr.flush()

    def get_config(self) -> dict[str, Any]:
        """Get current configuration."""
        if not self.config:
            self._load_config()
        return self.config.model_dump() if self.config else {}

    def update_config(self, config_data: dict[str, Any]) -> dict[str, Any]:
        """Update configuration with new data."""
        if not self.config:
            self._load_config()

        # Merge with existing config
        config_dict = self.config.model_dump() if self.config else {}

        for service, service_config in config_data.items():
            if service in config_dict:
                config_dict[service].update(service_config)
            else:
                config_dict[service] = service_config

        self.config = SystemConfig(**config_dict)
        self._save_config()
        return self.get_config()

    async def test_connection(self, service_name: str) -> dict[str, Any]:
        """Test connection to a specific service."""
        if not self.config:
            self._load_config()

        result = {"name": service_name, "status": "unknown", "error_message": None}

        if service_name == "claude":
            result = await self._test_claude_connection()
        elif service_name == "github":
            result = await self._test_github_connection()

        # Update config status
        config_dict = self.config.model_dump() if self.config else {}
        if service_name in config_dict:
            config_dict[service_name]["status"] = result["status"]
            config_dict[service_name]["error_message"] = result["error_message"]

            self.config = SystemConfig(**config_dict)
            self._save_config()

        return result

    async def test_all_connections(self) -> dict[str, dict[str, Any]]:
        """Test all service connections."""
        results = {}

        for service in ["claude", "github"]:
            results[service] = await self.test_connection(service)

        return results

    async def _test_claude_connection(self) -> dict[str, Any]:
        """Test connection to Claude API."""
        result = {"name": "claude", "status": "unknown", "error_message": None}

        if not self.config or not self.config.claude or not self.config.claude.api_key:
            result["status"] = "error"
            result["error_message"] = "Claude API key not configured"
            return result

        try:
            async with httpx.AsyncClient() as client:
                # Attempt a simple API call to verify the API key
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.config.claude.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.config.claude.model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "Hello"}],
                    },
                    timeout=5.0,
                )

                if response.status_code == 200:
                    result["status"] = "connected"
                else:
                    result["status"] = "error"
                    result["error_message"] = (
                        f"API error: {response.status_code} - {response.text}"
                    )

        except Exception as e:
            result["status"] = "error"
            result["error_message"] = f"Connection error: {str(e)}"

        return result

    async def _test_github_connection(self) -> dict[str, Any]:
        """Test connection to GitHub API."""
        result = {"name": "github", "status": "unknown", "error_message": None}

        if not self.config or not self.config.github or not self.config.github.token:
            result["status"] = "error"
            result["error_message"] = "GitHub token not configured"
            return result

        if not self.config.github.repo:
            result["status"] = "error"
            result["error_message"] = "GitHub repository not configured"
            return result

        try:
            repo_parts = self.config.github.repo.split("/")
            if len(repo_parts) != 2:
                result["status"] = "error"
                result["error_message"] = (
                    "Invalid repository format, must be 'owner/repo'"
                )
                return result

            owner, repo = repo_parts

            async with httpx.AsyncClient() as client:
                # Test repository access
                response = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}",
                    headers={
                        "Authorization": f"token {self.config.github.token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                    timeout=5.0,
                )

                if response.status_code == 200:
                    result["status"] = "connected"
                else:
                    result["status"] = "error"
                    result["error_message"] = (
                        f"GitHub API error: {response.status_code} - {response.text}"
                    )

        except Exception as e:
            result["status"] = "error"
            result["error_message"] = f"Connection error: {str(e)}"

        return result

# Singleton instance
config_manager = ConfigManager()
