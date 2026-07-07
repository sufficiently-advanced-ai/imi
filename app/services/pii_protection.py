"""
PII Protection and Data Redaction System for Telemetry
Implements comprehensive data sanitization for production telemetry export
"""

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from re import Pattern
from typing import Any


class RedactionLevel(Enum):
    """Levels of data redaction."""
    NONE = "none"
    BASIC = "basic"
    STRICT = "strict"
    FULL = "full"


@dataclass
class RedactionRule:
    """Configuration for a specific redaction rule."""
    name: str
    pattern: Pattern[str]
    replacement: str
    priority: int = 0
    description: str = ""


class PIIDetector:
    """Advanced PII detection using pattern matching and heuristics."""

    def __init__(self, redaction_level: RedactionLevel = RedactionLevel.BASIC):
        self.redaction_level = redaction_level
        self._rules = self._build_redaction_rules()

    def _build_redaction_rules(self) -> list[RedactionRule]:
        """Build redaction rules based on redaction level."""
        rules = []

        # Basic rules (always applied)
        basic_rules = [
            RedactionRule(
                name="email",
                pattern=re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', re.IGNORECASE),
                replacement="[EMAIL]",
                priority=10,
                description="Email addresses"
            ),
            RedactionRule(
                name="ip_address",
                pattern=re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
                replacement="[IP]",
                priority=9,
                description="IPv4 addresses"
            ),
            RedactionRule(
                name="api_key",
                pattern=re.compile(r'\b[A-Za-z0-9]{32,}\b'),
                replacement="[TOKEN]",
                priority=8,
                description="API keys and tokens (32+ chars)"
            ),
            RedactionRule(
                name="phone",
                pattern=re.compile(r'(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}'),
                replacement="[PHONE]",
                priority=7,
                description="Phone numbers"
            ),
            RedactionRule(
                name="ssn",
                pattern=re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b'),
                replacement="[SSN]",
                priority=6,
                description="Social Security Numbers"
            ),
            RedactionRule(
                name="credit_card",
                pattern=re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'),
                replacement="[CARD]",
                priority=5,
                description="Credit card numbers"
            ),
        ]
        rules.extend(basic_rules)

        # Strict rules (applied for STRICT and FULL levels)
        if self.redaction_level in [RedactionLevel.STRICT, RedactionLevel.FULL]:
            strict_rules = [
                RedactionRule(
                    name="username",
                    pattern=re.compile(r'\busername["\s]*[:=]["\s]*([^"\s,}]+)', re.IGNORECASE),
                    replacement=r'username": "[USERNAME]"',
                    priority=4,
                    description="Username fields"
                ),
                RedactionRule(
                    name="user_id",
                    pattern=re.compile(r'\buser_?id["\s]*[:=]["\s]*([^"\s,}]+)', re.IGNORECASE),
                    replacement=r'user_id": "[USER_ID]"',
                    priority=4,
                    description="User ID fields"
                ),
                RedactionRule(
                    name="session_id",
                    pattern=re.compile(r'\bsession_?id["\s]*[:=]["\s]*([^"\s,}]+)', re.IGNORECASE),
                    replacement=r'session_id": "[SESSION]"',
                    priority=3,
                    description="Session identifiers"
                ),
                RedactionRule(
                    name="auth_token",
                    pattern=re.compile(r'\b(?:auth|bearer|token)["\s]*[:=]["\s]*([^"\s,}]+)', re.IGNORECASE),
                    replacement=r'token": "[AUTH_TOKEN]"',
                    priority=2,
                    description="Authentication tokens"
                ),
            ]
            rules.extend(strict_rules)

        # Full rules (applied only for FULL level)
        if self.redaction_level == RedactionLevel.FULL:
            full_rules = [
                RedactionRule(
                    name="url_params",
                    pattern=re.compile(r'([?&])([^=]+)=([^&\s]+)'),
                    replacement=r'\1\2=[PARAM]',
                    priority=1,
                    description="URL parameters"
                ),
                RedactionRule(
                    name="json_values",
                    pattern=re.compile(r'("(?:[^"\\]|\\.)*"):\s*("(?:[^"\\]|\\.)*")'),
                    replacement=r'\1: "[REDACTED]"',
                    priority=0,
                    description="JSON string values"
                ),
            ]
            rules.extend(full_rules)

        # Sort by priority (highest first)
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    def detect_pii_keys(self, key: str) -> bool:
        """Detect if a key name suggests PII content."""
        pii_patterns = [
            r'email', r'mail', r'e_mail',
            r'user_?id', r'userid', r'uid',
            r'username', r'user_?name', r'login',
            r'password', r'passwd', r'pwd',
            r'token', r'auth', r'authorization',
            r'session', r'sess_?id',
            r'api_?key', r'secret', r'private',
            r'phone', r'mobile', r'tel',
            r'address', r'addr', r'street',
            r'ssn', r'social_?security',
            r'credit_?card', r'card_?number',
            r'ip_?address', r'ip_?addr', r'remote_?addr',
            r'cookie', r'csrf',
            r'first_?name', r'last_?name', r'full_?name',
        ]

        key_lower = key.lower()
        return any(re.search(pattern, key_lower) for pattern in pii_patterns)

    def redact_text(self, text: str) -> str:
        """Apply redaction rules to text."""
        if not isinstance(text, str):
            return text

        redacted_text = text
        for rule in self._rules:
            redacted_text = rule.pattern.sub(rule.replacement, redacted_text)

        return redacted_text

    def sanitize_dict(self, data: dict[str, Any], max_depth: int = 10) -> dict[str, Any]:
        """Recursively sanitize dictionary data."""
        if max_depth <= 0:
            return {"error": "max_depth_exceeded"}

        sanitized = {}
        for key, value in data.items():
            # Check if key suggests PII
            if self.detect_pii_keys(key):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = self.sanitize_value(value, max_depth - 1)

        return sanitized

    def sanitize_value(self, value: Any, max_depth: int = 10) -> Any:
        """Sanitize individual value based on type."""
        if isinstance(value, str):
            return self.redact_text(value)
        elif isinstance(value, dict):
            return self.sanitize_dict(value, max_depth)
        elif isinstance(value, list):
            return [self.sanitize_value(item, max_depth) for item in value]
        elif isinstance(value, tuple):
            return tuple(self.sanitize_value(item, max_depth) for item in value)
        else:
            return value

    def generate_safe_hash(self, value: str, salt: str = "imi-telemetry") -> str:
        """Generate a safe, consistent hash for sensitive values."""
        if not isinstance(value, str):
            value = str(value)

        # Use SHA-256 with salt for consistent but unpredictable hashing
        hash_input = f"{salt}:{value}".encode()
        hash_digest = hashlib.sha256(hash_input).hexdigest()
        return f"hash:{hash_digest[:16]}"  # Use first 16 chars for readability


