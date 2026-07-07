"""Zapier transcript adapter — normalizes commodity-recorder payloads into the
generic /api/ingest pipeline. No client resolution here; client scope is derived
downstream during signal promotion."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..models.ingestion.models import ContentSource, IngestRequest, IngestResponse
from ..services.task_queue import TaskQueue
from .ingest import _get_job_store, _get_task_queue, ingest_content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest/zapier", tags=["ingestion"])

_PROVIDER_MAP = {
    "fireflies": ContentSource.FIREFLIES,
    "otter": ContentSource.OTTER,
    "fathom": ContentSource.FATHOM,
    "grain": ContentSource.GRAIN,
    "zoom": ContentSource.OTHER,  # Zoom transcripts have no dedicated enum yet
}


class ZapierTranscriptPayload(BaseModel):
    """Generic Zapier webhook body for a finished recording's transcript."""
    provider: str = Field(..., min_length=1, description="Recorder name: otter, fathom, grain, fireflies, zoom")
    transcript: str = Field(..., min_length=1)
    title: str | None = None
    participants: list[str] | None = None
    external_id: str | None = Field(None, description="Provider recording ID, for idempotency")
    recorded_at: datetime | None = None


def _to_ingest_request(payload: ZapierTranscriptPayload) -> IngestRequest:
    normalized_provider = payload.provider.strip().lower()
    return IngestRequest(
        content=payload.transcript,
        source=_PROVIDER_MAP.get(normalized_provider, ContentSource.OTHER),
        source_id=payload.external_id,
        title=payload.title,
        participants=payload.participants,
        timestamp=payload.recorded_at,
        metadata={"provider": normalized_provider},
    )


@router.post("", status_code=202, response_model=IngestResponse)
async def ingest_zapier_transcript(
    payload: ZapierTranscriptPayload,
    response: Response,
    task_queue: TaskQueue = Depends(_get_task_queue),
    job_store: dict[str, Any] = Depends(_get_job_store),
) -> IngestResponse:
    """Accept a Zapier transcript and forward it into the shared ingest pipeline."""
    logger.info("[ZAPIER] Transcript from %s (external_id=%s)", payload.provider, payload.external_id)
    request = _to_ingest_request(payload)
    return await ingest_content(request, response, task_queue, job_store)
