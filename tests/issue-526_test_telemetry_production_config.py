"""
Tests for Production Telemetry Configuration (Issue #526)
Tests intelligent sampling, PII protection, and performance optimization
"""

import pytest
import os
import time
import json
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

# Import the telemetry components we're testing
from app.config import TelemetryConfig, Settings
from app.services.telemetry_manager import (
    TelemetryManager,
    IntelligentSampler,
    PIIProtectionSpanProcessor,
    telemetry_manager,
    initialize_telemetry
)
from app.services.pii_protection import (
    TelemetryDataSanitizer,
    PIIDetector,
    RedactionLevel,
    create_sanitizer_from_config
)


@pytest.fixture
def telemetry_config():
    """Create a test telemetry configuration."""
    settings = Settings(
        OTEL_ENABLED=True,
        OTEL_SERVICE_NAME="kb-llm-test",
        OTEL_SERVICE_VERSION="1.0.0-test",
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
        TELEMETRY_SAMPLING_ENABLED=True,
        TELEMETRY_DEFAULT_SAMPLE_RATE=0.1,
        TELEMETRY_ERROR_SAMPLE_RATE=1.0,
        TELEMETRY_LLM_SAMPLE_RATE=0.2,
        TELEMETRY_WEBHOOK_SAMPLE_RATE=0.5,
        TELEMETRY_PII_SCRUBBING_ENABLED=True,
        TELEMETRY_MAX_ATTRIBUTE_LENGTH=1024,
        TELEMETRY_MAX_SPAN_ATTRIBUTES=128,
        TELEMETRY_SCRUB_USER_DATA=True,
        TELEMETRY_ALLOWED_DOMAINS=["example.com", "api.example.com"],
        DEPLOY_ENV="test",
        CLIENT_NAME="test-client"
    )
    return TelemetryConfig(settings)


@pytest.fixture
def pii_detector():
    """Create a PII detector for testing."""
    return PIIDetector(RedactionLevel.STRICT)


@pytest.fixture
def data_sanitizer(telemetry_config):
    """Create a data sanitizer for testing."""
    return create_sanitizer_from_config(telemetry_config)


class TestTelemetryConfiguration:
    """Test telemetry configuration parsing and validation."""

    def test_telemetry_config_initialization(self, telemetry_config):
        """Test that telemetry config initializes correctly."""
        assert telemetry_config.enabled is True
        assert telemetry_config.service_name == "kb-llm-test"
        assert telemetry_config.default_sample_rate == 0.1
        assert telemetry_config.error_sample_rate == 1.0
        assert telemetry_config.pii_scrubbing_enabled is True


    def test_production_detection(self, telemetry_config):
        """Test production environment detection."""
        # Test environment is 'test', not production
        assert not telemetry_config.is_production()

        # Test production detection
        telemetry_config.environment = "production"
        assert telemetry_config.is_production()

    def test_resource_attributes(self, telemetry_config):
        """Test OpenTelemetry resource attributes generation."""
        attrs = telemetry_config.get_resource_attributes()

        assert attrs["service.name"] == "kb-llm-test"
        assert attrs["service.version"] == "1.0.0-test"
        assert attrs["deployment.environment"] == "test"
        assert attrs["client.name"] == "test-client"
        assert "service.instance.id" in attrs

    def test_instance_id_generation(self, telemetry_config):
        """Test unique instance ID generation."""
        instance_id = telemetry_config.service_instance_id
        assert instance_id is not None
        assert len(instance_id) > 8  # Should include hostname + UUID fragment


