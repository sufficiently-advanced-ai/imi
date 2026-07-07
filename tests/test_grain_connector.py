import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

from pydantic import ValidationError


class TestGrainModels:
    def test_transcript_segment_parses(self):
        from app.connectors.grain import GrainTranscriptSegment
        seg = GrainTranscriptSegment(
            participant_id="p1", speaker="Scott",
            start=720000, end=725000,
            text="I think we should move forward with this.",
        )
        assert seg.speaker == "Scott"
        assert seg.start == 720000

    def test_recording_parses(self):
        from app.connectors.grain import GrainRecording
        rec = GrainRecording(
            id="rec_abc123", title="Weekly Sync",
            start_datetime="2026-03-20T14:00:00Z",
            end_datetime="2026-03-20T15:00:00Z",
            duration_ms=3600000, participants=["Scott", "Alex"],
            transcript=[],
        )
        assert rec.id == "rec_abc123"
        assert rec.duration_ms == 3600000

    def test_recording_without_optional_fields(self):
        from app.connectors.grain import GrainRecording
        rec = GrainRecording(
            id="rec_abc123", title="Weekly Sync",
            start_datetime="2026-03-20T14:00:00Z",
            duration_ms=3600000,
        )
        assert rec.transcript is None
        assert rec.participants is None

    def test_recording_ignores_extra_fields(self):
        from app.connectors.grain import GrainRecording
        rec = GrainRecording(
            id="rec_1", title="T",
            start_datetime="2026-03-20T14:00:00Z",
            duration_ms=60000, some_future_field="ignored",
        )
        assert rec.id == "rec_1"


class TestTimestampTransform:
    def test_zero_offset(self):
        from app.connectors.grain import ms_offset_to_absolute
        result = ms_offset_to_absolute("2026-03-20T14:00:00Z", 0)
        assert "14:00:00" in result

    def test_twelve_minute_offset(self):
        from app.connectors.grain import ms_offset_to_absolute
        result = ms_offset_to_absolute("2026-03-20T14:00:00Z", 720000)
        assert "14:12:00" in result

    def test_sub_second_rounds_down(self):
        from app.connectors.grain import ms_offset_to_absolute
        result = ms_offset_to_absolute("2026-03-20T14:00:00Z", 500)
        assert "14:00:00" in result

    def test_preserves_timezone(self):
        from app.connectors.grain import ms_offset_to_absolute
        result = ms_offset_to_absolute("2026-03-20T09:00:00-05:00", 60000)
        assert "09:01:00" in result

    def test_large_offset_90_minutes(self):
        from app.connectors.grain import ms_offset_to_absolute
        result = ms_offset_to_absolute("2026-03-20T14:00:00Z", 5400000)
        assert "15:30:00" in result


class TestTranscriptFormatting:
    def test_single_segment(self):
        from app.connectors.grain import format_transcript, GrainTranscriptSegment
        segments = [
            GrainTranscriptSegment(
                participant_id="p1", speaker="Scott",
                start=5000, end=10000,
                text="I think we should move forward with this.",
            )
        ]
        result = format_transcript("2026-03-20T14:00:00Z", segments)
        assert "[2026-03-20T14:00:05" in result
        assert "Scott: I think we should move forward" in result

    def test_multi_segment_ordering(self):
        from app.connectors.grain import format_transcript, GrainTranscriptSegment
        segments = [
            GrainTranscriptSegment(
                participant_id="p1", speaker="Scott",
                start=5000, end=10000, text="First point.",
            ),
            GrainTranscriptSegment(
                participant_id="p2", speaker="Alex",
                start=18000, end=25000, text="Agreed, but we need details.",
            ),
        ]
        result = format_transcript("2026-03-20T14:00:00Z", segments)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "Scott:" in lines[0]
        assert "Alex:" in lines[1]

    def test_empty_transcript(self):
        from app.connectors.grain import format_transcript
        result = format_transcript("2026-03-20T14:00:00Z", [])
        assert result == ""

    def test_empty_text_segments_skipped(self):
        from app.connectors.grain import format_transcript, GrainTranscriptSegment
        segments = [
            GrainTranscriptSegment(
                participant_id="p1", speaker="Scott",
                start=5000, end=6000, text="",
            ),
            GrainTranscriptSegment(
                participant_id="p1", speaker="Scott",
                start=7000, end=10000, text="Real content.",
            ),
        ]
        result = format_transcript("2026-03-20T14:00:00Z", segments)
        lines = [line for line in result.strip().split("\n") if line.strip()]
        assert len(lines) == 1
        assert "Real content." in lines[0]


