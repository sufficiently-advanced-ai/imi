"""Phase 5 verification: durable profile corrections survive regeneration.

The corrections overlay works by injecting a human-entered, authoritative
"Corrections" block into the grounded-facts block that the profile prompt
treats as the source of truth. These tests pin that behavior:

  - corrections appear, lead the grounded facts, and override-priority is
    signalled in the header
  - an empty/missing corrections list emits NO "Corrections" header (so the
    prompt isn't polluted with an empty section)

This is the mechanism that makes a correction outlast the full-file profile
regeneration (which otherwise discards manual body edits).
"""

from unittest.mock import MagicMock, patch

from app.services.domain_aware_entity_processor import DomainAwareEntityProcessor


def _make_processor() -> DomainAwareEntityProcessor:
    with patch("app.services.domain_aware_entity_processor.git_ops", MagicMock()):
        return DomainAwareEntityProcessor(claude_client=MagicMock())


def test_corrections_lead_grounded_facts():
    processor = _make_processor()
    context = {
        "entity_id": "person-jenny-salpietro",
        "attributes": {
            "manual_corrections": [
                "Jenny is a Director, not a Manager",
                "Jenny does not work on Project Apollo",
            ],
            "reports_to": ["person-ethan"],
        },
        "content": "## Recent Signals\n- Some signal (2026-01-01)\n",
    }

    facts = processor._build_grounded_facts(context)

    # Corrections block is present, authoritative, and leads the facts.
    assert "Corrections (authoritative" in facts
    assert "Jenny is a Director, not a Manager" in facts
    assert "Jenny does not work on Project Apollo" in facts
    assert facts.index("Corrections (authoritative") < facts.index("Recent Signals")
    assert facts.index("Corrections (authoritative") < facts.index("Typed Relationships")


def test_no_corrections_header_when_empty():
    processor = _make_processor()
    for value in ([], None, ""):
        context = {
            "entity_id": "person-x",
            "attributes": {"manual_corrections": value, "reports_to": ["person-y"]},
            "content": "## Recent Signals\n- s\n",
        }
        facts = processor._build_grounded_facts(context)
        assert "Corrections" not in facts, f"unexpected header for value={value!r}"


def test_corrections_accepts_scalar_string():
    processor = _make_processor()
    context = {
        "entity_id": "person-x",
        "attributes": {"manual_corrections": "Single correction"},
        "content": "",
    }
    facts = processor._build_grounded_facts(context)
    assert "Corrections (authoritative" in facts
    assert "Single correction" in facts
