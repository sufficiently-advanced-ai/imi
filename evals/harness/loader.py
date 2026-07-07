"""Fixture loading and validation.

Follows the load_pairs() conventions from scripts/eval_conflict_precision.py:
explicit ValueError on malformed input, plain dicts out. Adds a content hash
used by the baseline file to enforce fixture immutability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TASKS = ("entities", "relationships", "signals", "summary", "profiles")

_GOLD_KEYS = {
    "entities",
    "forbidden_entities",
    "relationships",
    "forbidden_relationships",
    "signals",
    "forbidden_signals",
    "summary",
    "profiles",
}


def fixture_hash(raw_text: str) -> str:
    """Stable content hash of the fixture file text."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def load_fixture(path: str | Path) -> dict:
    """Load and validate one fixture. Returns the fixture dict with two
    injected keys: _path and _hash. Raises ValueError on malformed input."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
        fixture = json.loads(raw)
    except FileNotFoundError as exc:
        raise ValueError(f"Fixture file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fixture {path.name} is not valid JSON: {exc}") from exc

    if not isinstance(fixture, dict):
        raise ValueError(f"Fixture {path.name} must be a JSON object")

    for key in ("id", "meeting", "gold"):
        if key not in fixture:
            raise ValueError(f"Fixture {path.name} missing required key: {key}")

    if fixture["id"] != path.stem:
        raise ValueError(
            f"Fixture id '{fixture['id']}' does not match filename stem '{path.stem}'"
        )

    meeting = fixture["meeting"]
    transcript = meeting.get("transcript") if isinstance(meeting, dict) else None
    if not isinstance(meeting, dict) or not isinstance(transcript, str) or not transcript.strip():
        raise ValueError(f"Fixture {path.name}: meeting.transcript is required")
    if not isinstance(meeting.get("participants"), list):
        raise ValueError(f"Fixture {path.name}: meeting.participants must be a list")

    gold = fixture["gold"]
    if not isinstance(gold, dict):
        raise ValueError(f"Fixture {path.name}: gold must be an object")
    unknown = set(gold) - _GOLD_KEYS
    if unknown:
        raise ValueError(f"Fixture {path.name}: unknown gold keys: {sorted(unknown)}")

    _validate_gold(path.name, gold)

    fixture["_path"] = str(path)
    fixture["_hash"] = fixture_hash(raw)
    return fixture


def _validate_gold(name: str, gold: dict) -> None:
    for e in gold.get("entities") or []:
        if not isinstance(e, dict):
            raise ValueError(f"Fixture {name}: gold entity must be an object: {e}")
        missing = {"canonical_id", "canonical_name", "type"} - set(e)
        if missing:
            raise ValueError(
                f"Fixture {name}: gold entity missing {sorted(missing)}: {e}"
            )
    for r in gold.get("relationships") or []:
        if not isinstance(r, dict):
            raise ValueError(f"Fixture {name}: gold relationship must be an object: {r}")
        missing = {"subject", "predicate", "object"} - set(r)
        if missing:
            raise ValueError(
                f"Fixture {name}: gold relationship missing {sorted(missing)}: {r}"
            )
    for s in gold.get("signals") or []:
        if not isinstance(s, dict):
            raise ValueError(f"Fixture {name}: gold signal must be an object: {s}")
        if "type" not in s or not (s.get("keywords_all") or s.get("keywords_any")):
            raise ValueError(
                f"Fixture {name}: gold signal needs type and keywords: {s}"
            )
    for f in gold.get("forbidden_signals") or []:
        if not isinstance(f, dict):
            raise ValueError(f"Fixture {name}: forbidden signal must be an object: {f}")
        if not (f.get("keywords_all") or f.get("keywords_any")):
            raise ValueError(f"Fixture {name}: forbidden signal needs keywords: {f}")

    profiles = gold.get("profiles")
    if profiles is not None:
        if not isinstance(profiles, dict):
            raise ValueError(f"Fixture {name}: gold profiles must be an object")
        if profiles.get("entity_type") not in ("person", "project", "team"):
            raise ValueError(
                f"Fixture {name}: gold profiles.entity_type must be "
                f"person|project|team: {profiles.get('entity_type')!r}"
            )
        entity_id = profiles.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError(
                f"Fixture {name}: gold profiles.entity_id must be a non-empty string"
            )
        forbidden_attrs = profiles.get("forbidden_attributions") or []
        if not isinstance(forbidden_attrs, list):
            raise ValueError(
                f"Fixture {name}: gold profiles.forbidden_attributions must be a list"
            )
        for fa in forbidden_attrs:
            if not isinstance(fa, dict) or not (
                fa.get("keywords_any") or fa.get("keywords_all") or fa.get("text")
            ):
                raise ValueError(
                    f"Fixture {name}: forbidden_attribution needs text or keywords: {fa}"
                )


def labeled_for(fixture: dict, task: str) -> bool:
    """Whether the fixture opts into a task (gold key present and not null)."""
    return fixture.get("gold", {}).get(task) is not None


def get_replay(fixture: dict, task: str) -> dict | None:
    """Return the recorded replay blob for a task, or None."""
    replay = fixture.get("replay") or {}
    blob = replay.get(task)
    # Use type, not truthiness: an empty-string llm_response is a valid
    # recorded output and must not be silently dropped.
    if isinstance(blob, dict) and isinstance(blob.get("llm_response"), str):
        return blob
    return None


def load_all_fixtures(directory: str | Path) -> list[dict]:
    """Load every *.json fixture in a directory, sorted by id."""
    directory = Path(directory)
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        fixtures.append(load_fixture(path))
    if not fixtures:
        raise ValueError(f"No fixtures found in {directory}")
    return fixtures
