import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class JSONConfigSettings(BaseSettings):
    """Settings that can load from JSON config file, honoring env > JSON > defaults."""

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # Keep priority: init (explicit kwargs) > env > JSON > .env file > file secrets
        return (
            init_settings,
            env_settings,
            lambda: cls._json_settings_source(),  # our JSON file reader (lower than env)
            dotenv_settings,
            file_secret_settings,
        )

    @staticmethod
    def _json_settings_source() -> dict[str, Any]:
        try:
            return JSONConfigSettings._load_json_config()
        except Exception:
            return {}

    @staticmethod
    def _load_json_config() -> dict[str, Any]:
        """Load configuration from JSON file"""
        # Look for config relative to the project root
        # Use /app as the project root when running in container
        project_root = Path("/app")
        config_path = project_root / "config" / "app_config.json"

        if not config_path.exists():
            return {}

        try:
            with open(config_path) as f:
                config = json.load(f)

            # Flatten the nested structure for pydantic
            flattened = {}

            # Extract Claude settings
            if "claude" in config:
                claude = config["claude"]
                flattened["ANTHROPIC_API_KEY"] = claude.get("api_key", "")
                flattened["CLAUDE_SONNET_MODEL"] = claude.get(
                    "sonnet_model", claude.get("model", "claude-sonnet-4-5-20250929")
                )
                flattened["CLAUDE_HAIKU_MODEL"] = claude.get(
                    "haiku_model", claude.get("default_model", "claude-haiku-4-5-20251001")
                )
                flattened["CLAUDE_AGENT_MODEL"] = claude.get(
                    "agent_model", "claude-haiku-4-5-20251001"
                )
                if claude.get("base_url"):
                    flattened["ANTHROPIC_BASE_URL"] = claude["base_url"]

            # Extract GitHub settings
            if "github" in config:
                github = config["github"]
                flattened["GITHUB_TOKEN"] = github.get("token", "")
                flattened["REPO_NAME"] = github.get("repo", "")
                flattened["WEBHOOK_SECRET"] = github.get("webhook_secret", "")

            return flattened

        except (json.JSONDecodeError, Exception) as e:
            print(f"Warning: Could not load JSON config: {e}")
            return {}


