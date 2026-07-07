"""Type-registry context serialization (regression).

Neo4j properties cannot hold maps: passing `context` through as a dict made
EVERY registry upsert fail with `Neo.ClientError.Statement.TypeError`
("Property values can only be of primitive types... Encountered: Map{}"),
so the `_TypeRegistry` surface never persisted anything. The fix stores
context as JSON text and decodes it on read.
"""

import json
from unittest.mock import AsyncMock

import pytest

from app.model_schemas.type_registry import TypeEntry, TypeKind, TypeStatus
from app.services.graph.type_registry import (
    TypeRegistryService,
    _decode_context,
    _row_to_entry,
)


def _entry(context=None) -> TypeEntry:
    return TypeEntry(
        name="person",
        kind=TypeKind.ENTITY,
        status=TypeStatus.CANONICAL,
        domain_id="consulting_firm",
        context=context or {},
    )


@pytest.mark.asyncio
async def test_upsert_serializes_context_to_json_text():
    client = AsyncMock()
    client.execute_write.return_value = []
    svc = TypeRegistryService(client)

    await svc._upsert(_entry(context={"source": "extraction", "n": 2}))

    params = client.execute_write.call_args.args[1]
    # A raw dict here is exactly the bug: Neo4j rejects map-valued properties.
    assert isinstance(params["context"], str)
    assert json.loads(params["context"]) == {"source": "extraction", "n": 2}


@pytest.mark.asyncio
async def test_upsert_serializes_empty_context():
    client = AsyncMock()
    client.execute_write.return_value = []
    svc = TypeRegistryService(client)

    await svc._upsert(_entry(context={}))

    params = client.execute_write.call_args.args[1]
    assert params["context"] == "{}"


def test_row_to_entry_decodes_json_context():
    row = {
        "t": {
            "name": "person",
            "kind": "entity",
            "status": "canonical",
            "domain_id": "consulting_firm",
            "usage_count": 3,
            "context": json.dumps({"source": "extraction"}),
        }
    }
    entry = _row_to_entry(row)
    assert entry is not None
    assert entry.context == {"source": "extraction"}
    assert entry.usage_count == 3


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, {}),
        ("", {}),
        ("{}", {}),
        ('{"a": 1}', {"a": 1}),
        ("not json", {}),  # malformed degrades, never drops the row
        ('["list"]', {}),  # non-dict JSON degrades
        ({"already": "dict"}, {"already": "dict"}),  # legacy passthrough
    ],
)
def test_decode_context_tolerates_all_shapes(raw, expected):
    assert _decode_context(raw) == expected


@pytest.mark.requires_neo4j
@pytest.mark.asyncio
async def test_upsert_round_trips_against_real_neo4j():
    """The bug only manifests on a real database: Neo4j rejects map-valued
    properties at write time, which mocks can't catch."""
    from app.neo4j_client import Neo4jClient

    # A dedicated client, not the module singleton: the singleton's driver
    # binds its futures to whichever event loop initialized it first, and
    # pytest-asyncio gives every test a fresh loop ("attached to a different
    # loop" errors in full-suite runs).
    client = Neo4jClient()
    await client.initialize()
    svc = TypeRegistryService(client)
    entry = TypeEntry(
        name="__test_ctx_serialization__",
        kind=TypeKind.ENTITY,
        status=TypeStatus.PROVISIONAL,
        domain_id="__test_domain__",
        context={"source": "integration-test", "n": 1},
    )
    try:
        stored = await svc._upsert(entry)
        # Pre-fix this returned None (write rejected with a logged warning).
        assert stored is not None
        assert stored.context == {"source": "integration-test", "n": 1}

        fetched = await svc.get(
            entry.name, entry.kind, entry.domain_id
        )
        assert fetched is not None
        assert fetched.context == {"source": "integration-test", "n": 1}
    finally:
        await client.execute_write(
            "MATCH (t:_TypeRegistry {name: $name, domain_id: $d}) DELETE t",
            {"name": entry.name, "d": entry.domain_id},
        )
        await client.close()
