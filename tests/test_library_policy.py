"""ADR-006 §1/§2/§7: per-deployment library and recall policy in config/lanes.yaml.

Defaults reproduce ADR-003 exactly; every bad value warns and falls back to
its default (never breaks intake); decay off means library never goes stale,
without rewriting or deleting anything; recall.default_lanes changes what an
unqualified recall searches while an explicit ``lanes`` still wins.
"""

from datetime import UTC, datetime, timedelta

import pytest

import app.services.lane_admission as la
from app.models.signal import Signal
from app.services.memory_recall import RecallRequest
from app.services.signal_retrieval import index_capture

# Shared fixtures/helpers from the ADR-003 recall tests (real SqliteVectorStore).
from tests.test_recall_lanes import _cap, _Embedder, _recall, maker, store  # noqa: F401

NOW = datetime(2026, 9, 1, tzinfo=UTC)
DOMAIN = {"person", "organization", "technology", "position"}


@pytest.fixture(autouse=True)
def _config(monkeypatch, tmp_path):
    """No lanes.yaml unless a test writes one (``use``)."""
    monkeypatch.setenv("LANES_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    la.reset_lanes_config()
    yield
    la.reset_lanes_config()


@pytest.fixture
def use(monkeypatch, tmp_path):
    def write(text: str) -> None:
        cfg = tmp_path / "lanes.yaml"
        cfg.write_text(text)
        monkeypatch.setenv("LANES_CONFIG_PATH", str(cfg))
        la.reset_lanes_config()

    return write


def _days(stale_after: str) -> int:
    return (datetime.fromisoformat(stale_after) - NOW).days


# ---- defaults reproduce ADR-003 ---------------------------------------------


def test_defaults_without_a_config_file_are_adr003():
    policy = la.library_policy()
    assert policy == la.LibraryPolicy()
    assert policy.decay_enabled is True
    assert policy.horizons_days == (90, 180, 365)
    assert policy.entity_mode == "link_only" and not policy.allowlist
    assert policy.infer_relationships is False
    assert la.library_create_types(DOMAIN) == frozenset()
    assert la.recall_default_lanes() == ["record"]
    assert RecallRequest(query="q").lanes == ["record"]


@pytest.mark.parametrize(
    "durable, days", [(None, 180), (0.0, 90), (0.29, 90), (0.3, 180), (0.59, 180), (0.6, 365), (1.0, 365)]
)
def test_default_horizons_match_the_adr003_bands(durable, days):
    assert _days(la.library_stale_after(durable, NOW)) == days


def test_a_config_without_library_or_recall_keys_changes_nothing(use):
    use("sources:\n  mail: library\n")
    assert la.library_policy() == la.LibraryPolicy()
    assert la.recall_default_lanes() == ["record"]
    assert _days(la.library_stale_after(None, NOW)) == 180


def test_explicit_defaults_equal_no_config(use):
    use(
        "library:\n"
        "  decay: {enabled: true, horizons_days: [90, 180, 365]}\n"
        "  entities: {mode: link_only, create_types: []}\n"
        "  infer_relationships: false\n"
        "recall:\n  default_lanes: [record]\n"
    )
    assert la.library_policy() == la.LibraryPolicy()
    assert la.recall_default_lanes() == ["record"]


# ---- configured values --------------------------------------------------------


def test_library_primary_config_is_read(use):
    use(
        "library:\n"
        "  decay: {enabled: false}\n"
        "  entities:\n    mode: allowlist\n    create_types: [Organization, technology]\n"
        "  infer_relationships: true\n"
        "recall:\n  default_lanes: [record, library]\n"
    )
    policy = la.library_policy()
    assert policy.decay_enabled is False and policy.infer_relationships is True
    # Case-insensitive against the domain, returned as the domain spells it
    assert la.library_create_types(DOMAIN) == {"organization", "technology"}
    assert la.recall_default_lanes() == ["record", "library"]
    assert RecallRequest(query="q").lanes == ["record", "library"]


def test_custom_horizons_scale_the_durability_bands(use):
    use("library:\n  decay:\n    horizons_days: [30, 60, 730]\n")
    assert _days(la.library_stale_after(0.1, NOW)) == 30
    assert _days(la.library_stale_after(None, NOW)) == 60
    assert _days(la.library_stale_after(0.9, NOW)) == 730


def test_decay_disabled_means_no_horizon(use):
    use("library:\n  decay:\n    enabled: false\n")
    assert la.library_stale_after(0.1, NOW) is None
    assert la.library_stale_after(None, NOW) is None


def test_create_types_are_ignored_in_link_only_mode(use):
    use("library:\n  entities:\n    create_types: [organization]\n")
    assert la.library_create_types(DOMAIN) == frozenset()


# ---- validation: warn, fall back, never crash --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "library: [not, a, mapping]\n",
        "library:\n  decay: yes-please\n",
        "library:\n  decay:\n    enabled: sometimes\n",
        "library:\n  decay:\n    horizons_days: [90, 180]\n",
        "library:\n  decay:\n    horizons_days: [365, 180, 90]\n",
        "library:\n  decay:\n    horizons_days: [0, 180, 365]\n",
        "library:\n  decay:\n    horizons_days: [a, b, c]\n",
        "library:\n  entities:\n    mode: create_everything\n",
        "library:\n  entities: 7\n",
        "library:\n  infer_relationships: maybe\n",
    ],
)
def test_bad_library_values_fall_back_to_defaults(use, caplog, text):
    use(text)
    policy = la.library_policy()
    assert policy.decay_enabled is True
    assert policy.horizons_days == (90, 180, 365)
    assert policy.entity_mode == "link_only"
    assert policy.infer_relationships is False
    assert _days(la.library_stale_after(None, NOW)) == 180
    assert "lanes.yaml" in caplog.text