class TelemetryDataSanitizer:
    """Main sanitization class for telemetry data export."""

    def __init__(
        self,
        max_attribute_length: int = 1024,
        max_span_attributes: int = 128,
        redaction_level: RedactionLevel = RedactionLevel.BASIC,
        allowed_domains: list[str] = None,
    ):
        self.max_attribute_length = max_attribute_length
        self.max_span_attributes = max_span_attributes
        self.allowed_domains = set(allowed_domains or [])
        self.pii_detector = PIIDetector(redaction_level)

    def sanitize_span_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Sanitize span attributes for safe export."""
        if not attributes:
            return {}

        # First pass: sanitize values and detect PII keys
        sanitized_attrs = {}
        for key, value in attributes.items():
            # Check if key suggests PII
            if self.pii_detector.detect_pii_keys(key):
                sanitized_attrs[key] = "[REDACTED]"
                continue

            # Sanitize value
            sanitized_value = self._sanitize_attribute_value(key, value)

            # Truncate if too long
            if isinstance(sanitized_value, str) and len(sanitized_value) > self.max_attribute_length:
                sanitized_value = sanitized_value[:self.max_attribute_length] + "...[truncated]"

            sanitized_attrs[key] = sanitized_value

        # Second pass: limit attribute count
        if len(sanitized_attrs) > self.max_span_attributes:
            sanitized_attrs = self._prioritize_attributes(sanitized_attrs)

        return sanitized_attrs

    def _sanitize_attribute_value(self, key: str, value: Any) -> Any:
        """Sanitize individual attribute value."""
        if isinstance(value, str):
            # Check for URLs and handle domain filtering
            if self._is_url(value):
                return self._sanitize_url(value)
            else:
                return self.pii_detector.redact_text(value)

        elif isinstance(value, (dict, list, tuple)):
            return self.pii_detector.sanitize_value(value)

        elif isinstance(value, (int, float, bool)):
            return value  # Numeric/boolean values are generally safe

        else:
            # Convert to string and sanitize
            return self.pii_detector.redact_text(str(value))

    def _is_url(self, text: str) -> bool:
        """Check if text is a URL."""
        url_pattern = re.compile(
            r'^https?://',
            re.IGNORECASE
        )
        return bool(url_pattern.match(text))

    def _sanitize_url(self, url: str) -> str:
        """Sanitize URL while preserving structure for debugging."""
        try:
            from urllib.parse import parse_qs, urlencode, urlparse

            parsed = urlparse(url)

            # Check if domain is in allowed list
            if self.allowed_domains and parsed.netloc not in self.allowed_domains:
                return f"{parsed.scheme}://[EXTERNAL_DOMAIN]{parsed.path}"

            # Sanitize query parameters
            if parsed.query:
                query_params = parse_qs(parsed.query)
                sanitized_params = {}

                for param_key, param_values in query_params.items():
                    if self.pii_detector.detect_pii_keys(param_key):
                        sanitized_params[param_key] = ["[REDACTED]"]
                    else:
                        sanitized_values = []
                        for param_value in param_values:
                            sanitized_value = self.pii_detector.redact_text(param_value)
                            sanitized_values.append(sanitized_value)
                        sanitized_params[param_key] = sanitized_values

                sanitized_query = urlencode(sanitized_params, doseq=True)
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{sanitized_query}"

            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        except Exception:
            # If URL parsing fails, apply basic redaction
            return self.pii_detector.redact_text(url)

    def _prioritize_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Select most important attributes when count exceeds limit."""
        # Define priority patterns (higher priority = more important)
        priority_patterns = {
            r'http\.(method|status_code|route|url)': 100,
            r'operation\.(name|type)': 90,
            r'service\.(name|version)': 80,
            r'error(\.|_|$)': 70,
            r'exception(\.|_|$)': 70,
            r'duration(\.|_|$)': 60,
            r'user\.id': 50,
            r'request\.id': 50,
            r'trace\.id': 40,
            r'span\.id': 40,
            r'client\.(name|version)': 30,
        }

        # Calculate priority for each attribute
        attr_priorities = {}
        for key in attributes.keys():
            priority = 0
            for pattern, pattern_priority in priority_patterns.items():
                if re.search(pattern, key, re.IGNORECASE):
                    priority = max(priority, pattern_priority)
                    break

            attr_priorities[key] = priority

        # Sort by priority and keep only top attributes
        sorted_attrs = sorted(
            attributes.items(),
            key=lambda item: attr_priorities[item[0]],
            reverse=True
        )

        return dict(sorted_attrs[:self.max_span_attributes])

    def sanitize_metric_attributes(self, attributes: dict[str, str]) -> dict[str, str]:
        """Sanitize metric label attributes."""
        if not attributes:
            return {}

        sanitized = {}
        for key, value in attributes.items():
            if self.pii_detector.detect_pii_keys(key):
                # For metrics, we often want to preserve structure but hide values
                sanitized[key] = self.pii_detector.generate_safe_hash(value)
            else:
                sanitized_value = self.pii_detector.redact_text(str(value))
                # Keep metric labels short
                if len(sanitized_value) > 100:
                    sanitized_value = sanitized_value[:100] + "..."
                sanitized[key] = sanitized_value

        return sanitized

    def create_sanitization_report(self, original_data: dict, sanitized_data: dict) -> dict[str, Any]:
        """Create a report of sanitization actions taken."""
        report = {
            "original_attributes": len(original_data) if isinstance(original_data, dict) else 0,
            "sanitized_attributes": len(sanitized_data) if isinstance(sanitized_data, dict) else 0,
            "redacted_keys": [],
            "truncated_values": 0,
            "sanitization_level": self.pii_detector.redaction_level.value,
        }

        if isinstance(original_data, dict) and isinstance(sanitized_data, dict):
            for key, original_value in original_data.items():
                sanitized_value = sanitized_data.get(key)

                if sanitized_value == "[REDACTED]":
                    report["redacted_keys"].append(key)
                elif (isinstance(original_value, str) and
                      isinstance(sanitized_value, str) and
                      sanitized_value.endswith("...[truncated]")):
                    report["truncated_values"] += 1

        return report


def create_sanitizer_from_config(telemetry_config) -> TelemetryDataSanitizer:
    """Create sanitizer instance from telemetry configuration."""
    redaction_level = RedactionLevel.BASIC

    if telemetry_config.is_production():
        redaction_level = RedactionLevel.STRICT
    elif telemetry_config.scrub_user_data:
        redaction_level = RedactionLevel.STRICT

    return TelemetryDataSanitizer(
        max_attribute_length=telemetry_config.max_attribute_length,
        max_span_attributes=telemetry_config.max_span_attributes,
        redaction_level=redaction_level,
        allowed_domains=telemetry_config.allowed_domains,
    )
