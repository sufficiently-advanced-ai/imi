"""The junk-name predicate must gate BOTH ingest entry points:

1. the salient extractor's parse step (drops junk at the source), and
2. the orchestrator's pre-add_node filter (defense-in-depth for entities that
   arrive from signals/observation, not just salient extraction).
"""

from app.services.salient_entity_extractor import _parse_entity_items
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


def test_parse_entity_items_drops_junk_names():
    items = [
        {"type": "person", "canonical_name": "Jeff Jennings", "salience": "subject"},
        {"type": "person", "canonical_name": "+1-571-583-8135", "salience": "subject"},
        {
            "type": "person",
            "canonical_name": "range-control\r\nelectrical-equipment-repair",
            "salience": "subject",
        },
        {"type": "person", "canonical_name": "12345", "salience": "mention"},
    ]
    out = _parse_entity_items(items, ["person"])
    names = [e["canonical_name"] for e in out]
    assert names == ["Jeff Jennings"]


def test_filter_to_domain_entities_drops_junk_names():
    entities = [
        {"id": "person-jeff-jennings", "type": "person", "name": "Jeff Jennings"},
        {"id": "person-phone", "type": "person", "name": "1-800-858-3616"},
        {"id": "person-frag", "type": "person", "name": "main-st\nlocation"},
        {"id": "project-apollo", "type": "project", "name": "Apollo"},
    ]
    # valid_types provided explicitly so the test needs no domain config.
    out = IngestOrchestrator._filter_to_domain_entities(
        entities, valid_types={"person", "project"}
    )
    names = sorted(e["name"] for e in out)
    assert names == ["Apollo", "Jeff Jennings"]
