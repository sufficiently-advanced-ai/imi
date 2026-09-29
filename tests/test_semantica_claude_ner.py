"""Semantica's entity extraction goes through ClaudeClient (operation
semantica_ner) so inference.yaml routes it like every other model call."""

from types import SimpleNamespace

import pytest

from app.services.semantica_extraction import SemanticaExtraction
from app.services.semantica_init import ClaudeNERExtractor, create_ner_extractor


class _Client:
    def __init__(self, text=None, fail=False):
        self.text, self.fail, self.calls = text, fail, []

    async def generate_message(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("401 invalid x-api-key")
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)])


TEXT = "Dan Kauppi from Movate walked Scott through the Foley quoting app."
REPLY = """```json
{"entities": [
  {"text": "Dan Kauppi", "label": "PERSON", "confidence": 0.95},
  {"text": "Movate", "label": "ORG", "confidence": 0.9},
  {"text": "Movate", "label": "ORG", "confidence": 0.9},
  {"text": "the app", "label": "PRODUCT", "confidence": 0.3}
]}
```"""


@pytest.mark.asyncio
async def test_extraction_is_routed_through_claude_client():
    client = _Client(REPLY)
    ents = await ClaudeNERExtractor(client=client).aextract_entities(TEXT)
    assert client.calls[0]["operation"] == "semantica_ner"
    assert [(e.text, e.label) for e in ents] == [("Dan Kauppi", "PERSON"), ("Movate", "ORG")]
    assert ents[0].start_char == 0 and ents[0].end_char == len("Dan Kauppi")


@pytest.mark.asyncio
async def test_failures_return_nothing_rather_than_raise():
    assert await ClaudeNERExtractor(client=_Client(fail=True)).aextract_entities(TEXT) == []
    assert await ClaudeNERExtractor(client=_Client("not json")).aextract_entities(TEXT) == []
    assert await ClaudeNERExtractor(client=_Client(REPLY)).aextract_entities("  ") == []


@pytest.mark.asyncio
async def test_semantica_extraction_awaits_the_routed_extractor():
    ext = SemanticaExtraction(ner_extractor=create_ner_extractor(client=_Client(REPLY)),
                              duplicate_detector=None, domain_config=None)
    ents = await ext.extract_entities(TEXT)
    assert {(e["name"], e["type"]) for e in ents} == {("Dan Kauppi", "person"), ("Movate", "organization")}


def test_no_api_key_needed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(create_ner_extractor(), ClaudeNERExtractor)
