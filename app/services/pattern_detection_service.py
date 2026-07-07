"""
Pattern detection service for identifying domain-specific patterns in content.

This service analyzes conversations and documents to detect patterns like:
- Escalation indicators
- Decision points
- Risk signals
- Opportunity windows
"""

import logging
from datetime import datetime

from app.git_ops import git_ops
from app.model_schemas.domain_config import DomainConfiguration
from app.models import PatternAnalysis
from app.services.claude_client import ClaudeClient

logger = logging.getLogger(__name__)


class PatternDetectionService:
    """Service for detecting domain-specific patterns in content"""

    # Default patterns for fallback
    DEFAULT_PATTERNS = [
        {
            "name": "escalation_risk",
            "description": "Signs of growing tension, frustration, or conflict escalation",
        },
        {
            "name": "decision_delay",
            "description": "Indicators of indecision, postponement, or analysis paralysis",
        },
        {
            "name": "opportunity_signal",
            "description": "Mentions of new possibilities, expansions, or positive developments",
        },
        {
            "name": "action_item_overload",
            "description": "Too many commitments or unrealistic task assignments",
        },
    ]

    def __init__(self, claude_client: ClaudeClient):
        """Initialize with Claude client for pattern analysis"""
        self.claude_client = claude_client
        self.git_ops = git_ops

    def build_pattern_prompt(self, patterns: list[dict[str, str]]) -> str:
        """Build analysis prompt with domain-specific pattern context"""
        pattern_descriptions = []
        for pattern in patterns:
            pattern_descriptions.append(
                f"- {pattern['name']}: {pattern['description']}"
            )

        # Fix: Move the join operation outside the f-string
        pattern_list = "\n".join(pattern_descriptions)
        return f"""Analyze this conversation for the following patterns:
{pattern_list}

For each pattern detected, provide:
- Pattern name
- Confidence level (high/medium/low)
- Supporting evidence from the conversation

Respond in JSON format:
{{
    "patterns_detected": [
        {{
            "pattern_name": "pattern_name_here",
            "confidence": "high/medium/low",
            "evidence": ["quote 1", "quote 2", "..."]
        }}
    ]
}}"""

    async def detect_patterns(
        self, content: str, domain_config: DomainConfiguration
    ) -> PatternAnalysis:
        """Detect patterns from content using domain configuration"""
        # Extract all patterns from domain config
        patterns = []

        # Check for patterns in entity types
        if hasattr(domain_config, "entity_types") and domain_config.entity_types:
            for entity_type in domain_config.entity_types:
                if hasattr(entity_type, "patterns") and entity_type.patterns:
                    patterns.extend(entity_type.patterns)

        # Check for domain-level patterns
        if hasattr(domain_config, "patterns") and domain_config.patterns:
            patterns.extend(domain_config.patterns)

        # Check for intelligence_patterns (test fixture format)
        elif hasattr(domain_config, "intelligence_patterns") and domain_config.intelligence_patterns:
            for category, pattern_list in domain_config.intelligence_patterns.items():
                for pattern in pattern_list:
                    patterns.append({
                        "name": pattern["name"],
                        "description": pattern["description"],
                        "category": category,
                    })

        # Fall back to default patterns if none configured
        if not patterns:
            logger.info(
                f"No patterns configured for domain {domain_config.id}, using defaults"
            )
            patterns = self.DEFAULT_PATTERNS

        # Build prompt with domain patterns
        prompt = self.build_pattern_prompt(patterns)

        try:
            # Analyze content for patterns
            response = await self.claude_client.generate_response(
                prompt=prompt,
                context=content,
                temperature=0.3,  # Lower temperature for consistent pattern detection
            )

            # Parse JSON response
            import json

            result = json.loads(response)

            # Convert to PatternAnalysis model
            detected_patterns = []
            for pattern in result.get("patterns_detected", []):
                detected_patterns.append(
                    {
                        "pattern_name": pattern["pattern_name"],
                        "confidence": pattern["confidence"],
                        "evidence": pattern["evidence"],
                        "timestamp": datetime.now().isoformat(),
                    }
                )

            return PatternAnalysis(
                patterns_detected=detected_patterns,
                domain_id=domain_config.id,
                analysis_timestamp=datetime.now(),
            )

        except Exception as e:
            logger.error(f"Error detecting patterns: {e}")
            # Return empty analysis on error
            return PatternAnalysis(
                patterns_detected=[],
                domain_id=domain_config.id,
                analysis_timestamp=datetime.now(),
            )

    async def log_patterns_to_meeting(
        self, meeting_id: str, patterns: PatternAnalysis
    ) -> None:
        """Log detected patterns to meeting state file"""
        try:
            # Construct meeting state file path
            state_file = (
                f"meetings/{meeting_id[:4]}/{meeting_id[4:6]}/state-{meeting_id}.json"
            )

            # Read existing state
            import json

            existing_state = {}
            try:
                content = await self.git_ops.get_file_content(state_file)
                existing_state = json.loads(content)
            except Exception:
                # File doesn't exist yet, that's OK
                pass

            # Add patterns to state
            if "pattern_analysis" not in existing_state:
                existing_state["pattern_analysis"] = []

            existing_state["pattern_analysis"].append(
                {
                    "timestamp": patterns.analysis_timestamp.isoformat(),
                    "domain_id": patterns.domain_id,
                    "patterns": patterns.patterns_detected,
                }
            )

            # Write updated state
            await self.git_ops.update_file(
                file_path=state_file,
                content=json.dumps(existing_state, indent=2),
                commit_message=f"Add pattern analysis for meeting {meeting_id}",
            )

            logger.info(
                f"Logged {len(patterns.patterns_detected)} patterns to meeting {meeting_id}"
            )

        except Exception as e:
            logger.error(f"Error logging patterns to meeting {meeting_id}: {e}")
