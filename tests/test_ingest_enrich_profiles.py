"""ENRICH_PROFILES: grounded profile refresh for the entities a meeting touched,
without losing graph-owned frontmatter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.domain_aware_entity_processor import DomainAwareEntityProcessor
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


def test_preserve_bookkeeping_restores_identity_and_signals():
    existing = {"id": "person-ankit-patel", "name": "Ankit Patel", "aliases": ["Ankit"],
                "merged_ids": ["person-ankit"], "source": "ingest"}
    old_body = "# Ankit Patel\n\n## Recent Signals\n- [insight] Prunes task files (CCAF, 2026-06-02)\n"
    generated = "---\nname: Ankit P.\naliases:\n- A. Patel\nrole: Founder\n---\n\n# Ankit Patel\n\nFounder.\n"

    out = DomainAwareEntityProcessor._preserve_bookkeeping(generated, existing, old_body)

    import yaml
    fm = yaml.safe_load(out.split("---", 2)[1])
    assert fm["name"] == "Ankit Patel"  # the profile writer never renames
    assert fm["aliases"] == ["Ankit", "A. Patel"]
    assert fm["merged_ids"] == ["person-ankit"]
    assert fm["role"] == "Founder"  # model-owned fields kept
    assert "## Recent Signals\n- [insight] Prunes task files" in out


@pytest.mark.asyncio
async def test_upsert_recent_signals_merges_newest_first(tmp_path, monkeypatch):
    proc = DomainAwareEntityProcessor.__new__(DomainAwareEntityProcessor)
    proc.git_ops = SimpleNamespace(repo_path=str(tmp_path))
    domain = SimpleNamespace(entities={"person": SimpleNamespace(plural="people")})
    (tmp_path / "people").mkdir()
    f = tmp_path / "people" / "dan-kauppi.md"
    f.write_text("---\nid: person-dan-kauppi\n---\n# Dan\n\n## Recent Signals\n- old line\n\n## Notes\nkeep\n")

    await proc.upsert_recent_signals("person", "person-dan-kauppi", ["- new line", "- old line"], domain)

    text = f.read_text()
    assert "## Recent Signals\n- new line\n- old line\n\n## Notes\nkeep" in text


@pytest.mark.asyncio
async def test_enrich_profiles_refreshes_templated_entities(monkeypatch):
    calls = {"signals": [], "profiles": []}

    class _Proc:
        def __init__(self, claude):
            self.git_ops = SimpleNamespace(repo_path="/repo", commit_and_push=AsyncMock())
            _Proc.instance = self

        def _get_domain_prompt_template(self, t, domain):
            return "tpl" if t in ("person", "project", "team") else None

        async def upsert_recent_signals(self, t, eid, lines, domain):
            calls["signals"].append((eid, lines))

        async def update_entity_profile(self, t, eid, trigger_files, domain):
            if eid == "person-broken":
                raise RuntimeError("model error")
            calls["profiles"].append((eid, trigger_files))

        def _get_entity_storage_path(self, t, eid, domain):
            return f"/repo/people/{eid.split('-', 1)[1]}.md"

    import app.services.domain_aware_entity_processor as dap

    monkeypatch.setattr(dap, "DomainAwareEntityProcessor", _Proc)
    domain = SimpleNamespace(entities={"person": 1, "account": 1, "project": 1, "team": 1})
    monkeypatch.setattr(
        "app.core.domain_config.domain_config_service.get_domain_config_service",
        lambda: SimpleNamespace(get_active_domain=lambda: domain),
    )
    monkeypatch.setattr("app.services.signal_store.signal_store", SimpleNamespace(save=lambda ms: None))

    graph = SimpleNamespace(ingest_files=AsyncMock())
    orch = IngestOrchestrator(classifier=None, claude_client=object(), graph=graph,
                              signal_writer=None, git_ops=None, tools={})
    obs = SimpleNamespace(entity_ids=["account-foley", "person-anudeep", "person-broken"],
                          title="Foley Quoting Discussion Cont.", occurred_at=None)
    ms = MeetingSignals(meeting_id="m", bot_id="ingest-abc", signals=[
        Signal(id="s1", type="action_item", content="Generate golden quote test data",
               source_meeting_id="ingest-abc", source_timestamp="2026-09-21T14:00:00+00:00",
               entities=[EntityRef(id="account-foley", type="account", name="Foley")],
               owner=EntityRef(id="person-anudeep", type="person", name="Anudeep")),
    ])

    result = await orch._phase_enrich_profiles(obs, ms, "ingest-abc")

    # account has no rich template -> not refreshed; broken entity isolated
    assert [e for e, _ in calls["profiles"]] == ["person-anudeep"]
    assert calls["profiles"][0][1] == ["meetings/meeting-ingest-abc.md"]
    anudeep_lines = dict(calls["signals"])["person-anudeep"]
    assert anudeep_lines == [
        "- [action_item, owner] Generate golden quote test data (Foley Quoting Discussion Cont.)"
    ]
    assert result == {"rich_profiles_generated": 1}
    _Proc.instance.git_ops.commit_and_push.assert_awaited_once()
    graph.ingest_files.assert_awaited_once_with(["people/anudeep.md"])


# --- best-effort contract (restored from the original phase tests) -----------


def _make_orch():
    return IngestOrchestrator(
        classifier=None, claude_client=object(), graph=object(), signal_writer=None, git_ops=None,
    )


class _Obs:
    def __init__(self):
        self.entity_ids = ["person-jeff-jennings", "project-apollo"]
        self.title = "Standup"
        self.occurred_at = None


class _Signals:
    bot_id = "ingest-abc123"
    signal_count = 2
    signals = []


@pytest.mark.asyncio
async def test_enrich_profiles_is_non_fatal_on_error(monkeypatch):
    class _BoomStore:
        def save(self, ms):
            raise RuntimeError("disk full")

    monkeypatch.setattr("app.services.signal_store.signal_store", _BoomStore(), raising=True)
    monkeypatch.setattr(
        "app.core.domain_config.domain_config_service.get_domain_config_service",
        lambda: (_ for _ in ()).throw(RuntimeError("no domain")),
    )
    # Must not raise — phase is best-effort.
    result = await _make_orch()._phase_enrich_profiles(_Obs(), _Signals(), "ingest-abc123")
    assert result == {"rich_profiles_generated": 0}


@pytest.mark.asyncio
async def test_enrich_profiles_skips_when_no_entities(monkeypatch):
    monkeypatch.setattr("app.services.signal_store.signal_store", SimpleNamespace(save=lambda ms: None))
    obs = _Obs()
    obs.entity_ids = []
    result = await _make_orch()._phase_enrich_profiles(obs, _Signals(), "ingest-abc123")
    assert result == {"rich_profiles_generated": 0}