class TestIntelligentSampler:
    """Test intelligent sampling strategy."""

    def test_sampler_initialization(self, telemetry_config):
        """Test sampler initializes with correct configuration."""
        sampler = IntelligentSampler(telemetry_config)
        assert sampler.config == telemetry_config

    def test_operation_type_extraction(self, telemetry_config):
        """Test operation type extraction from span names."""
        sampler = IntelligentSampler(telemetry_config)

        # Test LLM operations
        assert sampler._extract_operation_type("claude_chat_request", {}) == "llm"
        assert sampler._extract_operation_type("anthropic_api_call", {}) == "llm"

        # Test webhook operations
        assert sampler._extract_operation_type("github_webhook_processing", {}) == "webhook"
        assert sampler._extract_operation_type("webhook_handler", {}) == "webhook"


        # Test high priority operations
        assert sampler._extract_operation_type("critical_system_check", {}) == "high_priority"

        # Test API operations
        assert sampler._extract_operation_type("http_request", {"http.route": "/api/test"}) == "api"

        # Test default
        assert sampler._extract_operation_type("some_other_operation", {}) == "default"

    def test_error_span_detection(self, telemetry_config):
        """Test error span detection."""
        sampler = IntelligentSampler(telemetry_config)

        # Test HTTP error status
        assert sampler._is_error_span({"http.status_code": "500"}) is True
        assert sampler._is_error_span({"http.status_code": "404"}) is True
        assert sampler._is_error_span({"http.status_code": "200"}) is False

        # Test error attributes
        assert sampler._is_error_span({"error": "true"}) is True
        assert sampler._is_error_span({"exception.type": "ValueError"}) is True
        assert sampler._is_error_span({"operation.failed": "timeout"}) is True

    def test_sampling_consistency(self, telemetry_config):
        """Test that same trace ID gets consistent sampling decision."""
        sampler = IntelligentSampler(telemetry_config)
        trace_id = 12345

        # Make multiple sampling decisions for same trace
        result1 = sampler.should_sample(None, trace_id, "test_span")
        result2 = sampler.should_sample(None, trace_id, "another_span")

        # Should get same decision
        assert result1.decision == result2.decision

    def test_error_first_sampling(self, telemetry_config):
        """Test that error spans are always sampled."""
        sampler = IntelligentSampler(telemetry_config)

        # Error span should always be sampled
        result = sampler.should_sample(
            None, 12345, "error_span",
            attributes={"http.status_code": "500"}
        )
        assert result.decision.name in ["RECORD_AND_SAMPLE", "RECORD"]

    def test_sampling_statistics(self, telemetry_config):
        """Test sampling statistics collection."""
        sampler = IntelligentSampler(telemetry_config)

        # Generate some sampling decisions
        for i in range(100):
            sampler.should_sample(None, i, f"span_{i}")

        stats = sampler.get_stats()
        assert stats["total_requests"] == 100
        assert "sample_rate" in stats
        assert "operation_stats" in stats


class TestPIIProtection:
    """Test PII protection and data redaction."""

    def test_pii_key_detection(self, pii_detector):
        """Test PII key detection."""
        # Should detect PII keys
        assert pii_detector.detect_pii_keys("email") is True
        assert pii_detector.detect_pii_keys("user_id") is True
        assert pii_detector.detect_pii_keys("password") is True
        assert pii_detector.detect_pii_keys("api_key") is True
        assert pii_detector.detect_pii_keys("session_token") is True

        # Should not detect non-PII keys
        assert pii_detector.detect_pii_keys("operation_name") is False
        assert pii_detector.detect_pii_keys("duration") is False
        assert pii_detector.detect_pii_keys("count") is False

    def test_text_redaction(self, pii_detector):
        """Test text-based PII redaction."""
        # Test email redaction
        text = "Contact us at support@example.com for help"
        redacted = pii_detector.redact_text(text)
        assert "[EMAIL]" in redacted
        assert "support@example.com" not in redacted

        # Test IP address redaction
        text = "Request from 192.168.1.100 failed"
        redacted = pii_detector.redact_text(text)
        assert "[IP]" in redacted
        assert "192.168.1.100" not in redacted

        # Test token redaction
        text = "API key: abc123def456ghi789jklmnop987654321"
        redacted = pii_detector.redact_text(text)
        assert "[TOKEN]" in redacted

    def test_dict_sanitization(self, pii_detector):
        """Test dictionary data sanitization."""
        test_data = {
            "email": "user@example.com",
            "user_id": "12345",
            "operation": "test_operation",
            "ip_address": "192.168.1.1",
            "nested": {
                "password": "secret123",
                "data": "normal_data"
            }
        }

        sanitized = pii_detector.sanitize_dict(test_data)

        # PII keys should be redacted
        assert sanitized["email"] == "[REDACTED]"
        assert sanitized["user_id"] == "[REDACTED]"
        assert sanitized["ip_address"] == "[REDACTED]"

        # Non-PII data should be preserved (but may have text redaction)
        assert "operation" in sanitized

        # Nested data should be handled
        assert sanitized["nested"]["password"] == "[REDACTED]"

    def test_span_attribute_sanitization(self, data_sanitizer):
        """Test span attribute sanitization."""
        attributes = {
            "http.method": "POST",
            "http.url": "https://example.com/api/users?email=test@example.com",
            "user.email": "sensitive@example.com",
            "operation.name": "create_user",
            "request.body": "This is a very long request body that exceeds the maximum length limit and should be truncated by the sanitizer to prevent excessive memory usage and improve export performance." * 10
        }

        sanitized = data_sanitizer.sanitize_span_attributes(attributes)

        # HTTP method should be preserved
        assert sanitized["http.method"] == "POST"

        # Sensitive attributes should be redacted
        assert sanitized["user.email"] == "[REDACTED]"

        # URLs should be sanitized
        assert "[EMAIL]" in sanitized["http.url"] or "example.com" in sanitized["http.url"]

        # Long values should be truncated
        assert len(sanitized["request.body"]) <= data_sanitizer.max_attribute_length + 20  # Allow for truncation marker

    def test_metric_attribute_sanitization(self, data_sanitizer):
        """Test metric attribute sanitization."""
        attributes = {
            "operation": "llm_request",
            "user_id": "sensitive_user_123",
            "endpoint": "/api/chat",
            "status": "success"
        }

        sanitized = data_sanitizer.sanitize_metric_attributes(attributes)

        # Non-sensitive attributes should be preserved
        assert sanitized["operation"] == "llm_request"
        assert sanitized["endpoint"] == "/api/chat"
        assert sanitized["status"] == "success"

        # Sensitive attributes should be hashed
        assert sanitized["user_id"].startswith("hash:")

    def test_attribute_prioritization(self, data_sanitizer):
        """Test attribute prioritization when count exceeds limit."""
        # Create more attributes than allowed
        attributes = {f"attr_{i}": f"value_{i}" for i in range(200)}

        # Add important attributes
        attributes.update({
            "http.method": "GET",
            "http.status_code": "200",
            "operation.name": "test_op",
            "error": "false",
            "duration": "100ms"
        })

        sanitized = data_sanitizer.sanitize_span_attributes(attributes)

        # Should not exceed limit
        assert len(sanitized) <= data_sanitizer.max_span_attributes

        # Important attributes should be preserved
        assert "http.method" in sanitized
        assert "http.status_code" in sanitized
        assert "operation.name" in sanitized