class Settings(JSONConfigSettings):
    # Webhook settings
    WEBHOOK_SECRET: str = ""
    REPO_NAME: str = ""  # e.g. "owner/repo"
    allowed_branches: list[str] = ["main"]
    rate_limit_requests: int = 100
    rate_limit_window: int = 3600  # 1 hour in seconds

    # Git settings
    GIT_REPO_URL: str = ""
    GIT_BRANCH: str = "main"
    GITHUB_TOKEN: str = ""  # required for PyGithub
    # Alias for GITHUB_TOKEN. .env.example, docker-compose.yml and
    # docker-compose.dev-hot.yml expose the corpus token as GITHUB_ACCESS_TOKEN,
    # but the git clone/push code reads GITHUB_TOKEN. Bridged in
    # _resolve_github_token_alias below.
    GITHUB_ACCESS_TOKEN: str = ""

    # Git user identity settings
    GIT_USER_EMAIL: str | None = None
    GIT_USER_NAME: str | None = None

    # Claude settings
    ANTHROPIC_API_KEY: str = ""  # required for claude
    # Optional override for the Anthropic API base URL. Lets the default
    # endpoint point at an Anthropic-compatible gateway/proxy (e.g. a single
    # in-DMZ box). Multi-endpoint routing lives in config/inference.yaml.
    ANTHROPIC_BASE_URL: str = ""

    # Model configuration — new canonical names (clear tier-based naming)
    CLAUDE_SONNET_MODEL: str = "claude-sonnet-4-5-20250929"  # Full-power: complex analysis, chat, synthesis
    CLAUDE_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"  # Lightweight: extraction, background processing
    CLAUDE_AGENT_MODEL: str = "claude-haiku-4-5-20251001"  # Agent SDK: meeting coordinator, central agent (switched from Sonnet for cost optimization)

    # Backward-compat aliases — if set in .env, these override the new names above
    CLAUDE_MODEL: str = ""  # Legacy alias for CLAUDE_SONNET_MODEL
    CLAUDE_DEFAULT_MODEL: str = ""  # Legacy alias for CLAUDE_HAIKU_MODEL
    CLAUDE_CHAT_MODEL: str = ""  # Removed — was never used in code

    @model_validator(mode="after")
    def _resolve_model_aliases(self) -> "Settings":
        """Support old env var names: CLAUDE_MODEL → CLAUDE_SONNET_MODEL, etc."""
        if self.CLAUDE_MODEL:
            self.CLAUDE_SONNET_MODEL = self.CLAUDE_MODEL
        if self.CLAUDE_DEFAULT_MODEL:
            self.CLAUDE_HAIKU_MODEL = self.CLAUDE_DEFAULT_MODEL
        return self

    @model_validator(mode="after")
    def _resolve_github_token_alias(self) -> "Settings":
        """Support the documented env var name: GITHUB_ACCESS_TOKEN → GITHUB_TOKEN.

        Without this bridge a private corpus (GIT_REPO_URL) never authenticates:
        the clone/push path in git_ops reads settings.GITHUB_TOKEN, while the
        shipped compose files and .env.example only set GITHUB_ACCESS_TOKEN. The
        canonical GITHUB_TOKEN wins when both are provided.
        """
        if self.GITHUB_ACCESS_TOKEN and not self.GITHUB_TOKEN:
            self.GITHUB_TOKEN = self.GITHUB_ACCESS_TOKEN
        return self

    # Bot settings
    BOT_COMMIT_PREFIX: str = "[bot]"  # Prefix for automated commits

    # Domain configuration
    DOMAINS_DIR: str = "/tmp/domains"
    PACKAGES_DIR: str = "/tmp/packages"

    # Vector store backend for governed memory records (signals, captures,
    # agent memories): "sqlite" (community default — persistent sidecar file
    # beside DATABASE_PATH), "pgvector" (hosted; requires DATABASE_URL), or
    # "faiss" (legacy semantica in-memory path; NOT recommended: the semantica
    # 0.3-0.5 FAISS facade drops vector metadata, which silently empties
    # governed recall, and loses all vectors on restart). Literal so a typo
    # fails at startup instead of silently degrading at resolution time.
    VECTOR_BACKEND: Literal["sqlite", "pgvector", "faiss"] = Field(
        "sqlite", description="Vector store backend: sqlite | pgvector | faiss"
    )

    # Database Settings - Issue #360
    DATABASE_URL: str | None = Field(None, description="SQLAlchemy database URL")
    DATABASE_PATH: str = Field(
        "/app/data/imi.db", description="Path to SQLite database file"
    )
    DATABASE_POOL_SIZE: int = Field(5, description="Database connection pool size")
    DATABASE_MAX_OVERFLOW: int = Field(10, description="Database max pool overflow")
    DATABASE_POOL_TIMEOUT: float = Field(
        30.0, description="Database pool timeout in seconds"
    )
    DATABASE_POOL_RECYCLE: int = Field(
        3600, description="Database pool recycle time in seconds"
    )

    # Standing Jobs Settings (Sprint 3, S3-5)
    STANDING_JOBS_ENABLED: bool = True
    STALENESS_EVAL_INTERVAL_SECONDS: int = Field(21600, ge=60)  # 6 hours
    WEEKLY_DIGEST_CHECK_INTERVAL_SECONDS: int = Field(86400, ge=60)  # 24 hours
    COMMITMENT_AGING_DAYS: int = Field(
        7, ge=1
    )  # Threshold for surfacing old open action items in weekly digest

    # Conflict Detection Settings (Sprint 4, S4-1)
    CONFLICT_CONFIDENCE_THRESHOLD: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="Minimum LLM confidence to surface a conflict candidate",
    )
    CONFLICT_MAX_COMPARISONS_PER_INGEST: int = Field(
        5,
        ge=1,
        le=50,
        description="Maximum standing signals compared per new decision ingest",
    )

    # Encryption settings
    ENCRYPTION_KEY: str = Field("", env="ENCRYPTION_KEY", description="AES encryption key for sensitive data")

    # OpenTelemetry Production Configuration - Issue #526
    OTEL_ENABLED: bool = Field(True, env="OTEL_ENABLED", description="Enable OpenTelemetry telemetry")
    OTEL_SERVICE_NAME: str = Field("imi", env="OTEL_SERVICE_NAME")
    OTEL_SERVICE_VERSION: str = Field("1.0.0", env="OTEL_SERVICE_VERSION")
    OTEL_SERVICE_INSTANCE_ID: str = Field("", env="OTEL_SERVICE_INSTANCE_ID")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field("http://localhost:4318", env="OTEL_EXPORTER_OTLP_ENDPOINT")
    OTEL_EXPORTER_OTLP_HEADERS: str = Field("", env="OTEL_EXPORTER_OTLP_HEADERS", description="Comma-separated headers")
    OTEL_METRICS_EXPORTER: str = Field("otlp", env="OTEL_METRICS_EXPORTER")
    OTEL_TRACES_EXPORTER: str = Field("otlp", env="OTEL_TRACES_EXPORTER")

    # Telemetry Sampling Configuration
    TELEMETRY_SAMPLING_ENABLED: bool = Field(False, env="TELEMETRY_SAMPLING_ENABLED", description="Sampling disabled for QA/build process")
    TELEMETRY_DEFAULT_SAMPLE_RATE: float = Field(1.0, env="TELEMETRY_DEFAULT_SAMPLE_RATE", description="100% sampling for QA and bug remediation")
    TELEMETRY_ERROR_SAMPLE_RATE: float = Field(1.0, env="TELEMETRY_ERROR_SAMPLE_RATE", description="100% error sampling")
    TELEMETRY_HIGH_PRIORITY_SAMPLE_RATE: float = Field(1.0, env="TELEMETRY_HIGH_PRIORITY_SAMPLE_RATE", description="100% high priority sampling")
    TELEMETRY_LLM_SAMPLE_RATE: float = Field(1.0, env="TELEMETRY_LLM_SAMPLE_RATE", description="100% LLM operation sampling for QA")
    TELEMETRY_WEBHOOK_SAMPLE_RATE: float = Field(1.0, env="TELEMETRY_WEBHOOK_SAMPLE_RATE", description="100% webhook sampling for QA")
    TELEMETRY_BATCH_SIZE: int = Field(512, env="TELEMETRY_BATCH_SIZE")
    TELEMETRY_EXPORT_TIMEOUT: int = Field(30, env="TELEMETRY_EXPORT_TIMEOUT")

    # PII Protection Settings
    TELEMETRY_PII_SCRUBBING_ENABLED: bool = Field(True, env="TELEMETRY_PII_SCRUBBING_ENABLED")
    TELEMETRY_MAX_ATTRIBUTE_LENGTH: int = Field(1024, env="TELEMETRY_MAX_ATTRIBUTE_LENGTH")
    TELEMETRY_MAX_SPAN_ATTRIBUTES: int = Field(128, env="TELEMETRY_MAX_SPAN_ATTRIBUTES")
    TELEMETRY_SCRUB_USER_DATA: bool = Field(True, env="TELEMETRY_SCRUB_USER_DATA")
    TELEMETRY_ALLOWED_DOMAINS: list[str] = Field([], env="TELEMETRY_ALLOWED_DOMAINS", description="Comma-separated list of allowed domains")

    # Performance Settings
    TELEMETRY_ASYNC_EXPORT: bool = Field(True, env="TELEMETRY_ASYNC_EXPORT")
    TELEMETRY_MAX_EXPORT_BATCH_SIZE: int = Field(512, env="TELEMETRY_MAX_EXPORT_BATCH_SIZE")
    TELEMETRY_EXPORT_INTERVAL: int = Field(5000, env="TELEMETRY_EXPORT_INTERVAL", description="Export interval in milliseconds")
    TELEMETRY_PERFORMANCE_OVERHEAD_LIMIT: float = Field(0.02, env="TELEMETRY_PERFORMANCE_OVERHEAD_LIMIT", description="2% max overhead")

    # Environment-Specific Settings
    DEPLOY_ENV: str = Field("development", env="DEPLOY_ENV")
    CLIENT_NAME: str = Field("unknown", env="CLIENT_NAME")

    # Neo4j Graph Database Settings
    NEO4J_URI: str = Field("bolt://localhost:7687", env="NEO4J_URI", description="Neo4j Bolt connection URI")
    NEO4J_USERNAME: str = Field("neo4j", env="NEO4J_USERNAME", description="Neo4j username")
    NEO4J_PASSWORD: SecretStr = Field(SecretStr("dev-password-2024"), env="NEO4J_PASSWORD", description="Neo4j password")
    NEO4J_REBUILD_ON_STARTUP: bool = Field(True, env="NEO4J_REBUILD_ON_STARTUP", description="Wipe and rebuild knowledge graph on container startup")

    # MCP Server Settings
    # The MCP SDK's DNS-rebinding-protection middleware (mcp.server.transport_security)
    # rejects requests whose Host header isn't on this allowlist with HTTP 421.
    # Stored as a comma-separated string (not List[str]) to sidestep
    # pydantic-settings' default JSON-decode for list types — env values like
    # `host1,host2` would otherwise raise SettingsError on startup. Consumers
    # split on comma at use time. Default empty: localhost is always added in
    # mcp_server._build_allowed_hosts(); this var only contributes additional
    # public hostnames when the SSE endpoint is exposed via a reverse proxy
    # that preserves the Host header (`proxy_set_header Host $host;`).
    MCP_ALLOWED_HOSTS: str = Field(
        "",
        env="MCP_ALLOWED_HOSTS",
        description="Comma-separated Host header allowlist for the MCP SSE endpoint",
    )

    # Demo Mode Settings (Epic #783)
    DEMO_MODE: bool = Field(False, env="DEMO_MODE", description="Enable demo video data injection routes")

    # Auth provider seam. 'demo' forces the mock-user flow with a session
    # cookie; 'none' is demo without any cookie requirement.
    AUTH_MODE: Literal["demo", "none"] = Field(
        "none",
        env="AUTH_MODE",
        description="'demo' | 'none' — which AuthProvider the app uses",
    )

    @validator("AUTH_MODE", pre=True)
    def _normalize_auth_mode(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @validator('TELEMETRY_ALLOWED_DOMAINS', pre=True)
    def parse_allowed_domains(cls, v):
        if isinstance(v, str):
            return [domain.strip() for domain in v.split(',') if domain.strip()]
        return v or []


    @validator('OTEL_EXPORTER_OTLP_HEADERS', pre=True)
    def parse_otlp_headers(cls, v):
        if not v:
            return ""
        if isinstance(v, dict):
            # Convert dict back to string format for field validation
            return ",".join([f"{k}={v}" for k, v in v.items()])
        return v

    model_config = SettingsConfigDict(
        env_file=".env.test"
        if "ENV_FILE" in os.environ and os.environ["ENV_FILE"] == ".env.test"
        else ".env"
    )

class TelemetryConfig:
    """Production OpenTelemetry configuration wrapper with sampling and PII protection."""

    def __init__(self, settings: Settings):
        # Core OpenTelemetry settings
        self.enabled = settings.OTEL_ENABLED
        self.service_name = settings.OTEL_SERVICE_NAME
        self.service_version = settings.OTEL_SERVICE_VERSION
        self.service_instance_id = settings.OTEL_SERVICE_INSTANCE_ID or self._generate_instance_id()
        self.endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
        self.headers = self._parse_headers(settings.OTEL_EXPORTER_OTLP_HEADERS)
        self.metrics_exporter = settings.OTEL_METRICS_EXPORTER
        self.traces_exporter = settings.OTEL_TRACES_EXPORTER

        # Sampling configuration
        self.sampling_enabled = settings.TELEMETRY_SAMPLING_ENABLED
        self.default_sample_rate = settings.TELEMETRY_DEFAULT_SAMPLE_RATE
        self.error_sample_rate = settings.TELEMETRY_ERROR_SAMPLE_RATE
        self.high_priority_sample_rate = settings.TELEMETRY_HIGH_PRIORITY_SAMPLE_RATE
        self.llm_sample_rate = settings.TELEMETRY_LLM_SAMPLE_RATE
        self.webhook_sample_rate = settings.TELEMETRY_WEBHOOK_SAMPLE_RATE
        self.batch_size = settings.TELEMETRY_BATCH_SIZE
        self.export_timeout = settings.TELEMETRY_EXPORT_TIMEOUT

        # PII protection settings
        self.pii_scrubbing_enabled = settings.TELEMETRY_PII_SCRUBBING_ENABLED
        self.max_attribute_length = settings.TELEMETRY_MAX_ATTRIBUTE_LENGTH
        self.max_span_attributes = settings.TELEMETRY_MAX_SPAN_ATTRIBUTES
        self.scrub_user_data = settings.TELEMETRY_SCRUB_USER_DATA
        self.allowed_domains = settings.TELEMETRY_ALLOWED_DOMAINS

        # Performance settings
        self.async_export = settings.TELEMETRY_ASYNC_EXPORT
        self.max_export_batch_size = settings.TELEMETRY_MAX_EXPORT_BATCH_SIZE
        self.export_interval = settings.TELEMETRY_EXPORT_INTERVAL
        self.performance_overhead_limit = settings.TELEMETRY_PERFORMANCE_OVERHEAD_LIMIT

        # Environment context
        self.environment = settings.DEPLOY_ENV
        self.client_name = settings.CLIENT_NAME

    def _generate_instance_id(self) -> str:
        """Generate a unique instance ID if not provided."""
        import platform
        import uuid
        hostname = platform.node()
        unique_id = str(uuid.uuid4())[:8]
        return f"{hostname}-{unique_id}"

    def _parse_headers(self, headers_str: str) -> dict[str, str]:
        """Parse OTLP headers from environment variable."""
        if not headers_str:
            return {}
        headers = {}
        for header in headers_str.split(','):
            if '=' in header:
                key, value = header.split('=', 1)
                headers[key.strip()] = value.strip()
        return headers

    def get_sample_rate_for_operation(self, operation_type: str, is_error: bool = False) -> float:
        """Get sampling rate for operation type.

        Returns 1.0 (100%) for all operations to ensure complete QA coverage.
        Sampling is disabled during build/QA process for comprehensive bug detection.
        """
        # Always return 100% sampling for QA and bug remediation
        return 1.0

    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() in ['production', 'prod', 'live']

    def get_resource_attributes(self) -> dict[str, Any]:
        """Get OpenTelemetry resource attributes."""
        return {
            "service.name": self.service_name,
            "service.version": self.service_version,
            "service.instance.id": self.service_instance_id,
            "service.namespace": "imi",
            "deployment.environment": self.environment,
            "client.name": self.client_name,
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.language": "python",
            "telemetry.auto.version": "1.0.0",
        }

    def should_scrub_attribute(self, key: str, value: Any) -> bool:
        """Determine if an attribute should be scrubbed for PII protection."""
        if not self.pii_scrubbing_enabled:
            return False

        # List of sensitive attribute patterns
        sensitive_patterns = [
            'email', 'user_id', 'username', 'password', 'token', 'key',
            'secret', 'auth', 'session', 'cookie', 'ip', 'address',
            'phone', 'ssn', 'credit_card', 'api_key'
        ]

        key_lower = key.lower()
        return any(pattern in key_lower for pattern in sensitive_patterns)

    def sanitize_attribute_value(self, value: Any) -> Any:
        """Sanitize attribute value for safe export."""
        if isinstance(value, str):
            if len(value) > self.max_attribute_length:
                return value[:self.max_attribute_length] + "...[truncated]"

            # Basic PII patterns - emails, IPs, etc.
            import re
            if self.pii_scrubbing_enabled:
                # Scrub emails
                value = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', value)
                # Scrub IP addresses
                value = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]', value)
                # Scrub potential tokens (32+ character alphanumeric strings)
                value = re.sub(r'\b[A-Za-z0-9]{32,}\b', '[TOKEN]', value)

        return value


class Config:
    """Unified configuration class."""

    def __init__(self, settings: Settings):
        self.telemetry = TelemetryConfig(settings)
        self.settings = settings


def get_config() -> Config:
    """Get unified configuration instance."""
    return Config(settings)


# Create settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get settings instance."""
    return settings