class TestBaseConnector:
    def test_cannot_instantiate_directly(self):
        from app.connectors.base import BaseConnector
        with pytest.raises(TypeError):
            BaseConnector()

    def test_grain_enum_value_exists(self):
        from app.models.ingestion.models import ContentSource
        assert ContentSource.GRAIN == "grain"

    def test_grain_enum_round_trips(self):
        from app.models.ingestion.models import ContentSource
        assert ContentSource("grain") == ContentSource.GRAIN


class TestGrainClient:
    @pytest.mark.asyncio
    async def test_list_recordings_single_page(self):
        from app.connectors.grain import GrainClient
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "recordings": [
                {"id": "rec_1", "title": "Meeting 1",
                 "start_datetime": "2026-03-20T14:00:00Z",
                 "duration_ms": 3600000}
            ],
            "next_cursor": None,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    return_value=mock_resp):
            async with GrainClient(api_key="test-key") as client:
                recordings = await client.list_recordings()
        assert len(recordings) == 1
        assert recordings[0]["id"] == "rec_1"

    @pytest.mark.asyncio
    async def test_list_recordings_pagination(self):
        from app.connectors.grain import GrainClient
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "recordings": [{"id": "rec_1", "title": "M1",
                           "start_datetime": "2026-03-20T14:00:00Z",
                           "duration_ms": 3600000}],
            "next_cursor": "cursor_abc",
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "recordings": [{"id": "rec_2", "title": "M2",
                           "start_datetime": "2026-03-21T14:00:00Z",
                           "duration_ms": 1800000}],
            "next_cursor": None,
        }

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    side_effect=[page1, page2]):
            async with GrainClient(api_key="test-key") as client:
                recordings = await client.list_recordings()
        assert len(recordings) == 2

    @pytest.mark.asyncio
    async def test_get_recording_with_transcript(self):
        from app.connectors.grain import GrainClient
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "rec_1", "title": "Weekly Sync",
            "start_datetime": "2026-03-20T14:00:00Z",
            "duration_ms": 3600000,
            "participants": ["Scott", "Alex"],
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 5000, "end": 10000, "text": "Let's begin."}
            ],
        }

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    return_value=mock_resp):
            async with GrainClient(api_key="test-key") as client:
                rec = await client.get_recording("rec_1")
        assert rec["id"] == "rec_1"
        assert len(rec["transcript"]) == 1

    @pytest.mark.asyncio
    async def test_auth_failure_raises_permission_error(self):
        from app.connectors.grain import GrainClient
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    return_value=mock_resp):
            async with GrainClient(api_key="bad-key") as client:
                with pytest.raises(PermissionError):
                    await client.list_recordings()

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self):
        from app.connectors.grain import GrainClient
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    return_value=mock_resp):
            async with GrainClient(api_key="test-key") as client:
                with pytest.raises(FileNotFoundError):
                    await client.get_recording("nonexistent")

    @pytest.mark.asyncio
    async def test_server_error_retries_then_succeeds(self):
        from app.connectors.grain import GrainClient
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.text = "Internal Server Error"

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"recordings": [], "next_cursor": None}

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
                    side_effect=[err_resp, ok_resp]):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with GrainClient(api_key="test-key") as client:
                    result = await client.list_recordings()
        assert result == []

    def test_empty_api_key_raises(self):
        from app.connectors.grain import GrainClient
        with pytest.raises(ValueError, match="API key"):
            GrainClient(api_key="")


