"""
Test Agent Framework - Tests for agent base classes and registry.

Tests the fundamental agent framework that provides dynamic decision-making
capabilities, distinct from deterministic workflows.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from datetime import datetime
from typing import Dict, Any, List

# Import will fail initially - we're doing TDD
try:
    from app.agents.base import AgentBase, AgentResult, AgentExecution, AgentRegistry
    from app.agents.base import DecisionContext, DecisionOutcome
except ImportError:
    # Expected to fail initially in TDD
    AgentBase = None
    AgentResult = None
    AgentExecution = None
    AgentRegistry = None
    DecisionContext = None
    DecisionOutcome = None

from app.services.claude_client import ClaudeClient
from app.services.file_cache import FileCache
from app.git_ops import GitOperations


class TestAgentBase:
    """Test the base agent class that provides dynamic decision-making interface."""
    
    @pytest.fixture
    def mock_dependencies(self):
        """Mock dependencies for agent testing."""
        return {
            'claude_client': Mock(spec=ClaudeClient),
            'git_ops': Mock(spec=GitOperations),
            'file_cache': Mock(spec=FileCache)
        }
    
    @pytest.fixture
    def sample_agent(self, mock_dependencies):
        """Create a sample agent for testing."""
        if AgentBase is None:
            pytest.skip("AgentBase not implemented yet - TDD approach")
        
        class SampleAgent(AgentBase):
            @property
            def name(self) -> str:
                return "test_agent"
            
            @property
            def description(self) -> str:
                return "Test agent for unit testing"
            
            @property
            def capabilities(self) -> List[str]:
                return ["analyze", "decide", "recommend"]
            
            async def make_decision(self, context: DecisionContext) -> DecisionOutcome:
                # Simple test decision logic
                if context.inputs.get("test_mode"):
                    return DecisionOutcome(
                        decision="test_decision",
                        confidence=0.9,
                        reasoning="Test mode enabled",
                        actions=["test_action"],
                        metadata={"test": True}
                    )
                return DecisionOutcome(
                    decision="default_decision",
                    confidence=0.5,
                    reasoning="Default decision path",
                    actions=[],
                    metadata={}
                )
        
        return SampleAgent(**mock_dependencies)
    
    def test_agent_base_abstract_properties_required(self, mock_dependencies):
        """Test that agent base class requires abstract properties."""
        if AgentBase is None:
            pytest.skip("AgentBase not implemented yet - TDD approach")
        
        with pytest.raises(TypeError):
            # Should fail because abstract methods not implemented
            AgentBase(**mock_dependencies)
    
    def test_agent_base_has_required_interface(self, sample_agent):
        """Test that agent base class provides required interface."""
        if AgentBase is None:
            pytest.skip("AgentBase not implemented yet - TDD approach")
        
        # Test required properties
        assert hasattr(sample_agent, 'name')
        assert hasattr(sample_agent, 'description')
        assert hasattr(sample_agent, 'capabilities')
        
        # Test required methods
        assert hasattr(sample_agent, 'make_decision')
        assert hasattr(sample_agent, 'execute_with_tracking')
        assert hasattr(sample_agent, 'get_performance_stats')
        
        # Test properties have correct types
        assert isinstance(sample_agent.name, str)
        assert isinstance(sample_agent.description, str)
        assert isinstance(sample_agent.capabilities, list)
    
    @pytest.mark.asyncio
    async def test_agent_decision_making_with_context(self, sample_agent):
        """Test agent can make context-aware decisions."""
        if DecisionContext is None:
            pytest.skip("DecisionContext not implemented yet - TDD approach")
        
        # Create decision context
        context = DecisionContext(
            inputs={"test_mode": True, "data": "sample"},
            background_context={"user": "test_user", "session": "test_session"},
            constraints={"max_time": 30, "confidence_threshold": 0.8},
            goals=["accuracy", "speed"]
        )
        
        # Make decision
        outcome = await sample_agent.make_decision(context)
        
        # Verify decision outcome
        assert isinstance(outcome, DecisionOutcome)
        assert outcome.decision == "test_decision"
        assert outcome.confidence == 0.9
        assert outcome.reasoning == "Test mode enabled"
        assert "test_action" in outcome.actions
        assert outcome.metadata["test"] is True
    
    @pytest.mark.asyncio
    async def test_agent_execution_tracking(self, sample_agent):
        """Test agent execution is properly tracked."""
        if DecisionContext is None:
            pytest.skip("DecisionContext not implemented yet - TDD approach")
        
        context = DecisionContext(
            inputs={"test_mode": False},
            background_context={},
            constraints={},
            goals=[]
        )
        
        # Execute with tracking
        result = await sample_agent.execute_with_tracking(context)
        
        # Verify result structure
        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.decision_outcome is not None
        assert result.execution_time_ms > 0
        assert result.agent_name == "test_agent"
        
        # Verify execution was recorded
        stats = sample_agent.get_performance_stats()
        assert stats["total_executions"] == 1
        assert stats["successful_executions"] == 1
        assert stats["success_rate"] == 1.0
    
    @pytest.mark.asyncio
    async def test_agent_handles_decision_failures(self, sample_agent):
        """Test agent properly handles decision-making failures."""
        if DecisionContext is None:
            pytest.skip("DecisionContext not implemented yet - TDD approach")
        
        # Mock decision method to raise exception
        original_method = sample_agent.make_decision
        sample_agent.make_decision = AsyncMock(side_effect=Exception("Decision failed"))
        
        context = DecisionContext(inputs={}, background_context={}, constraints={}, goals=[])
        
        # Execute with tracking
        result = await sample_agent.execute_with_tracking(context)
        
        # Verify failure handling
        assert isinstance(result, AgentResult)
        assert result.success is False
        assert result.error == "Decision failed"
        assert result.decision_outcome is None
        
        # Restore original method
        sample_agent.make_decision = original_method
    
    def test_agent_performance_statistics(self, sample_agent):
        """Test agent tracks performance statistics correctly."""
        if AgentBase is None:
            pytest.skip("AgentBase not implemented yet - TDD approach")
        
        # Initially no executions
        stats = sample_agent.get_performance_stats()
        assert stats["total_executions"] == 0
        assert stats["successful_executions"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["average_execution_time_ms"] == 0.0
        assert stats["last_execution"] is None


class TestAgentRegistry:
    """Test the agent registry for managing multiple agents."""
    
    @pytest.fixture
    def mock_dependencies(self):
        """Mock dependencies for registry testing."""
        return {
            'claude_client': Mock(spec=ClaudeClient),
            'git_ops': Mock(spec=GitOperations),
            'file_cache': Mock(spec=FileCache)
        }
    
    @pytest.fixture
    def agent_registry(self, mock_dependencies):
        """Create agent registry for testing."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        return AgentRegistry(**mock_dependencies)
    
    @pytest.fixture
    def sample_agents(self, mock_dependencies):
        """Create sample agents for registry testing."""
        if AgentBase is None:
            pytest.skip("AgentBase not implemented yet - TDD approach")
        
        class TestAgentA(AgentBase):
            @property
            def name(self) -> str:
                return "agent_a"
            
            @property
            def description(self) -> str:
                return "Test Agent A"
            
            @property
            def capabilities(self) -> List[str]:
                return ["analyze"]
            
            async def make_decision(self, context: DecisionContext) -> DecisionOutcome:
                return DecisionOutcome(
                    decision="decision_a",
                    confidence=0.8,
                    reasoning="Agent A decision",
                    actions=["action_a"],
                    metadata={}
                )
        
        class TestAgentB(AgentBase):
            @property
            def name(self) -> str:
                return "agent_b"
            
            @property
            def description(self) -> str:
                return "Test Agent B"
            
            @property
            def capabilities(self) -> List[str]:
                return ["recommend", "execute"]
            
            async def make_decision(self, context: DecisionContext) -> DecisionOutcome:
                return DecisionOutcome(
                    decision="decision_b",
                    confidence=0.9,
                    reasoning="Agent B decision",
                    actions=["action_b1", "action_b2"],
                    metadata={"priority": "high"}
                )
        
        return [
            TestAgentA(**mock_dependencies),
            TestAgentB(**mock_dependencies)
        ]
    
    def test_agent_registry_initialization(self, agent_registry):
        """Test agent registry initializes correctly."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        assert hasattr(agent_registry, 'agents')
        assert hasattr(agent_registry, 'register_agent')
        assert hasattr(agent_registry, 'get_agent')
        assert hasattr(agent_registry, 'list_agents')
        assert len(agent_registry.agents) == 0
    
    def test_agent_registration(self, agent_registry, sample_agents):
        """Test agents can be registered in the registry."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Verify registration
        assert len(agent_registry.agents) == 2
        assert "agent_a" in agent_registry.agents
        assert "agent_b" in agent_registry.agents
    
    def test_agent_retrieval(self, agent_registry, sample_agents):
        """Test agents can be retrieved by name."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Test retrieval
        agent_a = agent_registry.get_agent("agent_a")
        assert agent_a is not None
        assert agent_a.name == "agent_a"
        
        agent_b = agent_registry.get_agent("agent_b")
        assert agent_b is not None
        assert agent_b.name == "agent_b"
        
        # Test non-existent agent
        missing_agent = agent_registry.get_agent("nonexistent")
        assert missing_agent is None
    
    def test_agent_listing(self, agent_registry, sample_agents):
        """Test listing all registered agents."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Test listing
        agent_list = agent_registry.list_agents()
        assert len(agent_list) == 2
        
        # Verify list contains correct metadata
        agent_names = [info["name"] for info in agent_list]
        assert "agent_a" in agent_names
        assert "agent_b" in agent_names
        
        # Check structure of agent info
        for agent_info in agent_list:
            assert "name" in agent_info
            assert "description" in agent_info
            assert "capabilities" in agent_info
            assert "performance" in agent_info
    
    @pytest.mark.asyncio
    async def test_agent_execution_through_registry(self, agent_registry, sample_agents):
        """Test executing agents through the registry."""
        if AgentRegistry is None or DecisionContext is None:
            pytest.skip("AgentRegistry/DecisionContext not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Create context
        context = DecisionContext(
            inputs={"task": "test"},
            background_context={},
            constraints={},
            goals=[]
        )
        
        # Execute agent through registry
        result = await agent_registry.execute_agent("agent_a", context)
        
        # Verify execution
        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.agent_name == "agent_a"
        assert result.decision_outcome.decision == "decision_a"
    
    @pytest.mark.asyncio
    async def test_agent_chaining_capability(self, agent_registry, sample_agents):
        """Test chaining multiple agents for complex decisions."""
        if AgentRegistry is None or DecisionContext is None:
            pytest.skip("AgentRegistry/DecisionContext not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Define agent chain
        chain = [
            {"agent": "agent_a", "inputs": {"task": "analyze"}},
            {"agent": "agent_b", "inputs": {"task": "recommend"}}
        ]
        
        # Execute chain
        results = await agent_registry.execute_chain(chain)
        
        # Verify chain execution
        assert len(results) == 2
        assert all(isinstance(result, AgentResult) for result in results)
        assert results[0].agent_name == "agent_a"
        assert results[1].agent_name == "agent_b"
        assert all(result.success for result in results)
    
    def test_registry_performance_stats(self, agent_registry, sample_agents):
        """Test registry provides overall performance statistics."""
        if AgentRegistry is None:
            pytest.skip("AgentRegistry not implemented yet - TDD approach")
        
        # Register agents
        for agent in sample_agents:
            agent_registry.register_agent(agent)
        
        # Get registry stats
        stats = agent_registry.get_registry_stats()
        
        # Verify stats structure
        assert "total_agents" in stats
        assert "total_executions" in stats
        assert "overall_success_rate" in stats
        assert "agent_performance" in stats
        
        assert stats["total_agents"] == 2
        assert stats["total_executions"] == 0  # No executions yet
        assert isinstance(stats["agent_performance"], dict)


class TestDecisionContext:
    """Test the decision context data structure."""
    
    def test_decision_context_creation(self):
        """Test decision context can be created with required fields."""
        if DecisionContext is None:
            pytest.skip("DecisionContext not implemented yet - TDD approach")
        
        context = DecisionContext(
            inputs={"key": "value"},
            background_context={"session": "test"},
            constraints={"time_limit": 30},
            goals=["accuracy", "speed"]
        )
        
        assert context.inputs == {"key": "value"}
        assert context.background_context == {"session": "test"}
        assert context.constraints == {"time_limit": 30}
        assert context.goals == ["accuracy", "speed"]
    
    def test_decision_context_defaults(self):
        """Test decision context provides sensible defaults."""
        if DecisionContext is None:
            pytest.skip("DecisionContext not implemented yet - TDD approach")
        
        context = DecisionContext(inputs={})
        
        assert context.inputs == {}
        assert context.background_context == {}
        assert context.constraints == {}
        assert context.goals == []


class TestDecisionOutcome:
    """Test the decision outcome data structure."""
    
    def test_decision_outcome_creation(self):
        """Test decision outcome can be created with all fields."""
        if DecisionOutcome is None:
            pytest.skip("DecisionOutcome not implemented yet - TDD approach")
        
        outcome = DecisionOutcome(
            decision="test_decision",
            confidence=0.85,
            reasoning="Detailed reasoning here",
            actions=["action1", "action2"],
            metadata={"priority": "high", "category": "analysis"}
        )
        
        assert outcome.decision == "test_decision"
        assert outcome.confidence == 0.85
        assert outcome.reasoning == "Detailed reasoning here"
        assert outcome.actions == ["action1", "action2"]
        assert outcome.metadata == {"priority": "high", "category": "analysis"}
    
    def test_decision_outcome_validation(self):
        """Test decision outcome validates confidence levels."""
        if DecisionOutcome is None:
            pytest.skip("DecisionOutcome not implemented yet - TDD approach")
        
        # Valid confidence
        outcome = DecisionOutcome(
            decision="test",
            confidence=0.5,
            reasoning="test",
            actions=[],
            metadata={}
        )
        assert outcome.confidence == 0.5
        
        # Test boundary conditions
        with pytest.raises(ValueError):
            DecisionOutcome(
                decision="test",
                confidence=1.5,  # Invalid: > 1.0
                reasoning="test",
                actions=[],
                metadata={}
            )
        
        with pytest.raises(ValueError):
            DecisionOutcome(
                decision="test",
                confidence=-0.1,  # Invalid: < 0.0
                reasoning="test",
                actions=[],
                metadata={}
            )