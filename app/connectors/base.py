"""Abstract base class for external content connectors."""

from abc import ABC, abstractmethod

from app.models.ingestion.models import IngestRequest


class BaseConnector(ABC):
    """Interface for connectors that fetch external recordings/content."""

    @abstractmethod
    async def list_recordings(self, **kwargs) -> list[dict]:
        """List available recordings (metadata only)."""
        ...

    @abstractmethod
    async def fetch_recording(self, recording_id: str) -> dict:
        """Fetch a single recording with transcript."""
        ...

    @abstractmethod
    def to_ingest_request(self, recording: dict) -> IngestRequest:
        """Transform a recording into a normalized IngestRequest."""
        ...
