"""ADR-003 lane admission: source defaults, drop rules, model thresholds,
shadow/off behaviour, library decay, the drop log, and the capture path."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import lane_admission as la
from app.services.inference.decisions import (
    ChoiceAnswer,
    DecisionResult,
    DecisionUnavailable,
    NoulAnswer,
)
from app.services.memory_capture import CaptureStore

NOW = datetime(2026, 9, 26, tzinfo=UTC)
DMARC = "# Report\n\nFrom: noreply-dmarc-support@google.com\nDate: Thu\n\n(empty body)"


class _Fake:
    def __init__(self, mode="on", memory=0.0, library=0.0, junk=0.0, durable=0.5, fail=False):
        self._mode, self.fail, self.calls = mode, fail, []
        self.probs = {"memory": memory, "library": library, "junk": junk}
        self.durable = durable

    def mode(self, operation):
        assert operation == la.LANE_OPERATION
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.calls.append(state)
        if self.fail:
            raise DecisionUnavailable("boom", status=500)
        choice = max(self.probs, key=self.probs.get)
        return DecisionResult(
            answers={
                "lane": ChoiceAnswer(choice=choice, probabilities=self.probs, confidence=0.9),
                "durable": NoulAnswer(noul=self.durable),
            },
            model="jev", endpoint="fake", input_tokens=1, output_tokens=1,
            cost_usd=0.0, latency_ms=1, raw={},
        )


@pytest.fixture(autouse=True)
def _no_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LANES_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.chdir(tmp_path)
    la.reset_lanes_config()
    yield
    la.reset_lanes_config()


def test_source_defaults():
    assert la.source_default("manual") == "record"
    assert la.source_default("Fathom") == "record"
    assert la.source_default("web") == "library"
    assert la.source_default("mail") == la.PER_ITEM
    assert la.source_default("never-heard-of-it") == la.PER_ITEM


def test_config_overrides_sources_owner_and_drop_senders(monkeypatch, tmp_path):
    cfg = tmp_path / "lanes.yaml"
    cfg.write_text("owner: Ada\nsources:\n  mail: library\ndrop_senders: [promo@shop.example]\n")
    monkeypatch.setenv("LANES_CONFIG_PATH", str(cfg))
    la.reset_lanes_config()
    assert la.source_default("mail") == "library"
    assert la.owner_name() == "Ada"
    assert la.drop_rule("From: Shop <promo@shop.example>\n", "mail")


@pytest.mark.asyncio
async def test_automated_mail_is_dropped_by_rule_without_the_model():
    fake = _Fake()
    d = await la.admit(DMARC, "mail", client=fake)
    assert d.drop and d.reason.startswith("rule:")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_record_default_sources_are_never_dropped():
    d = await la.admit(DMARC, "manual", client=_Fake(junk=0.99))
    assert not d.drop and d.lane == "record"


@pytest.mark.asyncio
async def test_per_item_source_follows_the_model_when_on():
    mine = await la.admit("Call recap with Ankit", "mail", client=_Fake(memory=0.7, library=0.3))
    assert (mine.lane, mine.drop, mine.stale_after) == ("record", False, None)

    news = await la.admit("Newsletter issue", "mail", client=_Fake(memory=0.2, library=0.8), now=NOW)
    assert news.lane == "library" and not news.drop
    assert news.stale_after is not None


@pytest.mark.asyncio
async def test_junk_needs_high_confidence_to_drop():
    assert (await la.admit("promo", "mail", client=_Fake(junk=0.85, library=0.15))).drop
    weak = await la.admit("promo?", "mail", client=_Fake(junk=0.6, library=0.4))
    assert not weak.drop and weak.lane == "library"


@pytest.mark.asyncio
async def test_fixed_library_source_keeps_its_lane_but_can_drop_junk():
    d = await la.admit("An essay I wrote?", "web", client=_Fake(memory=0.9, library=0.1))
    assert d.lane == "library"
    assert (await la.admit("Video Player is loading", "web", client=_Fake(junk=0.95))).drop


@pytest.mark.asyncio
async def test_shadow_logs_but_keeps_the_pre_lanes_behaviour():
    d = await la.admit("Newsletter", "mail", client=_Fake(mode="shadow", library=0.9, junk=0.1))
    assert d.lane == "record" and not d.drop  # per-item fallback = old behaviour
    assert d.p_library == pytest.approx(0.9)
    assert (await la.admit("junk", "web", client=_Fake(mode="shadow", junk=0.99))).drop is False


@pytest.mark.asyncio
async def test_model_off_or_failing_falls_back_to_source_default():
    off = _Fake(mode="off")
    assert (await la.admit("x", "mail", client=off)).lane == "record"
    assert off.calls == []
    assert (await la.admit("x", "web", client=off)).lane == "library"
    failed = await la.admit("x", "mail", client=_Fake(fail=True))
    assert failed.lane == "record" and "model failed" in failed.reason


@pytest.mark.parametrize("durable,days", [(None, 180), (0.1, 90), (0.5, 180), (0.9, 365)])
def test_library_decay_horizon_scales_with_durability(durable, days):
    stale = datetime.fromisoformat(la.library_stale_after(durable, NOW))
    assert (stale - NOW).days == days


def test_admission_log_is_idempotent(tmp_path):
    log = la.AdmissionLog(tmp_path)
    decision = la.LaneDecision(lane="library", drop=True, reason="rule: x")
    assert log.append(decision, content="same", source="mail", source_id="1", now=NOW)
    assert not log.append(decision, content="same", source="mail", source_id="1", now=NOW)
    assert len(log.path(NOW).read_text().splitlines()) == 1
    assert log.relative_path(NOW) == "memory/admission/2026-09.jsonl"


# ---- capture path -----------------------------------------------------------


async def _capture(tmp_path, content, source, fake):
    from app.services import capture_service, signal_indexing

    store = CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)
    git = MagicMock()
    git.commit_and_push = AsyncMock()
    with (
        patch.object(signal_indexing, "index_capture_one", lambda m: "v1"),
        patch.object(capture_service, "enrich_capture", AsyncMock(return_value={"type": "reference"})),
        patch("app.services.capture_service.git_ops", git),
    ):
        result = await capture_service.capture_and_persist(
            content, source=source, store=store, repo_root=tmp_path, decision_client=fake
        )
    return result, store


@pytest.mark.asyncio
async def test_capture_path_persists_the_admitted_lane(tmp_path):
    result, store = await _capture(tmp_path, "Newsletter issue 12", "mail", _Fake(library=0.9))
    assert result["success"] and result["lane"] == "library"
    saved = store.get(result["id"])
    assert saved.lane == "library" and saved.stale_after


@pytest.mark.asyncio
async def test_capture_path_drops_without_persisting(tmp_path):
    result, store = await _capture(tmp_path, DMARC, "mail", _Fake())
    assert result["success"] and result["dropped"] and result["id"] is None
    assert list(store.iter_all()) == []
    assert (tmp_path / "memory" / "admission").is_dir()


@pytest.mark.asyncio
async def test_dedup_keeps_the_existing_lane_without_a_new_judgment(tmp_path):
    first, store = await _capture(tmp_path, "Newsletter issue 12", "mail", _Fake(library=0.9))
    fake = _Fake(memory=0.9)
    from app.services import capture_service

    again = await capture_service.capture_and_persist(
        "Newsletter issue 12", source="mail", store=store, repo_root=tmp_path, decision_client=fake
    )
    assert again["deduped"] and again["lane"] == "library"
    assert fake.calls == []


def test_owner_falls_back_to_kb_owner_name(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "KB_OWNER_NAME", "Ada Lovelace", raising=False)
    la.reset_lanes_config()
    assert la.owner_name() == "Ada Lovelace"
    monkeypatch.setattr(settings, "KB_OWNER_NAME", None, raising=False)
    assert la.owner_name() == la.DEFAULT_OWNER