class TestGrainConnector:
    def test_to_ingest_request_all_fields(self):
        from app.connectors.grain import GrainConnector
        from app.models.ingestion.models import ContentSource
        connector = GrainConnector(api_key="test-key")
        recording = {
            "id": "rec_abc123", "title": "Weekly Sync",
            "start_datetime": "2026-03-20T14:00:00Z",
            "end_datetime": "2026-03-20T15:00:00Z",
            "duration_ms": 3600000,
            "participants": ["Scott", "Alex"],
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 5000, "end": 10000,
                 "text": "I think we should move forward."},
                {"participant_id": "p2", "speaker": "Alex",
                 "start": 18000, "end": 25000,
                 "text": "Agreed, but we need details."},
            ],
        }
        req = connector.to_ingest_request(recording)
        assert req.source == ContentSource.GRAIN
        assert req.source_id == "grain:rec_abc123"
        assert req.title == "Weekly Sync"
        assert req.participants == ["Scott", "Alex"]
        assert "[2026-03-20T14:00:05" in req.content
        assert "Scott:" in req.content
        assert "Alex:" in req.content
        assert req.timestamp is not None
        assert req.timestamp.year == 2026
        assert req.metadata["duration_ms"] == 3600000
        assert req.metadata["grain_recording_id"] == "rec_abc123"

    def test_to_ingest_request_no_transcript(self):
        from app.connectors.grain import GrainConnector
        connector = GrainConnector(api_key="test-key")
        recording = {
            "id": "rec_abc123", "title": "No Transcript Meeting",
            "start_datetime": "2026-03-20T14:00:00Z",
            "duration_ms": 1800000, "transcript": None,
        }
        req = connector.to_ingest_request(recording)
        assert "No transcript available" in req.content
        assert req.title == "No Transcript Meeting"
        assert req.source_id == "grain:rec_abc123"

    def test_to_ingest_request_empty_transcript(self):
        from app.connectors.grain import GrainConnector
        connector = GrainConnector(api_key="test-key")
        recording = {
            "id": "rec_x", "title": "Empty",
            "start_datetime": "2026-03-20T14:00:00Z",
            "duration_ms": 60000, "transcript": [],
        }
        req = connector.to_ingest_request(recording)
        assert "No transcript available" in req.content

    def test_deduplicates_participants_from_transcript(self):
        from app.connectors.grain import GrainConnector
        connector = GrainConnector(api_key="test-key")
        recording = {
            "id": "rec_1", "title": "T",
            "start_datetime": "2026-03-20T14:00:00Z",
            "duration_ms": 60000, "participants": None,
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 0, "end": 1000, "text": "Hi"},
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 2000, "end": 3000, "text": "More"},
                {"participant_id": "p2", "speaker": "Alex",
                 "start": 4000, "end": 5000, "text": "Hello"},
            ],
        }
        req = connector.to_ingest_request(recording)
        assert sorted(req.participants) == ["Alex", "Scott"]


class TestJSONLExport:
    def test_single_request_to_jsonl(self):
        from app.connectors.grain import ingest_request_to_jsonl
        from app.models.ingestion.models import IngestRequest, ContentSource
        req = IngestRequest(
            content="[2026-03-20T14:00:05+00:00] Scott: Hello",
            source=ContentSource.GRAIN,
            source_id="grain:rec_1", title="Test Meeting",
        )
        line = ingest_request_to_jsonl(req)
        parsed = json.loads(line)
        assert parsed["source"] == "grain"
        assert parsed["source_id"] == "grain:rec_1"
        assert "Scott: Hello" in parsed["content"]

    def test_jsonl_round_trip(self):
        from app.connectors.grain import ingest_request_to_jsonl
        from app.models.ingestion.models import IngestRequest, ContentSource
        original = IngestRequest(
            content="[2026-03-20T14:00:05+00:00] Scott: Hello",
            source=ContentSource.GRAIN,
            source_id="grain:rec_1", title="Test Meeting",
            participants=["Scott"],
            metadata={"grain_recording_id": "rec_1", "duration_ms": 60000},
        )
        line = ingest_request_to_jsonl(original)
        restored = IngestRequest(**json.loads(line))
        assert restored.source == original.source
        assert restored.source_id == original.source_id
        assert restored.title == original.title
        assert restored.participants == original.participants

    def test_multiple_to_jsonl_lines(self):
        from app.connectors.grain import ingest_request_to_jsonl
        from app.models.ingestion.models import IngestRequest, ContentSource
        requests = [
            IngestRequest(
                content=f"Content {i}", source=ContentSource.GRAIN,
                source_id=f"grain:rec_{i}", title=f"Meeting {i}",
            )
            for i in range(3)
        ]
        lines = [ingest_request_to_jsonl(r) for r in requests]
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "content" in parsed