class TestPerformanceOptimization:
    """Test performance optimization features."""

    @patch('app.services.telemetry_manager.trace')
    @patch('app.services.telemetry_manager.metrics')
    def test_telemetry_manager_initialization(self, mock_metrics, mock_trace, telemetry_config):
        """Test telemetry manager initialization."""
        manager = TelemetryManager()

        # Mock the dependencies
        with patch.object(manager, 'config', telemetry_config):
            manager.initialize()

        assert manager._initialized is True

    def test_performance_tracking(self, telemetry_config):
        """Test performance overhead tracking."""
        manager = TelemetryManager()
        manager.config = telemetry_config

        # Test performance tracking context manager
        with manager.performance_tracking("test_operation"):
            time.sleep(0.001)  # Small delay to measure

        # Check that performance metrics were recorded
        assert manager._performance_metrics.sample_count > 0
        assert manager._performance_metrics.total_overhead_ns > 0

    def test_sampling_decision_performance(self, telemetry_config):
        """Test that sampling decisions are fast."""
        sampler = IntelligentSampler(telemetry_config)

        start_time = time.perf_counter()

        # Make many sampling decisions
        for i in range(1000):
            sampler.should_sample(None, i, f"span_{i}")

        end_time = time.perf_counter()
        total_time = end_time - start_time

        # Should complete 1000 decisions in under 100ms
        assert total_time < 0.1

    def test_pii_sanitization_performance(self, data_sanitizer):
        """Test PII sanitization performance."""
        # Create large attribute set
        attributes = {
            f"key_{i}": f"This is a test value with potential email@example.com and IP 192.168.1.{i % 255}"
            for i in range(100)
        }

        start_time = time.perf_counter()
        sanitized = data_sanitizer.sanitize_span_attributes(attributes)
        end_time = time.perf_counter()

        sanitization_time = end_time - start_time

        # Should complete sanitization in under 50ms
        assert sanitization_time < 0.05
        assert len(sanitized) > 0


