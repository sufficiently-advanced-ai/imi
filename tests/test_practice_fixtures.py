import json
from pathlib import Path
from app.routes.ingest_zapier import ZapierTranscriptPayload

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts" / "solo_consulting"


def test_fixtures_exist_and_cover_multiple_clients():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 6, "need enough transcripts for a cross-client weekly sweep"
    clients = set()
    for f in files:
        payload = ZapierTranscriptPayload(**json.loads(f.read_text()))
        assert payload.transcript.strip()
        if payload.title and payload.title.strip():
            clients.add(payload.title.split(" ")[0])
    assert len(clients) >= 3, "need >=3 distinct clients to exercise scoping"
