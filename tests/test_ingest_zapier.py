from app.routes.ingest_zapier import _to_ingest_request, ZapierTranscriptPayload
from app.models.ingestion.models import ContentSource


def test_maps_provider_and_fields():
    payload = ZapierTranscriptPayload(
        provider="otter", title="Acme weekly", transcript="We agreed to send the SOW.",
        participants=["Jane Doe", "Me"], external_id="otter-123",
        recorded_at="2026-05-20T10:00:00+00:00",
    )
    req = _to_ingest_request(payload)
    assert req.source == ContentSource.OTTER
    assert req.source_id == "otter-123"
    assert req.title == "Acme weekly"
    assert req.content == "We agreed to send the SOW."
    assert req.participants == ["Jane Doe", "Me"]
    assert req.metadata == {"provider": "otter"}


def test_unknown_provider_falls_back_to_other():
    req = _to_ingest_request(ZapierTranscriptPayload(provider="superphone", transcript="hi"))
    assert req.source == ContentSource.OTHER


def test_zoom_maps_to_other_but_preserves_provider():
    req = _to_ingest_request(ZapierTranscriptPayload(provider="Zoom", transcript="hi"))
    assert req.source == ContentSource.OTHER
    assert req.metadata["provider"] == "zoom"
