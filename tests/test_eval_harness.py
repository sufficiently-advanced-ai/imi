"""Offline tests for the eval harness (evals/harness/) — no API calls.

Covers matching, scoring, loader validation, and report gating against the
committed seed fixtures plus synthetic predicted outputs.
"""

import json
from pathlib import Path

import pytest

from evals.harness import consistency, matching, scoring
from evals.harness.loader import (
    fixture_hash,
    get_replay,
    labeled_for,
    load_all_fixtures,
    load_fixture,
)
from evals.harness.report import diff_against_baseline, to_baseline

FIXTURES_DIR = Path(__file__).parent.parent / "evals" / "fixtures" / "transcripts"


# ---------------------------------------------------------------------------
# Name normalization & equivalence
# ---------------------------------------------------------------------------


class TestNameMatching:
    def test_legal_suffix_stripped_for_accounts(self):
        assert matching.names_equivalent("Acme Corp", "Acme", "account")
        assert matching.names_equivalent("Acme, Inc.", "acme", "account")

    def test_spacing_variants_equivalent(self):
        assert matching.names_equivalent("Nation Swell", "NationSwell", "account")
        assert matching.names_equivalent("Exec Online", "ExecOnline", "account")

    def test_person_titles_stripped(self):
        assert matching.names_equivalent("Dr. Sarah Chen", "Sarah Chen", "person")

    def test_different_names_not_equivalent(self):
        assert not matching.names_equivalent("Heydrich", "Hendricks", "account")
        assert not matching.names_equivalent("", "Acme", "account")

    def test_suffix_not_stripped_for_persons(self):
        # "Co" is a legal suffix for orgs but a legitimate name part for people
        assert not matching.names_equivalent("Marco", "Mar", "person")


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------

GOLD_ENTITIES = [
    {
        "canonical_id": "account-nationswell",
        "canonical_name": "NationSwell",
        "type": "account",
        "aliases": ["Nation Swell", "Nationswell"],
        "required": True,
    },
    {
        "canonical_id": "person-dana-okafor",
        "canonical_name": "Dana Okafor",
        "type": "person",
        "aliases": ["Dana"],
        "required": True,
    },
    {
        "canonical_id": "project-atlas-migration",
        "canonical_name": "Atlas Migration",
        "type": "project",
        "aliases": ["the Atlas project"],
        "required": False,
    },
]

FORBIDDEN_ENTITIES = [
    {"name": "Salesforce", "type": "account", "reason": "passing tooling mention"},
]


