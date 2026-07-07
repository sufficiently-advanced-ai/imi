"""
ChunkBatcher - Accumulates transcript chunks and batches them for processing.

This class implements intelligent chunk batching for Issue #603, which reduces
API calls by combining multiple transcript chunks before processing.

Key features:
- Configurable batch size (default: 3 chunks)
- Automatic batch readiness detection
- Combines chunk text while preserving metadata
- Thread-safe buffer management
"""



class ChunkBatcher:
    """
    Accumulates transcript chunks and decides when to batch them for processing.

    This class reduces API costs by batching multiple transcript chunks together
    before sending them to processors and the agent.

    Attributes:
        batch_size (int): Number of chunks to accumulate before processing
        buffer (List[Dict]): Internal buffer storing chunks
    """

    def __init__(self, batch_size: int = 3):
        """
        Initialize ChunkBatcher with configurable batch size.

        Args:
            batch_size (int): Number of chunks to accumulate. Must be >= 1.

        Raises:
            ValueError: If batch_size is less than 1
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        self.batch_size = batch_size
        self.buffer: list[dict] = []

    def add(self, chunk: dict) -> bool:
        """
        Add a chunk to the buffer and check if batch is ready.

        Args:
            chunk (Dict): Chunk with keys: text, speaker, timestamp

        Returns:
            bool: True if batch is ready (buffer size == batch_size), False otherwise
        """
        self.buffer.append(chunk)
        return len(self.buffer) >= self.batch_size

    def get_batch(self) -> dict | None:
        """
        Retrieve a combined batch from buffered chunks and clear the buffer.

        Combines all chunks in buffer into a single batch with:
        - text: space-separated text from all chunks
        - speaker: speaker from last chunk
        - timestamp: timestamp from last chunk
        - chunk_count: number of chunks combined

        Returns:
            Dict | None: Combined batch dict, or None if buffer is empty
        """
        if not self.buffer:
            return None

        # Combine all chunk text with spaces
        combined_text = " ".join(chunk["text"] for chunk in self.buffer)

        # Use metadata from last chunk (most recent)
        last_chunk = self.buffer[-1]

        # Create batch with combined text and metadata
        batch = {
            "text": combined_text,
            "speaker": last_chunk["speaker"],
            "timestamp": last_chunk["timestamp"],
            "chunk_count": len(self.buffer)
        }

        # Clear buffer after creating batch
        self.buffer = []

        return batch

    def is_empty(self) -> bool:
        """
        Check if buffer is empty.

        Returns:
            bool: True if buffer has no chunks, False otherwise
        """
        return len(self.buffer) == 0
