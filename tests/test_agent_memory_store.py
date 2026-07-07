"""Tests for AgentMemoryStore (Phase 2 of the OB1 absorption).

Agent memories persist to the git corpus at memory/agent/YYYY/MM/{id}.json
(sharded by created_at to keep directories bounded). Idempotency-key lookup
is the writeback replay guarantee; the in-process key index is a rebuildable
cache over the files.
"""

from app.models.agent_memory import AgentMemory
from app.services.agent_memory_store import AgentMemoryStore


def _make(**overrides) -> AgentMemory:
    fields: dict[str, object] = dict(
        memory_type="lesson",
        content="Batch embedding calls.",
        created_at="2026-07-03T12:00:00+00:00",
    )
    fields.update(overrides)
    return AgentMemory(**fields)  # type: ignore[arg-type]


def _store(tmp_path) -> AgentMemoryStore:
    return AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)


def test_save_shards_by_year_month(tmp_path):
    store = _store(tmp_path)
    mem = _make()
    store.save(mem)
    expected = tmp_path / "memory" / "agent" / "2026" / "07" / f"{mem.id}.json"
    assert expected.is_file()
    assert store.relative_path(mem) == f"memory/agent/2026/07/{mem.id}.json"


def test_get_roundtrip_across_shards(tmp_path):
    store = _store(tmp_path)
    old = _make(created_at="2025-12-31T23:00:00+00:00")
    new = _make(created_at="2026-07-03T12:00:00+00:00")
    store.save(old)
    store.save(new)
    assert store.get(old.id).content == old.content
    assert store.get(new.id).created_at == new.created_at
    assert store.get("missing") is None


def test_find_by_idempotency_key(tmp_path):
    store = _store(tmp_path)
    mem = _make(idempotency_key="task-1:0")
    store.save(mem)
    found = store.find_by_idempotency_key("task-1:0")
    assert found is not None
    assert found.id == mem.id
    assert store.find_by_idempotency_key("task-1:99") is None


def test_find_by_idempotency_key_sees_saves_after_index_build(tmp_path):
    """The key index is lazy but must absorb writes made through this store."""
    store = _store(tmp_path)
    store.save(_make(idempotency_key="a:0"))
    assert store.find_by_idempotency_key("a:0") is not None  # builds index
    later = _make(idempotency_key="b:0")
    store.save(later)
    assert store.find_by_idempotency_key("b:0").id == later.id


def test_list_filters_and_sorts_newest_first(tmp_path):
    store = _store(tmp_path)
    lesson = _make(memory_type="lesson", task_id="task-alpha-1")
    decision = _make(
        memory_type="decision",
        runtime_name="openclaw",
        created_at="2026-07-04T09:00:00+00:00",
        task_id="task-beta-2",
    )
    store.save(lesson)
    store.save(decision)

    assert [m.id for m in store.list()] == [decision.id, lesson.id]
    assert [m.id for m in store.list(memory_type="lesson")] == [lesson.id]
    assert [m.id for m in store.list(runtime_name="openclaw")] == [decision.id]
    assert [m.id for m in store.list(task_id_prefix="task-alpha")] == [lesson.id]
    assert store.list(review_status="confirmed") == []
    assert len(store.list(limit=1)) == 1


def test_update_overwrites_in_place(tmp_path):
    store = _store(tmp_path)
    mem = _make()
    store.save(mem)
    confirmed = mem.model_copy(
        update={
            "review_status": "confirmed",
            "provenance_status": "user_confirmed",
            "can_use_as_instruction": True,
        }
    )
    store.update(confirmed)
    assert store.get(mem.id).review_status == "confirmed"