class TestEntityMatching:
    def test_alias_match(self):
        result = matching.match_entities(
            [{"name": "Nation Swell", "type": "account"}],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        assert result.unique_gold_matched == 1
        assert result.matched[0]["gold_id"] == "account-nationswell"

    def test_duplicate_emission_counted(self):
        result = matching.match_entities(
            [
                {"name": "Nation Swell", "type": "account"},
                {"name": "Nationswell", "type": "account"},
            ],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        assert result.unique_gold_matched == 1
        assert len(result.duplicates) == 1
        scores = scoring.score_entities(result)
        assert scores["canonicalization_rate"] == 0.5
        assert scores["duplicate_emissions"] == 1

    def test_trap_hit_is_fp(self):
        result = matching.match_entities(
            [{"name": "Salesforce", "type": "account"}],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        assert len(result.trap_hits) == 1
        assert len(result.false_positives) == 1

    def test_type_mismatch_never_matches(self):
        result = matching.match_entities(
            [{"name": "NationSwell", "type": "project"}],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        assert result.unique_gold_matched == 0
        assert len(result.false_positives) == 1

    def test_missed_required_only_counts_required(self):
        result = matching.match_entities(
            [{"name": "Dana Okafor", "type": "person"}],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        missed_ids = {m["gold_id"] for m in result.missed_required}
        assert missed_ids == {"account-nationswell"}  # optional project not penalized

    def test_scores_precision_recall(self):
        result = matching.match_entities(
            [
                {"name": "Dana", "type": "person"},
                {"name": "Salesforce", "type": "account"},
            ],
            GOLD_ENTITIES,
            FORBIDDEN_ENTITIES,
        )
        scores = scoring.score_entities(result)
        assert scores["precision"] == 0.5  # 1 TP, 1 FP
        assert scores["recall"] == 0.5  # missed nationswell (required), atlas optional
        assert scores["trap_hits"] == 1


# ---------------------------------------------------------------------------
# Relationship (triple) matching
# ---------------------------------------------------------------------------

GOLD_PEOPLE = [
    {
        "canonical_id": "person-barry-chen",
        "canonical_name": "Barry Chen",
        "type": "person",
        "aliases": ["Barry"],
    },
    {
        "canonical_id": "person-stephen-cole",
        "canonical_name": "Stephen Cole",
        "type": "person",
        "aliases": ["Stephen"],
    },
]

GOLD_RELS = [
    {
        "subject": "person-barry-chen",
        "predicate": "reports_to",
        "object": "person-stephen-cole",
        "required": True,
    },
]


class TestTripleMatching:
    def test_inverse_predicate_normalized(self):
        # "Stephen manages Barry" == "Barry reports_to Stephen"
        result = matching.match_relationships(
            [{"subject": "Stephen Cole", "predicate": "manages", "object": "Barry"}],
            GOLD_RELS,
            [],
            GOLD_PEOPLE,
        )
        assert len(result.matched) == 1
        assert not result.missed_required

    def test_direction_matters(self):
        # "Stephen reports to Barry" is WRONG direction -> FP + miss
        result = matching.match_relationships(
            [
                {
                    "subject": "Stephen Cole",
                    "predicate": "reports_to",
                    "object": "Barry Chen",
                }
            ],
            GOLD_RELS,
            [],
            GOLD_PEOPLE,
        )
        assert not result.matched
        assert len(result.false_positives) == 1
        assert len(result.missed_required) == 1

    def test_symmetric_predicate_unordered(self):
        gold = [
            {
                "subject": "person-barry-chen",
                "predicate": "collaborates_with",
                "object": "person-stephen-cole",
            }
        ]
        result = matching.match_relationships(
            [
                {
                    "subject": "Stephen",
                    "predicate": "collaborates_with",
                    "object": "Barry",
                }
            ],
            gold,
            [],
            GOLD_PEOPLE,
        )
        assert len(result.matched) == 1

    def test_unknown_predicate_is_fp(self):
        result = matching.match_relationships(
            [{"subject": "Barry", "predicate": "likes", "object": "Stephen"}],
            GOLD_RELS,
            [],
            GOLD_PEOPLE,
        )
        assert result.unknown_predicates == ["likes"]
        assert len(result.false_positives) == 1

    def test_forbidden_triple_is_trap(self):
        forbidden = [
            {
                "subject": "person-barry-chen",
                "predicate": "works_on",
                "object": "project-beard",
                "reason": "joke topic",
            }
        ]
        result = matching.match_relationships(
            [{"subject": "Barry", "predicate": "works_on", "object": "project-beard"}],
            GOLD_RELS,
            forbidden,
            GOLD_PEOPLE,
        )
        assert len(result.trap_hits) == 1
        assert len(result.false_positives) == 1


# ---------------------------------------------------------------------------
# Signal matching
# ---------------------------------------------------------------------------

GOLD_SIGNALS = [
    {
        "gold_id": "sig-deploy-gate",
        "type": "decision",
        "keywords_all": ["staging"],
        "keywords_any": ["sign-off", "gate"],
        "required": True,
    },
    {
        "gold_id": "sig-sow",
        "type": "action_item",
        "keywords_all": ["sow"],
        "keywords_any": ["friday"],
        "required": True,
    },
]

FORBIDDEN_SIGNALS = [
    {"type": "decision", "keywords_all": ["beard"], "reason": "banter"},
    {"keywords_all": ["timesheet"], "reason": "housekeeping"},
]


class TestSignalMatching:
    def test_match_and_trap(self):
        predicted = [
            {
                "type": "decision",
                "content": "All deploys go through staging with a sign-off.",
            },
            {"type": "decision", "content": "Chris decided to dye his beard grey."},
        ]
        result = matching.match_signals(predicted, GOLD_SIGNALS, FORBIDDEN_SIGNALS)
        assert len(result.matched) == 1
        assert len(result.trap_hits) == 1
        assert result.trap_hits[0]["trap"] == "banter"

    def test_typeless_forbidden_traps_any_type(self):
        predicted = [
            {"type": "action_item", "content": "Submit timesheets by tomorrow."}
        ]
        result = matching.match_signals(predicted, GOLD_SIGNALS, FORBIDDEN_SIGNALS)
        assert len(result.trap_hits) == 1

    def test_type_error_tracked_in_confusion(self):
        predicted = [
            {
                "type": "key_point",
                "content": "Deploys now require staging and a sign-off gate.",
            }
        ]
        result = matching.match_signals(predicted, GOLD_SIGNALS, FORBIDDEN_SIGNALS)
        assert len(result.type_errors) == 1
        assert result.confusion["decision"]["key_point"] == 1
        scores = scoring.score_signals(result)
        assert scores["type_errors"] == 1
        # type error is FP for predicted type and FN for gold type
        assert scores["per_type"]["key_point"]["fp"] == 1
        assert scores["per_type"]["decision"]["fn"] == 1

    def test_greedy_one_to_one(self):
        predicted = [
            {"type": "decision", "content": "Staging gate decision number one."},
            {"type": "decision", "content": "Staging gate decision duplicate."},
        ]
        result = matching.match_signals(predicted, GOLD_SIGNALS, FORBIDDEN_SIGNALS)
        assert len(result.matched) == 1  # gold consumed once
        assert len(result.false_positives) == 1

    def test_missed_required(self):
        result = matching.match_signals([], GOLD_SIGNALS, FORBIDDEN_SIGNALS)
        assert {m["gold_id"] for m in result.missed_required} == {
            "sig-deploy-gate",
            "sig-sow",
        }
        scores = scoring.score_signals(result)
        assert scores["recall"] == 0.0
        assert scores["precision"] == 1.0  # no predictions -> no FP possible


# ---------------------------------------------------------------------------
# Summary deterministic checks
# ---------------------------------------------------------------------------


class TestSummaryChecks:
    GOLD_SUMMARY = {
        "must_mention_keywords": [["staging", "deploy gate"], ["NationSwell"]],
        "must_not_mention_keywords": ["beard"],
        "title_max_words": 10,
        "title_must_contain_any": ["NationSwell", "deploy"],
    }

    def test_keyword_checks_pass(self):
        text = "The team agreed on a staging gate. NationSwell SOW goes out Friday."
        checks = matching.check_summary_keywords(text, self.GOLD_SUMMARY)
        assert not checks["mention_failures"]
        assert not checks["forbidden_hits"]

    def test_keyword_checks_fail(self):
        text = "Chris will dye his beard grey."
        checks = matching.check_summary_keywords(text, self.GOLD_SUMMARY)
        assert len(checks["mention_failures"]) == 2
        assert checks["forbidden_hits"] == ["beard"]

    def test_title_checks(self):
        assert not matching.check_title(
            "NationSwell deploy gate and SOW renewal", self.GOLD_SUMMARY
        )
        assert "untitled" in matching.check_title("Untitled Meeting", self.GOLD_SUMMARY)
        assert "missing_topic" in matching.check_title(
            "Weekly catch up", self.GOLD_SUMMARY
        )
        long_title = "A very long meeting title that exceeds the maximum allowed word count for deploy"
        assert "too_long" in matching.check_title(long_title, self.GOLD_SUMMARY)


class TestProfileChecks:
    GOLD_PROFILES = {
        "entity_type": "person",
        "entity_id": "person-sarah-chen",
        "must_mention_keywords": [["staging", "gate"]],
        "forbidden_attribution_keywords": ["latency", "mesh"],
    }

    def test_grounded_profile_passes(self):
        text = "## Recent Activities\n- Owns the staging gate rollout."
        checks = matching.check_profile_keywords(text, self.GOLD_PROFILES)
        assert not checks["mention_failures"]
        assert not checks["forbidden_attribution_hits"]

    def test_misattribution_is_caught(self):
        # A co-participant's latency/mesh work leaking onto the subject's profile.
        text = (
            "## Recent Activities\n"
            "- Looked into API latency and ran a mesh networking spike."
        )
        checks = matching.check_profile_keywords(text, self.GOLD_PROFILES)
        assert checks["mention_failures"] == [["staging", "gate"]]
        assert set(checks["forbidden_attribution_hits"]) == {"latency", "mesh"}

    def test_loader_rejects_bad_entity_type(self, tmp_path):
        from evals.harness.loader import load_fixture

        fixture = {
            "id": "x",
            "meeting": {"transcript": "**A**: hi", "participants": ["A"]},
            "gold": {"profiles": {"entity_type": "org", "entity_id": "x-1"}},
        }
        p = tmp_path / "x.json"
        p.write_text(json.dumps(fixture), encoding="utf-8")
        with pytest.raises(ValueError, match="entity_type"):
            load_fixture(p)

    def test_loader_rejects_nonlist_forbidden_attributions(self, tmp_path):
        # Malformed input must raise ValueError, not an uncaught TypeError.
        from evals.harness.loader import load_fixture

        fixture = {
            "id": "y",
            "meeting": {"transcript": "**A**: hi", "participants": ["A"]},
            "gold": {
                "profiles": {
                    "entity_type": "person",
                    "entity_id": "person-a",
                    "forbidden_attributions": 5,
                }
            },
        }
        p = tmp_path / "y.json"
        p.write_text(json.dumps(fixture), encoding="utf-8")
        with pytest.raises(ValueError, match="forbidden_attributions"):
            load_fixture(p)

    def test_runner_offline_scores_replay(self):
        import asyncio

        from app.services.prompt_loader import prompt_sha
        from evals.harness.loader import load_fixture
        from evals.harness.runners.profiles import ProfilesRunner

        fx = load_fixture(FIXTURES_DIR / "008_profile_attribution.json")
        # This test exercises the offline scoring path, not replay freshness:
        # pin the recorded sha to the current prompt so prompt edits (which
        # legitimately invalidate the replay for --offline runs) don't turn
        # this into a permanent skip.
        fx["replay"]["profiles"]["prompt_sha256"] = prompt_sha("person_update")
        result = asyncio.run(ProfilesRunner().run(fx, None, offline=True))
        assert not result.skipped
        assert result.scores["must_pass_rate"] == 1.0
        assert result.scores["attribution_violations"] == 0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_loads_committed_fixtures(self):
        fixtures = load_all_fixtures(FIXTURES_DIR)
        ids = [f["id"] for f in fixtures]
        assert "002_standup_name_variants" in ids
        assert "004_decisions_vs_opinions" in ids
        for f in fixtures:
            assert f["_hash"] == fixture_hash(
                Path(f["_path"]).read_text(encoding="utf-8")
            )

    def test_labeled_for_respects_null_optout(self):
        fixtures = {f["id"]: f for f in load_all_fixtures(FIXTURES_DIR)}
        f002 = fixtures["002_standup_name_variants"]
        f004 = fixtures["004_decisions_vs_opinions"]
        assert labeled_for(f002, "entities")
        assert not labeled_for(f002, "summary")
        assert not labeled_for(f002, "relationships")
        assert labeled_for(f004, "signals")
        assert labeled_for(f004, "summary")

    def test_get_replay_none_when_unrecorded(self):
        fixtures = load_all_fixtures(FIXTURES_DIR)
        assert get_replay(fixtures[0], "entities") is None

    def test_rejects_id_filename_mismatch(self, tmp_path):
        bad = tmp_path / "999_other.json"
        bad.write_text(
            json.dumps(
                {
                    "id": "wrong_id",
                    "meeting": {"transcript": "x", "participants": []},
                    "gold": {},
                }
            )
        )
        with pytest.raises(ValueError, match="does not match filename"):
            load_fixture(bad)

    def test_rejects_missing_transcript(self, tmp_path):
        bad = tmp_path / "999_bad.json"
        bad.write_text(
            json.dumps({"id": "999_bad", "meeting": {"participants": []}, "gold": {}})
        )
        with pytest.raises(ValueError, match="transcript"):
            load_fixture(bad)

    def test_rejects_unknown_gold_keys(self, tmp_path):
        bad = tmp_path / "999_bad2.json"
        bad.write_text(
            json.dumps(
                {
                    "id": "999_bad2",
                    "meeting": {"transcript": "x", "participants": []},
                    "gold": {"decisions": []},
                }
            )
        )
        with pytest.raises(ValueError, match="unknown gold keys"):
            load_fixture(bad)


# ---------------------------------------------------------------------------
# Report gating
# ---------------------------------------------------------------------------


class TestReportGating:
    def _report(self, precision):
        return {
            "run_id": "test",
            "tasks": {
                "entities": {
                    "micro": {
                        "precision": precision,
                        "recall": 0.9,
                        "canonicalization_rate": 1.0,
                    },
                    "per_fixture": {},
                }
            },
        }

    def test_regression_detected(self):
        baseline = to_baseline(self._report(0.95))
        regressions = diff_against_baseline(self._report(0.80), baseline)
        assert regressions and regressions[0]["metric"] == "precision"

    def test_within_tolerance_passes(self):
        baseline = to_baseline(self._report(0.95))
        assert not diff_against_baseline(self._report(0.94), baseline)

    def test_no_baseline_no_regressions(self):
        assert not diff_against_baseline(self._report(0.5), None)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_micro_average_sums_counts(self):
        per_fixture = {
            "a": {
                "tp": 3,
                "fp": 1,
                "fn": 0,
                "trap_hits": 1,
                "canonicalization_rate": 1.0,
            },
            "b": {
                "tp": 1,
                "fp": 1,
                "fn": 2,
                "trap_hits": 0,
                "canonicalization_rate": 0.5,
            },
        }
        micro = scoring.aggregate_micro(per_fixture)
        assert micro["tp"] == 4 and micro["fp"] == 2 and micro["fn"] == 2
        assert micro["precision"] == pytest.approx(4 / 6)
        assert micro["recall"] == pytest.approx(4 / 6)
        assert micro["trap_hits"] == 1
        assert micro["canonicalization_rate"] == pytest.approx(0.75)

    def test_aggregate_sums_consistency_violations(self):
        per_fixture = {
            "a": {"tp": 1, "fp": 0, "fn": 0, "consistency_violations": 2},
            "b": {"tp": 1, "fp": 0, "fn": 0, "consistency_violations": 1},
        }
        micro = scoring.aggregate_micro(per_fixture)
        assert micro["consistency_violations"] == 3


# ---------------------------------------------------------------------------
# Deterministic consistency checks (Layer 2 — non-LLM)
# ---------------------------------------------------------------------------


class TestConsistency:
    def test_decision_language_in_nondecision_is_flagged(self):
        sigs = [
            {"type": "key_point", "content": "We decided to ship Friday"},
            {"type": "insight", "content": "Honestly we'll go with the staging gate"},
        ]
        out = consistency.signal_decision_language_mismatch(sigs)
        assert len(out) == 2
        assert {o["type"] for o in out} == {"key_point", "insight"}
        assert all(o["rule"] == "decision_language_in_nondecision" for o in out)

    def test_real_decision_type_is_not_flagged(self):
        sigs = [{"type": "decision", "content": "We'll go with staging gate"}]
        assert consistency.signal_decision_language_mismatch(sigs) == []

    def test_softly_worded_nondecision_is_not_flagged(self):
        sigs = [{"type": "key_point", "content": "Onboarding timeline is a concern"}]
        assert consistency.signal_decision_language_mismatch(sigs) == []

    def test_relationship_endpoint_not_in_entity_set(self):
        gold = [
            {
                "canonical_id": "person-a",
                "canonical_name": "Alice Smith",
                "aliases": ["Alice"],
            },
        ]
        triples = [
            {"subject": "person-a", "predicate": "reports_to", "object": "person-ghost"},
            {"subject": "Alice", "predicate": "works_on", "object": "person-a"},
        ]
        out = consistency.relationship_endpoints_known(triples, gold)
        assert len(out) == 1
        assert out[0]["role"] == "object"
        assert out[0]["unknown_endpoint"] == "person-ghost"

    def test_summary_hallucinated_name_is_flagged(self):
        gold = [
            {
                "canonical_id": "person-a",
                "canonical_name": "Alice Smith",
                "aliases": ["Alice"],
            }
        ]
        transcript = "Alice Smith discussed the staging gate."
        summary = (
            "Decisions Made: Alice Smith will lead. Globex Corp was also brought up. "
            "Action Items: follow up."
        )
        out = consistency.summary_names_grounded(summary, gold, transcript)
        names = [o["name"] for o in out]
        assert "Globex Corp" in names
        # real name (in transcript) and section headers are not flagged
        assert "Alice Smith" not in names
        assert "Decisions Made" not in names
        assert "Action Items" not in names
