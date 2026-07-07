"""
TDD Test Suite for ChunkBatcher (Issue #603)

This module contains comprehensive unit tests for the ChunkBatcher class,
which accumulates transcript chunks and decides when to batch them for processing.

Test Coverage:
- Initialization and configuration
- Adding chunks to the buffer
- Retrieving batched chunks
- Buffer state management
- Edge cases and error handling

All tests are designed to FAIL initially following TDD principles.
"""

import pytest
from typing import Dict, List


class TestChunkBatcherInitialization:
    """Test suite for ChunkBatcher initialization and configuration."""

    def test_default_batch_size(self):
        """
        Test that ChunkBatcher initializes with default batch_size of 3.

        Expected behavior:
        - Default batch_size should be 3
        - Buffer should be empty list
        """
        # This will fail until ChunkBatcher is implemented
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()
        assert batcher.batch_size == 3
        assert batcher.buffer == []

    def test_custom_batch_size(self):
        """
        Test that ChunkBatcher can be initialized with custom batch_size.

        Expected behavior:
        - batch_size should be set to custom value
        - Buffer should be empty list
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=5)
        assert batcher.batch_size == 5
        assert batcher.buffer == []

    def test_batch_size_validation_positive(self):
        """
        Test that batch_size must be positive integer.

        Expected behavior:
        - batch_size of 0 or negative should raise ValueError
        """
        from app.services.chunk_batcher import ChunkBatcher

        # Should raise ValueError for 0
        with pytest.raises(ValueError):
            ChunkBatcher(batch_size=0)

        # Should raise ValueError for negative
        with pytest.raises(ValueError):
            ChunkBatcher(batch_size=-1)

    def test_buffer_is_list(self):
        """
        Test that buffer is initialized as a List[Dict].

        Expected behavior:
        - buffer should be a list type
        - buffer should be empty on initialization
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()
        assert isinstance(batcher.buffer, list)
        assert len(batcher.buffer) == 0


