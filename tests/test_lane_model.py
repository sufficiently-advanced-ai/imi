"""ADR-003 lane field: validated, defaults to record, never client-settable."""

import pytest
from pydantic import ValidationError

from app.models.agent_memory import AgentMemory
from app.models.captured_memory import CapturedMemory
from app.models.signal import Signal
from app.routes.captures import CaptureRequest
from app.services.memory_writeback import WritebackRequest


def _signal(**kw):
    return Signal(id="s1", type="key_point", content="c", source_meeting_id="m",
                  source_timestamp="2026-09-26T00:00:00+00:00", **kw)


@pytest.mark.parametrize("make", [
    lambda **kw: CapturedMemory(content="c", source="manual", **kw),
    lambda **kw: AgentMemory(memory_type="lesson", content="c", summary="c", **kw),
    _signal,
])
def test_lane_defaults_to_record_and_is_validated(make):
    assert make().lane == "record"
    assert make(lane="library").lane == "library"
    with pytest.raises(ValidationError):
        make(lane="world")


def test_records_written_before_lanes_load_as_record():
    legacy = '{"id": "x", "content": "c", "source": "web"}'
    assert CapturedMemory.model_validate_json(legacy).lane == "record"


def test_capture_request_does_not_accept_a_lane():
    """A client that could declare its lane could claim record-lane rights."""
    body = CaptureRequest(content="c", source="web", lane="record")
    assert "lane" not in body.model_dump()


def test_writeback_request_does_not_accept_a_lane():
    body = WritebackRequest(memory_payload={"lessons": ["l"]}, lane="library")
    assert "lane" not in body.model_dump()