class TestIntegrationScenarios:
    """Test integration scenarios and end-to-end functionality."""

    def test_production_configuration_validation(self):
        """Test production configuration validation."""
        # Test production environment variables
        prod_settings = Settings(
            OTEL_ENABLED=True,
            TELEMETRY_SAMPLING_ENABLED=True,
            TELEMETRY_DEFAULT_SAMPLE_RATE=0.1,
            TELEMETRY_ERROR_SAMPLE_RATE=1.0,
            TELEMETRY_PII_SCRUBBING_ENABLED=True,
            TELEMETRY_SCRUB_USER_DATA=True,
            DEPLOY_ENV="production"
        )

        config = TelemetryConfig(prod_settings)

        # Validate production settings
        assert config.enabled is True
        assert config.sampling_enabled is True
        assert config.default_sample_rate == 0.1  # 10% sampling
        assert config.error_sample_rate == 1.0    # 100% error sampling
        assert config.pii_scrubbing_enabled is True
        assert config.is_production() is True

    def test_development_configuration_validation(self):
        """Test development configuration validation."""
        dev_settings = Settings(
            OTEL_ENABLED=True,
            TELEMETRY_SAMPLING_ENABLED=True,
            TELEMETRY_DEFAULT_SAMPLE_RATE=1.0,  # 100% for debugging
            TELEMETRY_PII_SCRUBBING_ENABLED=True,
            TELEMETRY_SCRUB_USER_DATA=False,    # Allow user data in dev
            DEPLOY_ENV="development"
        )

        config = TelemetryConfig(dev_settings)

        # Validate development settings
        assert config.enabled is True
        assert config.default_sample_rate == 1.0  # 100% sampling for debugging
        assert config.scrub_user_data is False    # Less aggressive redaction
        assert not config.is_production()

    def test_error_trace_retention(self, telemetry_config):
        """Test that error traces are retained with 100% sampling."""
        sampler = IntelligentSampler(telemetry_config)

        # Test multiple error scenarios
        error_scenarios = [
            {"http.status_code": "500"},
            {"http.status_code": "404"},
            {"error": "true"},
            {"exception.type": "ValueError"},
            {"operation.failed": "timeout"}
        ]

        for error_attrs in error_scenarios:
            result = sampler.should_sample(None, 12345, "error_span", attributes=error_attrs)
            # Error spans should always be sampled
            assert result.decision.name in ["RECORD_AND_SAMPLE", "RECORD"]


    def test_sanitization_report_generation(self, data_sanitizer):
        """Test sanitization reporting functionality."""
        original_data = {
            "email": "user@example.com",
            "operation": "test",
            "long_value": "x" * 2000,  # Will be truncated
            "normal_value": "ok"
        }

        sanitized_data = data_sanitizer.sanitize_span_attributes(original_data)
        report = data_sanitizer.create_sanitization_report(original_data, sanitized_data)

        assert "original_attributes" in report
        assert "sanitized_attributes" in report
        assert "redacted_keys" in report
        assert "truncated_values" in report
        assert "email" in report["redacted_keys"]


class TestConfigurationValidation:
    """Test configuration validation and error handling."""

    def test_invalid_sample_rates(self):
        """Test handling of invalid sample rates."""
        # Test negative sample rate
        settings = Settings(TELEMETRY_DEFAULT_SAMPLE_RATE=-0.1)
        config = TelemetryConfig(settings)

        # Should handle gracefully and provide valid rate
        rate = config.get_sample_rate_for_operation("test", False)
        assert 0 <= rate <= 1

    def test_missing_configuration(self):
        """Test handling of missing configuration values."""
        minimal_settings = Settings()
        config = TelemetryConfig(minimal_settings)

        # Should have reasonable defaults
        assert config.service_name is not None
        assert config.default_sample_rate >= 0
        assert config.error_sample_rate >= 0

    def test_environment_variable_parsing(self):
        """Test environment variable parsing."""
        # Test comma-separated domains
        settings = Settings(TELEMETRY_ALLOWED_DOMAINS="example.com,api.example.com,*.internal.com")
        config = TelemetryConfig(settings)

        assert "example.com" in config.allowed_domains
        assert "api.example.com" in config.allowed_domains
        assert "*.internal.com" in config.allowed_domains

    def test_header_parsing(self):
        """Test OTLP header parsing."""
        settings = Settings(OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer token123,x-api-key=key456")
        config = TelemetryConfig(settings)

        headers = config.headers
        assert headers.get("authorization") == "Bearer token123"
        assert headers.get("x-api-key") == "key456"


# Integration test with actual telemetry manager
class TestTelemetryManagerIntegration:
    """Test full telemetry manager integration."""

    @pytest.fixture(autouse=True)
    def setup_test_environment(self):
        """Set up test environment variables."""
        os.environ.update({
            "OTEL_ENABLED": "true",
            "OTEL_SERVICE_NAME": "kb-llm-test",
            "TELEMETRY_SAMPLING_ENABLED": "true",
            "TELEMETRY_DEFAULT_SAMPLE_RATE": "0.1",
            "TELEMETRY_ERROR_SAMPLE_RATE": "1.0",
            "DEPLOY_ENV": "test"
        })
        yield
        # Cleanup after test
        for key in ["OTEL_ENABLED", "OTEL_SERVICE_NAME", "TELEMETRY_SAMPLING_ENABLED"]:
            os.environ.pop(key, None)

    @patch('app.services.telemetry_manager.trace')
    @patch('app.services.telemetry_manager.metrics')
    def test_telemetry_manager_health_check(self, mock_metrics, mock_trace):
        """Test telemetry manager health check functionality."""
        # Initialize telemetry
        initialize_telemetry()

        # Get health statistics
        stats = telemetry_manager.get_telemetry_stats()

        assert "enabled" in stats
        assert "initialized" in stats
        assert "environment" in stats
        assert "service_name" in stats

    def test_span_creation_with_pii_protection(self):
        """Test span creation with PII protection."""
        # Test data with PII
        test_attributes = {
            "http.method": "POST",
            "user.email": "test@example.com",
            "operation": "user_creation",
            "request.ip": "192.168.1.100"
        }

        # Create span (will be non-recording in test environment)
        span = telemetry_manager.create_span("test_operation", "api", test_attributes)

        # Span should be created successfully
        assert span is not None