class TestChunkBatcherAdd:
    """Test suite for adding chunks to the buffer."""

    def test_add_single_chunk_not_ready(self):
        """
        Test adding a single chunk when batch is not full.

        Expected behavior:
        - add() should return False (batch not ready)
        - Chunk should be in buffer
        - Buffer length should be 1
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        chunk = {
            "text": "Hello world",
            "speaker": "Alice",
            "timestamp": "2024-01-01T00:00:00Z"
        }

        ready = batcher.add(chunk)
        assert ready is False
        assert len(batcher.buffer) == 1
        assert batcher.buffer[0] == chunk

    def test_add_chunks_until_ready(self):
        """
        Test adding chunks until batch is ready.

        Expected behavior:
        - First two add() calls return False
        - Third add() call returns True (batch ready)
        - Buffer contains all 3 chunks
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        chunk1 = {"text": "First", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"}
        chunk2 = {"text": "Second", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"}
        chunk3 = {"text": "Third", "speaker": "Alice", "timestamp": "2024-01-01T00:00:02Z"}

        assert batcher.add(chunk1) is False
        assert batcher.add(chunk2) is False
        assert batcher.add(chunk3) is True
        assert len(batcher.buffer) == 3

    def test_add_chunk_preserves_order(self):
        """
        Test that chunks are stored in the order they were added.

        Expected behavior:
        - Chunks should be retrievable in FIFO order
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=5)

        chunks = [
            {"text": f"Chunk {i}", "speaker": "Speaker", "timestamp": f"2024-01-01T00:00:{i:02d}Z"}
            for i in range(3)
        ]

        for chunk in chunks:
            batcher.add(chunk)

        for i, chunk in enumerate(batcher.buffer):
            assert chunk["text"] == f"Chunk {i}"

    def test_add_chunk_with_all_fields(self):
        """
        Test that chunks with all required fields are accepted.

        Expected behavior:
        - Chunk with text, speaker, timestamp should be added successfully
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()

        chunk = {
            "text": "Complete chunk",
            "speaker": "John Doe",
            "timestamp": "2024-01-01T12:30:00Z"
        }

        ready = batcher.add(chunk)
        assert ready is False
        assert batcher.buffer[0]["text"] == "Complete chunk"
        assert batcher.buffer[0]["speaker"] == "John Doe"
        assert batcher.buffer[0]["timestamp"] == "2024-01-01T12:30:00Z"

    def test_add_returns_true_at_exact_batch_size(self):
        """
        Test that add() returns True exactly when batch_size is reached.

        Expected behavior:
        - Returns True when buffer length == batch_size
        - Returns False when buffer length < batch_size
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        chunk1 = {"text": "First", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"}
        chunk2 = {"text": "Second", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"}

        assert batcher.add(chunk1) is False
        assert batcher.add(chunk2) is True


class TestChunkBatcherGetBatch:
    """Test suite for retrieving batched chunks."""

    def test_get_batch_combines_text(self):
        """
        Test that get_batch() combines text from all chunks with spaces.

        Expected behavior:
        - Combined text should be space-separated
        - Returns Dict with 'text' field
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        batcher.add({"text": "Hello", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "world", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})
        batcher.add({"text": "today", "speaker": "Alice", "timestamp": "2024-01-01T00:00:02Z"})

        batch = batcher.get_batch()
        assert batch["text"] == "Hello world today"

    def test_get_batch_uses_last_speaker(self):
        """
        Test that get_batch() uses speaker from the last chunk.

        Expected behavior:
        - batch['speaker'] should be from last chunk in buffer
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        batcher.add({"text": "First", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Second", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"})
        batcher.add({"text": "Third", "speaker": "Charlie", "timestamp": "2024-01-01T00:00:02Z"})

        batch = batcher.get_batch()
        assert batch["speaker"] == "Charlie"

    def test_get_batch_uses_last_timestamp(self):
        """
        Test that get_batch() uses timestamp from the last chunk.

        Expected behavior:
        - batch['timestamp'] should be from last chunk in buffer
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        batcher.add({"text": "First", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Second", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})
        batcher.add({"text": "Third", "speaker": "Alice", "timestamp": "2024-01-01T00:00:02Z"})

        batch = batcher.get_batch()
        assert batch["timestamp"] == "2024-01-01T00:00:02Z"

    def test_get_batch_includes_chunk_count(self):
        """
        Test that get_batch() includes chunk_count field.

        Expected behavior:
        - batch['chunk_count'] should equal number of chunks combined
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=3)

        batcher.add({"text": "One", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Two", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})
        batcher.add({"text": "Three", "speaker": "Alice", "timestamp": "2024-01-01T00:00:02Z"})

        batch = batcher.get_batch()
        assert batch["chunk_count"] == 3

    def test_get_batch_clears_buffer(self):
        """
        Test that get_batch() clears the buffer after returning batch.

        Expected behavior:
        - Buffer should be empty after get_batch()
        - is_empty() should return True
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "One", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Two", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert batcher.buffer == []
        assert batcher.is_empty() is True

    def test_get_batch_empty_buffer_returns_none(self):
        """
        Test that get_batch() returns None when buffer is empty.

        Expected behavior:
        - Should return None (not empty dict or empty string)
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()

        batch = batcher.get_batch()
        assert batch is None

    def test_get_batch_partial_buffer(self):
        """
        Test that get_batch() works with partial buffer (< batch_size).

        Expected behavior:
        - Should combine available chunks even if less than batch_size
        - chunk_count should reflect actual number of chunks
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=5)

        batcher.add({"text": "One", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Two", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert batch is not None
        assert batch["text"] == "One Two"
        assert batch["chunk_count"] == 2
        assert batch["speaker"] == "Bob"

    def test_get_batch_multiple_calls(self):
        """
        Test that get_batch() can be called multiple times in sequence.

        Expected behavior:
        - First call should return batch
        - Second call on empty buffer should return None
        - Can add more chunks and get batch again
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        # First batch
        batcher.add({"text": "A", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "B", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})
        batch1 = batcher.get_batch()
        assert batch1["text"] == "A B"

        # Empty buffer
        batch2 = batcher.get_batch()
        assert batch2 is None

        # Second batch
        batcher.add({"text": "C", "speaker": "Bob", "timestamp": "2024-01-01T00:00:02Z"})
        batcher.add({"text": "D", "speaker": "Bob", "timestamp": "2024-01-01T00:00:03Z"})
        batch3 = batcher.get_batch()
        assert batch3["text"] == "C D"

    def test_get_batch_preserves_chunk_structure(self):
        """
        Test that get_batch() returns Dict with all required fields.

        Expected behavior:
        - Should return Dict with keys: text, speaker, timestamp, chunk_count
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "Hello", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "World", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert "text" in batch
        assert "speaker" in batch
        assert "timestamp" in batch
        assert "chunk_count" in batch


class TestChunkBatcherIsEmpty:
    """Test suite for checking buffer state."""

    def test_is_empty_initially(self):
        """
        Test that buffer is empty on initialization.

        Expected behavior:
        - is_empty() should return True for new batcher
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()
        assert batcher.is_empty() is True

    def test_is_empty_after_add(self):
        """
        Test that is_empty() returns False after adding chunks.

        Expected behavior:
        - is_empty() should return False when buffer has chunks
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher()

        batcher.add({"text": "Test", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        assert batcher.is_empty() is False

    def test_is_empty_after_get_batch(self):
        """
        Test that is_empty() returns True after get_batch() clears buffer.

        Expected behavior:
        - is_empty() should return True after buffer is cleared
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "One", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Two", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})

        batcher.get_batch()
        assert batcher.is_empty() is True


class TestChunkBatcherEdgeCases:
    """Test suite for edge cases and error conditions."""

    def test_add_empty_text_chunk(self):
        """
        Test handling of chunks with empty text.

        Expected behavior:
        - Should accept chunk with empty string
        - get_batch() should handle empty text appropriately
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Hello", "speaker": "Alice", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert batch["text"] == " Hello"  # Space-separated even with empty

    def test_batch_size_one(self):
        """
        Test batcher with batch_size of 1.

        Expected behavior:
        - First add() should return True
        - get_batch() should return single chunk
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=1)

        ready = batcher.add({"text": "Solo", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        assert ready is True

        batch = batcher.get_batch()
        assert batch["text"] == "Solo"
        assert batch["chunk_count"] == 1

    def test_very_large_batch_size(self):
        """
        Test batcher with very large batch_size.

        Expected behavior:
        - Should accumulate many chunks without returning True
        - get_batch() should still work with partial buffer
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=1000)

        for i in range(10):
            ready = batcher.add({"text": f"Chunk {i}", "speaker": "Alice", "timestamp": f"2024-01-01T00:00:{i:02d}Z"})
            assert ready is False

        assert len(batcher.buffer) == 10

        batch = batcher.get_batch()
        assert batch["chunk_count"] == 10

    def test_unicode_text_handling(self):
        """
        Test that chunks with unicode characters are handled correctly.

        Expected behavior:
        - Should preserve unicode characters in text
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "Hello 世界", "speaker": "Alice", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "مرحبا", "speaker": "Bob", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert batch["text"] == "Hello 世界 مرحبا"

    def test_special_characters_in_speaker(self):
        """
        Test that special characters in speaker names are preserved.

        Expected behavior:
        - Should preserve special characters, apostrophes, etc.
        """
        from app.services.chunk_batcher import ChunkBatcher
        batcher = ChunkBatcher(batch_size=2)

        batcher.add({"text": "First", "speaker": "O'Brien", "timestamp": "2024-01-01T00:00:00Z"})
        batcher.add({"text": "Second", "speaker": "José García", "timestamp": "2024-01-01T00:00:01Z"})

        batch = batcher.get_batch()
        assert batch["speaker"] == "José García"
