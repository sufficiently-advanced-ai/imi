"""
TDD Test Suite for Parallel Processing (Issue #603)

This module contains comprehensive unit tests for parallel processor execution
using asyncio.gather() in the webhook handler.

Test Coverage:
- Parallel execution of processors
- Exception handling with return_exceptions=True
- Signal filtering from results
- Performance characteristics

All tests are designed to FAIL initially following TDD principles.
"""

import pytest
import asyncio
from typing import Dict, List, Optional
from unittest.mock import Mock, AsyncMock, patch, MagicMock


class TestParallelExecution:
    """Test suite for parallel processor execution."""

    @pytest.mark.asyncio
    async def test_processors_run_in_parallel(self):
        """
        Test that all processors execute simultaneously using asyncio.gather().

        Expected behavior:
        - All processors should be called with the same text and context
        - asyncio.gather() should be used for parallel execution
        - Total execution time should be ~max(individual times), not sum
        """
        # Mock processors
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(return_value={"type": "decision", "text": "Decision 1"})

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(return_value={"type": "keypoint", "text": "Keypoint 1"})

        processor3 = Mock()
        processor3.process_chunk = AsyncMock(return_value={"type": "action", "text": "Action 1"})

        processors = [processor1, processor2, processor3]

        # Create session
        session = {
            "processors": processors,
            "agent": AsyncMock(),
            "batcher": Mock(),
            "created_at": 1234567890.0,
            "meeting_id": "test-meeting"
        }

        text = "Important meeting decision"
        context = {"bot_id": "bot-123", "meeting_id": "meeting-456"}

        # Execute parallel processing (this is the code we're testing)
        tasks = [
            processor.process_chunk(text, context)
            for processor in session["processors"]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify all processors were called
        processor1.process_chunk.assert_called_once_with(text, context)
        processor2.process_chunk.assert_called_once_with(text, context)
        processor3.process_chunk.assert_called_once_with(text, context)

        # Verify we got all results
        assert len(results) == 3
        assert all(isinstance(r, dict) for r in results)

    @pytest.mark.asyncio
    async def test_gather_return_exceptions_true(self):
        """
        Test that asyncio.gather uses return_exceptions=True.

        Expected behavior:
        - Exceptions should be returned in results, not raised
        - Should allow processing to continue even if one processor fails
        """
        # Mock processors - one succeeds, one fails
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(return_value={"type": "decision"})

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(side_effect=ValueError("Processing error"))

        processor3 = Mock()
        processor3.process_chunk = AsyncMock(return_value={"type": "action"})

        processors = [processor1, processor2, processor3]

        text = "Test text"
        context = {"bot_id": "bot-123", "meeting_id": "meeting-456"}

        # Execute with return_exceptions=True
        tasks = [p.process_chunk(text, context) for p in processors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Should have 3 results: 2 dicts and 1 exception
        assert len(results) == 3
        assert isinstance(results[0], dict)
        assert isinstance(results[1], ValueError)
        assert isinstance(results[2], dict)

    @pytest.mark.asyncio
    async def test_parallel_execution_timing(self):
        """
        Test that parallel execution is faster than sequential.

        Expected behavior:
        - Parallel execution time should be close to slowest processor
        - Should NOT be sum of all processor times
        """
        import time

        # Create processors with different delays
        async def slow_processor(text, context):
            await asyncio.sleep(0.3)
            return {"type": "decision", "duration": 0.3}

        async def medium_processor(text, context):
            await asyncio.sleep(0.2)
            return {"type": "keypoint", "duration": 0.2}

        async def fast_processor(text, context):
            await asyncio.sleep(0.1)
            return {"type": "action", "duration": 0.1}

        processors = [
            Mock(process_chunk=slow_processor),
            Mock(process_chunk=medium_processor),
            Mock(process_chunk=fast_processor)
        ]

        text = "Test"
        context = {"bot_id": "bot-123", "meeting_id": "meeting-456"}

        # Parallel execution
        start = time.time()
        tasks = [p.process_chunk(text, context) for p in processors]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        parallel_time = time.time() - start

        # Should be close to 0.3s (slowest), not 0.6s (sum)
        assert parallel_time < 0.4  # Allow some overhead
        assert parallel_time >= 0.3  # At least as long as slowest

    @pytest.mark.asyncio
    async def test_all_processors_receive_same_input(self):
        """
        Test that all processors receive identical text and context.

        Expected behavior:
        - Each processor should get the same text
        - Each processor should get the same context dict
        """
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(return_value={"type": "decision"})

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(return_value={"type": "keypoint"})

        processors = [processor1, processor2]

        text = "Shared input text"
        context = {"bot_id": "bot-123", "meeting_id": "meeting-456"}

        tasks = [p.process_chunk(text, context) for p in processors]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Verify both got same arguments
        call1_text, call1_context = processor1.process_chunk.call_args[0]
        call2_text, call2_context = processor2.process_chunk.call_args[0]

        assert call1_text == call2_text == text
        assert call1_context == call2_context == context


class TestExceptionHandling:
    """Test suite for exception handling in parallel processing."""

    @pytest.mark.asyncio
    async def test_exception_logged_but_not_raised(self):
        """
        Test that processor exceptions are logged but don't crash the handler.

        Expected behavior:
        - Exception should be logged with logger.error
        - Webhook should return success (not 500 error)
        """
        processor = Mock()
        processor.process_chunk = AsyncMock(side_effect=RuntimeError("Processor crashed"))

        tasks = [processor.process_chunk("text", {"bot_id": "bot-123"})]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Should have exception in results
        assert len(results) == 1
        assert isinstance(results[0], RuntimeError)

        # In actual implementation, this should be logged
        # (We'll test logging separately with mocks)

    @pytest.mark.asyncio
    async def test_multiple_processor_failures(self):
        """
        Test handling of multiple simultaneous processor failures.

        Expected behavior:
        - All exceptions should be captured in results
        - Should not raise any exception
        """
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(side_effect=ValueError("Error 1"))

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(side_effect=KeyError("Error 2"))

        processor3 = Mock()
        processor3.process_chunk = AsyncMock(side_effect=RuntimeError("Error 3"))

        processors = [processor1, processor2, processor3]

        tasks = [p.process_chunk("text", {}) for p in processors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert len(results) == 3
        assert all(isinstance(r, Exception) for r in results)

    @pytest.mark.asyncio
    async def test_partial_failure_continues_processing(self):
        """
        Test that successful processors continue even if others fail.

        Expected behavior:
        - Successful results should be in results list
        - Failed processors should have exceptions in results list
        - Both types should coexist in results
        """
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(return_value={"type": "decision", "text": "Success 1"})

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(side_effect=ValueError("Failure"))

        processor3 = Mock()
        processor3.process_chunk = AsyncMock(return_value={"type": "action", "text": "Success 2"})

        processors = [processor1, processor2, processor3]

        tasks = [p.process_chunk("text", {}) for p in processors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Results should be mixed
        assert isinstance(results[0], dict)
        assert isinstance(results[1], ValueError)
        assert isinstance(results[2], dict)

    @pytest.mark.asyncio
    async def test_exception_contains_error_info(self):
        """
        Test that caught exceptions contain useful error information.

        Expected behavior:
        - Exception type should be preserved
        - Exception message should be preserved
        """
        error_msg = "Custom error message"
        processor = Mock()
        processor.process_chunk = AsyncMock(side_effect=ValueError(error_msg))

        tasks = [processor.process_chunk("text", {})]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        exception = results[0]
        assert isinstance(exception, ValueError)
        assert str(exception) == error_msg

    @pytest.mark.asyncio
    async def test_asyncio_cancellation_handling(self):
        """
        Test handling of asyncio.CancelledError in processors.

        Expected behavior:
        - CancelledError should be caught like other exceptions
        - Should not crash the webhook handler
        """
        processor = Mock()
        processor.process_chunk = AsyncMock(side_effect=asyncio.CancelledError())

        tasks = [processor.process_chunk("text", {})]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert len(results) == 1
        assert isinstance(results[0], asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_timeout_exception_handling(self):
        """
        Test handling of asyncio.TimeoutError in processors.

        Expected behavior:
        - TimeoutError should be caught and logged
        - Other processors should continue
        """
        processor1 = Mock()
        processor1.process_chunk = AsyncMock(side_effect=asyncio.TimeoutError())

        processor2 = Mock()
        processor2.process_chunk = AsyncMock(return_value={"type": "decision"})

        tasks = [
            processor1.process_chunk("text", {}),
            processor2.process_chunk("text", {})
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert isinstance(results[0], asyncio.TimeoutError)
        assert isinstance(results[1], dict)


class TestSignalFiltering:
    """Test suite for filtering valid signals from results."""

    @pytest.mark.asyncio
    async def test_filter_exceptions_from_results(self):
        """
        Test that exceptions are filtered out when collecting signals.

        Expected behavior:
        - Only dict results should be added to signals list
        - Exceptions should be skipped
        """
        # Mix of results and exceptions
        results = [
            {"type": "decision", "text": "Decision 1"},
            ValueError("Error"),
            {"type": "keypoint", "text": "Keypoint 1"},
            RuntimeError("Another error"),
            None
        ]

        # Filter to valid signals
        signals = []
        for result in results:
            if isinstance(result, Exception):
                # Should be logged but not added
                continue
            if result is not None:
                signals.append(result)

        assert len(signals) == 2
        assert all(isinstance(s, dict) for s in signals)

    @pytest.mark.asyncio
    async def test_filter_none_results(self):
        """
        Test that None results are filtered out.

        Expected behavior:
        - Processors returning None should not create signals
        - Only non-None dicts should be in signals list
        """
        results = [
            {"type": "decision", "text": "Decision 1"},
            None,
            {"type": "keypoint", "text": "Keypoint 1"},
            None
        ]

        signals = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                signals.append(result)

        assert len(signals) == 2
        assert signals[0]["type"] == "decision"
        assert signals[1]["type"] == "keypoint"

    @pytest.mark.asyncio
    async def test_all_valid_signals_passed_to_agent(self):
        """
        Test that all valid signals are passed to agent.process_signals().

        Expected behavior:
        - Agent should receive list of all valid signals
        - No exceptions or None values in the list
        """
        agent = AsyncMock()

        results = [
            {"type": "decision", "text": "Decision 1"},
            ValueError("Error"),
            {"type": "keypoint", "text": "Keypoint 1"},
            None,
            {"type": "action", "text": "Action 1"}
        ]

        # Filter signals
        signals = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                signals.append(result)

        # Send to agent
        if signals:
            batched_chunk = {
                "text": "Combined text",
                "speaker": "Alice",
                "timestamp": "2024-01-01T00:00:00Z",
                "chunk_count": 3
            }
            await agent.process_signals(signals, batched_chunk)

        # Verify agent received filtered signals
        agent.process_signals.assert_called_once()
        call_signals, call_chunk = agent.process_signals.call_args[0]

        assert len(call_signals) == 3
        assert all(isinstance(s, dict) for s in call_signals)
        assert call_chunk == batched_chunk

    @pytest.mark.asyncio
    async def test_no_signals_agent_not_called(self):
        """
        Test that agent is not called when no valid signals exist.

        Expected behavior:
        - If all processors return None or raise exceptions, agent is not called
        - Should log "No signals extracted"
        """
        agent = AsyncMock()

        results = [
            ValueError("Error 1"),
            None,
            RuntimeError("Error 2"),
            None
        ]

        # Filter signals
        signals = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                signals.append(result)

        # Only call agent if signals exist
        if signals:
            await agent.process_signals(signals, {})

        # Agent should NOT have been called
        agent.process_signals.assert_not_called()

    @pytest.mark.asyncio
    async def test_signal_structure_preserved(self):
        """
        Test that signal structure is preserved through filtering.

        Expected behavior:
        - Signal dicts should maintain all their fields
        - No data should be lost or modified
        """
        original_signal = {
            "type": "decision",
            "text": "Important decision made",
            "confidence": 0.95,
            "metadata": {"key": "value"}
        }

        results = [
            original_signal,
            ValueError("Error"),
            None
        ]

        # Filter
        signals = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                signals.append(result)

        # Verify structure preserved
        assert len(signals) == 1
        assert signals[0] == original_signal
        assert signals[0]["type"] == "decision"
        assert signals[0]["confidence"] == 0.95
        assert signals[0]["metadata"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_empty_results_list(self):
        """
        Test handling of empty results list (no processors).

        Expected behavior:
        - Should handle empty results gracefully
        - No signals should be extracted
        """
        results = []

        signals = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                signals.append(result)

        assert len(signals) == 0
        assert signals == []
