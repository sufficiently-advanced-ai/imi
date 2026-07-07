"""Grain API connector — downloads recordings and normalizes to IngestRequest."""

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict

from app.connectors.base import BaseConnector
from app.models.ingestion.models import ContentSource, IngestRequest

logger = logging.getLogger(__name__)


# --- Grain API response models ---

class GrainTranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    participant_id: str
    speaker: str
    start: int   # ms offset from recording start
    end: int     # ms offset from recording start
    text: str


class GrainRecording(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    start_datetime: str   # ISO 8601
    end_datetime: str | None = None
    duration_ms: int
    participants: list[str] | None = None
    transcript: list[GrainTranscriptSegment] | None = None


# --- Timestamp helpers ---

def ms_offset_to_absolute(start_datetime_iso: str, offset_ms: int) -> str:
    """Convert ms offset from recording start to absolute ISO 8601 timestamp."""
    start = datetime.fromisoformat(start_datetime_iso)
    absolute = start + timedelta(milliseconds=offset_ms)
    return absolute.isoformat()


def format_transcript(
    start_datetime_iso: str,
    segments: list[GrainTranscriptSegment],
) -> str:
    """Format transcript segments into [timestamp] Speaker: text lines."""
    lines = []
    for seg in segments:
        if not seg.text.strip():
            continue
        ts = ms_offset_to_absolute(start_datetime_iso, seg.start)
        lines.append(f"[{ts}] {seg.speaker}: {seg.text}")
    return "\n".join(lines)


GRAIN_BASE_URL = "https://grain.com/_/public-api"


class GrainClient:
    """HTTP client for Grain public API with retry logic."""

    def __init__(self, api_key: str, max_retries: int = 2):
        if not api_key or not api_key.strip():
            raise ValueError("API key is required")
        self.api_key = api_key
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{GRAIN_BASE_URL}{path}"
        last_exc = None
        for attempt in range(self.max_retries + 1):
            resp = await self._client.request(
                method, url, headers=self._headers(), params=params,
            )
            if resp.status_code == 401:
                raise PermissionError("Grain API authentication failed")
            if resp.status_code == 404:
                raise FileNotFoundError(f"Not found: {path}")
            if resp.status_code >= 500:
                last_exc = ConnectionError(f"Server error {resp.status_code}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_exc
            if resp.status_code >= 400:
                raise ValueError(f"Grain API error {resp.status_code}: {resp.text}")
            return resp.json()
        raise last_exc or RuntimeError("Unexpected retry state")

    async def list_recordings(
        self, start_date: str | None = None, end_date: str | None = None,
    ) -> list[dict]:
        """List all recordings, handling cursor pagination."""
        all_recordings: list[dict] = []
        params: dict = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        page = 0
        while True:
            page += 1
            data = await self._request("GET", "/recordings", params=params)
            batch = data.get("recordings", [])
            all_recordings.extend(batch)
            cursor = data.get("next_cursor")
            logger.debug(
                "Grain list_recordings page=%d batch=%d total=%d next_cursor=%s",
                page, len(batch), len(all_recordings), "yes" if cursor else "no",
            )
            if not cursor:
                break
            params["cursor"] = cursor
        logger.info(
            "Grain list_recordings done: %d recordings across %d page(s)",
            len(all_recordings), page,
        )
        return all_recordings

    async def get_recording(self, recording_id: str) -> dict:
        """Get a single recording with JSON transcript."""
        return await self._request(
            "GET", f"/recordings/{recording_id}",
            params={"transcript_format": "json"},
        )

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


class GrainConnector(BaseConnector):
    """Transforms Grain recordings into IngestRequests."""

    def __init__(self, api_key: str):
        self._client = GrainClient(api_key=api_key)

    async def list_recordings(self, **kwargs) -> list[dict]:
        return await self._client.list_recordings(
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
        )

    async def fetch_recording(self, recording_id: str) -> dict:
        return await self._client.get_recording(recording_id)

    def to_ingest_request(self, recording: dict) -> IngestRequest:
        rec = GrainRecording(**recording)
        segments = rec.transcript or []
        parsed_segments = [
            GrainTranscriptSegment(**s) if isinstance(s, dict) else s
            for s in segments
        ]

        content = format_transcript(rec.start_datetime, parsed_segments)
        if not content.strip():
            content = f"No transcript available for: {rec.title}"

        # Deduplicate participants: prefer recording metadata, fall back to transcript speakers
        participants = rec.participants
        if not participants and parsed_segments:
            seen: dict[str, None] = {}
            for seg in parsed_segments:
                seen.setdefault(seg.speaker, None)
            participants = list(seen.keys())

        return IngestRequest(
            content=content,
            source=ContentSource.GRAIN,
            source_id=f"grain:{rec.id}",
            title=rec.title,
            participants=participants,
            timestamp=datetime.fromisoformat(rec.start_datetime),
            metadata={
                "grain_recording_id": rec.id,
                "duration_ms": rec.duration_ms,
                "end_datetime": rec.end_datetime,
            },
        )

    async def close(self):
        await self._client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


def ingest_request_to_jsonl(request: IngestRequest) -> str:
    """Serialize an IngestRequest to a single JSONL line."""
    return request.model_dump_json()