class TestErrorHandling:
    def test_malformed_segment_raises_validation_error(self):
        from app.connectors.grain import GrainTranscriptSegment
        with pytest.raises(ValidationError):
            GrainTranscriptSegment(
                participant_id="p1", speaker="Scott",
                start="not-a-number", end=10000, text="Hello",
            )

    def test_missing_required_recording_fields_raises(self):
        from app.connectors.grain import GrainRecording
        with pytest.raises(ValidationError):
            GrainRecording(title="No ID")

    def test_connector_handles_empty_text_segments(self):
        from app.connectors.grain import GrainConnector
        connector = GrainConnector(api_key="test-key")
        recording = {
            "id": "rec_bad", "title": "Malformed",
            "start_datetime": "2026-03-20T14:00:00Z",
            "duration_ms": 60000,
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 0, "end": 1000, "text": ""},
            ],
        }
        req = connector.to_ingest_request(recording)
        assert req.content  # Falls back to "No transcript available"


class TestCLI:
    @pytest.mark.asyncio
    async def test_dry_run_lists_recordings(self, capsys):
        from app.connectors.__main__ import run_export
        mock_recordings = [
            {"id": "rec_1", "title": "Meeting 1",
             "start_datetime": "2026-03-20T14:00:00Z", "duration_ms": 3600000},
            {"id": "rec_2", "title": "Meeting 2",
             "start_datetime": "2026-03-21T10:00:00Z", "duration_ms": 1800000},
        ]
        with patch("app.connectors.grain.GrainClient.list_recordings",
                    new_callable=AsyncMock, return_value=mock_recordings):
            with patch("app.connectors.grain.GrainClient.close", new_callable=AsyncMock):
                count = await run_export(
                    token="test-key", since="2026-03-01",
                    until=None, output=None, dry_run=True, stdout=False,
                )
        assert count == 2
        captured = capsys.readouterr()
        assert "rec_1" in captured.out
        assert "Meeting 1" in captured.out

    @pytest.mark.asyncio
    async def test_export_to_file(self, tmp_path):
        from app.connectors.__main__ import run_export
        mock_recordings = [
            {"id": "rec_1", "title": "M1",
             "start_datetime": "2026-03-20T14:00:00Z", "duration_ms": 3600000},
        ]
        mock_detail = {
            "id": "rec_1", "title": "M1",
            "start_datetime": "2026-03-20T14:00:00Z", "duration_ms": 3600000,
            "participants": ["Scott"],
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 0, "end": 5000, "text": "Hello world."}
            ],
        }
        output_file = tmp_path / "export.jsonl"
        with patch("app.connectors.grain.GrainClient.list_recordings",
                    new_callable=AsyncMock, return_value=mock_recordings):
            with patch("app.connectors.grain.GrainClient.get_recording",
                        new_callable=AsyncMock, return_value=mock_detail):
                with patch("app.connectors.grain.GrainClient.close", new_callable=AsyncMock):
                    count = await run_export(
                        token="test-key", since="2026-03-01",
                        until=None, output=str(output_file),
                        dry_run=False, stdout=False,
                    )
        assert count == 1
        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["source"] == "grain"
        assert parsed["source_id"] == "grain:rec_1"

    @pytest.mark.asyncio
    async def test_export_to_stdout(self, capsys):
        from app.connectors.__main__ import run_export
        mock_recordings = [
            {"id": "rec_1", "title": "M1",
             "start_datetime": "2026-03-20T14:00:00Z", "duration_ms": 3600000},
        ]
        mock_detail = {
            "id": "rec_1", "title": "M1",
            "start_datetime": "2026-03-20T14:00:00Z", "duration_ms": 3600000,
            "transcript": [
                {"participant_id": "p1", "speaker": "Scott",
                 "start": 0, "end": 5000, "text": "Hi."}
            ],
        }
        with patch("app.connectors.grain.GrainClient.list_recordings",
                    new_callable=AsyncMock, return_value=mock_recordings):
            with patch("app.connectors.grain.GrainClient.get_recording",
                        new_callable=AsyncMock, return_value=mock_detail):
                with patch("app.connectors.grain.GrainClient.close", new_callable=AsyncMock):
                    count = await run_export(
                        token="test-key", since="2026-03-01",
                        until=None, output=None, dry_run=False, stdout=True,
                    )
        assert count == 1
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["source"] == "grain"