def test_unknown_mode_with_create_types_still_creates_nothing(use, caplog):
    use("library:\n  entities:\n    mode: allow_list\n    create_types: [organization]\n")
    assert la.library_create_types(DOMAIN) == frozenset()
    assert "library.entities.mode" in caplog.text


def test_create_types_outside_the_domain_are_dropped_with_a_warning(use, caplog):
    use("library:\n  entities:\n    mode: allowlist\n    create_types: [organization, spaceship]\n")
    assert la.library_create_types(DOMAIN) == {"organization"}
    assert "spaceship" in caplog.text


def test_create_types_without_a_readable_domain_create_nothing(use):
    use("library:\n  entities:\n    mode: allowlist\n    create_types: [organization]\n")
    assert la.library_create_types(None) == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "recall:\n  default_lanes: []\n",
        "recall:\n  default_lanes: [record, archive]\n",
        "recall:\n  default_lanes: library\n",
        "recall: [record]\n",
    ],
)
def test_bad_recall_default_lanes_fall_back_to_record(use, caplog, text):
    use(text)
    assert la.recall_default_lanes() == ["record"]
    assert RecallRequest(query="q").lanes == ["record"]


def test_unreadable_yaml_never_breaks_intake(use):
    use("library: [unclosed\n")
    assert la.library_policy() == la.LibraryPolicy()
    assert la.recall_default_lanes() == ["record"]


def test_attribution_types_default_to_the_domains_people_and_organizations():
    assert la.library_attribution_types({"person", "account", "project"}) == ("person",)
    assert la.library_attribution_types({"contact", "company"}) == ("contact", "company")
    assert la.library_attribution_types(None) == ()


def test_attribution_types_can_be_configured(use):
    use("library:\n  attribution_types: [organization, person]\n")
    assert la.library_attribution_types(DOMAIN) == ("organization", "person")


# ---- §2 fixed rules hold under any configuration ------------------------------


LIBRARY_PRIMARY = (
    "library:\n"
    "  decay: {enabled: false}\n"
    "  entities: {mode: allowlist, create_types: [organization, person]}\n"
    "  infer_relationships: true\n"
    "recall:\n  default_lanes: [library, record]\n"
)


@pytest.mark.parametrize("config", ["", LIBRARY_PRIMARY])
@pytest.mark.parametrize("extracted", ["decision", "action_item", "key_point", "claim"])
def test_library_produces_evidence_grade_claims_only(use, config, extracted):
    if config:
        use(config)
    sig = Signal(
        id="s1", type=extracted, content="x", source_meeting_id="b1",
        source_timestamp="2026-09-21T00:00:00+00:00", status="open", due_date="2026-10-01",
    )
    fields = la.library_claim_fields(
        sig, attributed_to="A Source", as_of="2026-09-21", stale_after=la.library_stale_after(None, NOW)
    )
    claim = Signal(**{**sig.model_dump(), **fields})
    assert claim.type == "claim" and claim.lane == "library"
    assert claim.status is None and claim.owner is None and claim.due_date is None
    assert claim.can_use_as_instruction is False
    assert claim.provenance_status not in {"user_confirmed", "imported"}


# ---- §7 recall ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_lanes_from_config_keep_library_under_background(use, store, maker):  # noqa: F811
    use("recall:\n  default_lanes: [record, library]\n")
    mine = _cap("Ada decided to price the pilot at 10k.")
    article = _cap("Analysts revised the outlook.", lane="library")
    for r in (mine, article):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [mine, article], maker)

    assert [m["record_id"] for m in result["memories"]] == [mine.id]
    assert [m["record_id"] for m in result["background"]] == [article.id]


@pytest.mark.asyncio
async def test_explicit_lanes_win_over_the_configured_default(use, store, maker):  # noqa: F811
    use("recall:\n  default_lanes: [record, library]\n")
    article = _cap("Analysts revised the outlook.", lane="library")
    index_capture(store, _Embedder(), article)

    result = await _recall(store, [article], maker, lanes=["record"])

    assert result["memories"] == [] and result["background"] == []


@pytest.mark.asyncio
async def test_decay_off_surfaces_already_stamped_library_without_rewriting_it(use, store, maker):  # noqa: F811
    """Decay is never deletion (ADR-003 §5): with decay on, a stale record is
    only hidden; turning decay off brings it back, horizon untouched."""
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    stale = _cap("Old analysis.", lane="library", stale_after=past)
    index_capture(store, _Embedder(), stale)

    hidden = await _recall(store, [stale], maker, lanes=["library"])
    assert hidden["background"] == []

    use("library:\n  decay:\n    enabled: false\n")
    shown = await _recall(store, [stale], maker, lanes=["library"])
    assert [m["record_id"] for m in shown["background"]] == [stale.id]
    assert shown["background"][0]["freshness"]["stale_after"] == past
    assert stale.stale_after == past


@pytest.mark.asyncio
async def test_memory_recall_tool_uses_the_configured_default(use, monkeypatch):
    """chat_tools.memory_recall (MCP + chat surfaces) passes no lanes when the
    caller names none, so the configured default applies."""
    from app.services import chat_tools
    from app.services import memory_recall as recall_service

    seen = []

    async def fake_recall(request, **kw):
        seen.append(request.lanes)
        return {"memories": [], "background": []}

    monkeypatch.setattr(recall_service, "recall", fake_recall)
    use("recall:\n  default_lanes: [record, library]\n")
    await chat_tools.memory_recall("q")
    await chat_tools.memory_recall("q", lanes=["library"])
    assert seen == [["record", "library"], ["library"]]
