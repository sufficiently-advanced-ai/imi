"""Tests for capture enrichment (Phase 1 of the OB1 absorption).

Covers the LLM metadata extraction ported from OB1's ``extractMetadata``
(server/index.ts): a capture's text is classified into
type/topics/people/action_items/dates_mentioned, and EVERY failure mode
(no client, client error, empty response, unparseable JSON) degrades to the
fallback metadata — enrichment must never block capture persistence.
"""

from types import SimpleNamespace

import pytest

from app.services.capture_enrichment import FALLBACK_METADATA, enrich_capture


class FakeClaudeClient:
    """Mimics ClaudeClient.generate_message's response shape (.content[0].text)."""

    def __init__(self, response_text=None, error=None):
        self._response_text = response_text
        self._error = error
        self.calls: list[dict] = []

    async def generate_message(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._response_text is None:
            return SimpleNamespace(content=[])
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._response_text)]
        )


@pytest.mark.asyncio
async def test_enrich_parses_llm_metadata():
    client = FakeClaudeClient(
        response_text=(
            '{"type": "person_note", "topics": ["career", "consulting"],'
            ' "people": ["Sarah Chen"], "action_items": ["follow up with Sarah"],'
            ' "dates_mentioned": ["2026-07-10"]}'
        )
    )
    meta = await enrich_capture(
        "Sarah mentioned she may leave to start a consulting business.",
        claude_client=client,
    )
    assert meta["type"] == "person_note"
    assert meta["topics"] == ["career", "consulting"]
    assert meta["people"] == ["Sarah Chen"]
    assert meta["action_items"] == ["follow up with Sarah"]
    assert meta["dates_mentioned"] == ["2026-07-10"]
    # exactly one LLM call, with the capture text in the user prompt
    assert len(client.calls) == 1
    assert "Sarah" in client.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_enrich_handles_json_wrapped_in_prose():
    client = FakeClaudeClient(
        response_text=(
            'Here is the metadata:\n```json\n{"type": "task", "topics": ["ops"],'
            ' "people": [], "action_items": ["rotate keys"], "dates_mentioned": []}'
            "\n```"
        )
    )
    meta = await enrich_capture("Rotate the API keys.", claude_client=client)
    assert meta["type"] == "task"
    assert meta["action_items"] == ["rotate keys"]


@pytest.mark.asyncio
async def test_enrich_falls_back_when_client_raises():
    client = FakeClaudeClient(error=RuntimeError("api down"))
    meta = await enrich_capture("Some thought.", claude_client=client)
    assert meta == FALLBACK_METADATA


@pytest.mark.asyncio
async def test_enrich_falls_back_on_unparseable_response():
    client = FakeClaudeClient(response_text="I could not classify this, sorry!")
    meta = await enrich_capture("Some thought.", claude_client=client)
    assert meta == FALLBACK_METADATA


@pytest.mark.asyncio
async def test_enrich_falls_back_on_empty_response():
    client = FakeClaudeClient(response_text=None)
    meta = await enrich_capture("Some thought.", claude_client=client)
    assert meta == FALLBACK_METADATA


@pytest.mark.asyncio
async def test_enrich_falls_back_without_client():
    meta = await enrich_capture("Some thought.", claude_client=None)
    assert meta == FALLBACK_METADATA


@pytest.mark.asyncio
async def test_enrich_coerces_malformed_fields():
    # Unknown type → observation; scalar topics → list; missing keys → defaults.
    client = FakeClaudeClient(
        response_text='{"type": "haiku", "topics": "poetry", "people": null}'
    )
    meta = await enrich_capture("A thought.", claude_client=client)
    assert meta["type"] == "observation"
    assert meta["topics"] == ["poetry"]
    assert meta["people"] == []
    assert meta["action_items"] == []
    assert meta["dates_mentioned"] == []


def test_fallback_metadata_shape():
    assert FALLBACK_METADATA == {
        "type": "observation",
        "topics": ["uncategorized"],
        "people": [],
        "action_items": [],
        "dates_mentioned": [],
    }
