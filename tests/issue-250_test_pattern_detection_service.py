"""
Tests for Issue #250: Create LLM-Based Pattern Detection
Tests the PatternDetectionService for domain-aware pattern detection and logging
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch
import json

from app.services.pattern_detection_service import PatternDetectionService
from app.models import PatternAnalysis
from app.model_schemas.domain_config import DomainConfiguration
from app.services.claude_client import ClaudeClient
from app.git_ops import GitOperations


class TestPatternDetectionService:
    """Test suite for Pattern Detection Service"""

    @pytest.fixture
    def mock_claude_client(self):
        """Create a mock Claude client"""
        client = Mock(spec=ClaudeClient)
        client.generate_response = AsyncMock()
        return client

    @pytest.fixture
    def mock_git_ops(self):
        """Create a mock GitOps instance"""
        git_ops = Mock(spec=GitOperations)
        git_ops.add_and_commit = AsyncMock()
        git_ops.pull_latest = AsyncMock()
        return git_ops

    @pytest.fixture
    def sample_domain_config(self):
        """Create a sample domain configuration with LLM-friendly patterns"""
        from types import SimpleNamespace

        # Create a simple mock with the required attributes
        return SimpleNamespace(
            id="consulting_firm",
            name="Consulting Firm",
            version="1.0.0",
            entities={},
            intelligence_patterns={
                "risk_detection": [
                    {
                        "name": "scope_creep",
                        "description": "Project requirements expanding beyond original scope - look for timeline extensions, budget increases, additional features mentioned",
                        "priority": "high",
                        "triggers": [],  # Empty triggers for LLM-based detection
                        "actions": [],
                    },
                    {
                        "name": "resource_conflicts",
                        "description": "Team members overcommitted or unavailable - watch for scheduling conflicts, workload concerns, availability issues",
                        "priority": "medium",
                        "triggers": [],
                        "actions": [],
                    },
                ],
                "opportunity_detection": [
                    {
                        "name": "upsell_opportunity",
                        "description": "Client expressing interest in additional services or expanded scope - mentions of other projects, questions about additional capabilities",
                        "priority": "medium",
                        "triggers": [],
                        "actions": [],
                    }
                ],
            },
        )

    @pytest.fixture
    def pattern_detection_service(self, mock_claude_client, mock_git_ops):
        """Create a PatternDetectionService instance"""
        return PatternDetectionService(mock_claude_client)

    @pytest.mark.asyncio
    async def test_build_pattern_prompt(self, pattern_detection_service, sample_domain_config):
        """Test building pattern detection prompts from domain configuration"""
        patterns = []
        for category, pattern_list in sample_domain_config.intelligence_patterns.items():
            for pattern in pattern_list:
                patterns.append({
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "category": category,
                })

        prompt = pattern_detection_service.build_pattern_prompt(patterns)

        assert "Analyze this conversation for the following patterns:" in prompt
        assert "scope_creep: Project requirements expanding beyond original scope" in prompt
        assert "resource_conflicts: Team members overcommitted or unavailable" in prompt
        assert "upsell_opportunity: Client expressing interest in additional services" in prompt
        assert "For each pattern detected, provide:" in prompt
        assert "Pattern name" in prompt
        assert "Confidence level (high/medium/low)" in prompt
        assert "Supporting evidence from the conversation" in prompt

    @pytest.mark.asyncio
    async def test_detect_patterns_in_conversation(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test pattern detection from conversation content"""
        conversation_content = """
        John: The client mentioned they want to add a new reporting module to the project.
        Sarah: That wasn't in the original scope. We'll need to extend the timeline by 2 weeks.
        John: And increase the budget by $20,000. Also, Mike is already overbooked with the Alpha project.
        Sarah: Yes, and Jane mentioned she has three other deadlines this week.
        """

        # Mock Claude response
        mock_payload = {
            "patterns_detected": [
                {
                    "pattern_name": "scope_creep",
                    "confidence": "high",
                    "evidence": [
                        "Client mentioned they want to add a new reporting module",
                        "That wasn't in the original scope",
                        "Need to extend the timeline by 2 weeks",
                        "Increase the budget by $20,000",
                    ],
                },
                {
                    "pattern_name": "resource_conflicts",
                    "confidence": "medium",
                    "evidence": [
                        "Mike is already overbooked with the Alpha project",
                        "Jane mentioned she has three other deadlines this week",
                    ],
                },
            ]
        }
        mock_claude_client.generate_response.return_value = json.dumps(mock_payload)

        analysis = await pattern_detection_service.detect_patterns(
            conversation_content, sample_domain_config
        )

        assert isinstance(analysis, PatternAnalysis)
        assert len(analysis.patterns_detected) == 2
        assert analysis.patterns_detected[0]["pattern_name"] == "scope_creep"
        assert analysis.patterns_detected[0]["confidence"] == "high"
        assert len(analysis.patterns_detected[0]["evidence"]) == 4
        assert analysis.patterns_detected[1]["pattern_name"] == "resource_conflicts"
        assert analysis.patterns_detected[1]["confidence"] == "medium"

    @pytest.mark.asyncio
    async def test_detect_patterns_in_document(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test pattern detection from document content"""
        document_content = """
        # Project Status Update
        
        The client has expressed interest in our AI consulting services and asked about 
        our capabilities in machine learning model deployment. They mentioned they have 
        several other divisions that might benefit from similar solutions.
        
        During the meeting, they specifically asked about:
        - Custom model development
        - MLOps infrastructure setup
        - Ongoing model monitoring services
        """

        # Mock Claude response
        mock_payload = {
            "patterns_detected": [
                {
                    "pattern_name": "upsell_opportunity",
                    "confidence": "high",
                    "evidence": [
                        "Client has expressed interest in our AI consulting services",
                        "Asked about our capabilities in machine learning model deployment",
                        "They mentioned they have several other divisions that might benefit",
                        "Asked about custom model development, MLOps infrastructure, and monitoring services",
                    ],
                }
            ]
        }
        mock_claude_client.generate_response.return_value = json.dumps(mock_payload)

        analysis = await pattern_detection_service.detect_patterns(
            document_content, sample_domain_config
        )

        assert isinstance(analysis, PatternAnalysis)
        assert len(analysis.patterns_detected) == 1
        assert analysis.patterns_detected[0]["pattern_name"] == "upsell_opportunity"
        assert analysis.patterns_detected[0]["confidence"] == "high"


    @pytest.mark.asyncio
    async def test_pattern_detection_with_no_patterns_found(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test pattern detection when no patterns are found"""
        conversation_content = "Just a regular status update with no significant patterns."

        # Mock Claude response with no patterns
        mock_claude_client.generate_response.return_value = json.dumps({"patterns_detected": []})

        analysis = await pattern_detection_service.detect_patterns(
            conversation_content, sample_domain_config
        )

        assert isinstance(analysis, PatternAnalysis)
        assert analysis.patterns_detected == []

    @pytest.mark.asyncio
    async def test_pattern_detection_error_handling(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test error handling in pattern detection"""
        conversation_content = "Test content"

        # Mock Claude to raise an exception
        mock_claude_client.generate_response.side_effect = Exception("API Error")
        analysis = await pattern_detection_service.detect_patterns(
            conversation_content, sample_domain_config
        )
        assert isinstance(analysis, PatternAnalysis)
        assert analysis.patterns_detected == []

    @pytest.mark.asyncio
    async def test_integration_with_conversation_processing(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test pattern detection integration with conversation processing pipeline"""
        # This tests that the service can be called from the conversation processing flow
        conversation_data = {
            "bot_id": "bot123",
            "content": "Client mentioned they need additional features beyond original scope and timeline issues.",
            "meeting_state_file": "meeting_states/2025-01-15-sync.md",
        }

        mock_payload = {
            "patterns_detected": [
                {
                    "pattern_name": "scope_creep",
                    "confidence": "high",
                    "evidence": ["Client mentioned they need additional features beyond original scope"],
                }
            ]
        }
        mock_claude_client.generate_response.return_value = json.dumps(mock_payload)

        # Process conversation with pattern detection
        analysis = await pattern_detection_service.detect_patterns(
            conversation_data["content"], sample_domain_config
        )

        assert len(analysis.patterns_detected) == 1
        assert analysis.patterns_detected[0]["pattern_name"] == "scope_creep"

    @pytest.mark.asyncio
    async def test_integration_with_git_webhook_processing(
        self, pattern_detection_service, sample_domain_config, mock_claude_client
    ):
        """Test pattern detection integration with git webhook processing"""
        # This tests that the service can be called from the webhook processing flow
        document_data = {
            "file_path": "projects/alpha-project/status.md",
            "content": "Project facing resource allocation issues. Team members are overbooked.",
            "commit_sha": "abc123",
        }

        mock_payload = {
            "patterns_detected": [
                {
                    "pattern_name": "resource_conflicts",
                    "confidence": "high",
                    "evidence": [
                        "Project facing resource allocation issues",
                        "Team members are overbooked",
                    ],
                }
            ]
        }
        mock_claude_client.generate_response.return_value = json.dumps(mock_payload)

        # Process document with pattern detection
        analysis = await pattern_detection_service.detect_patterns(
            document_data["content"], sample_domain_config
        )

        assert len(analysis.patterns_detected) == 1
        assert analysis.patterns_detected[0]["pattern_name"] == "resource_conflicts"