"""Tests for the use-case pack tooling (ADR-005).

Pure: no running instance, no model calls. Runs in the full backend suite and,
with ``--noconftest``, in a bare venv holding only pydantic, pyyaml, httpx and
pytest (the pack-proof workflow's validate job does that).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import build_plugin  # noqa: E402
import check_pack_proof as proof  # noqa: E402
import install_pack  # noqa: E402
import pack_common  # noqa: E402
import validate_packs  # noqa: E402

PACK = "freelance-implementation"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    """A writable copy of the freelance pack under tmp_path/use-cases/."""
    dest = tmp_path / "use-cases" / PACK
    shutil.copytree(ROOT / "use-cases" / PACK, dest)
    return dest


def run_validate(pack_dir: Path) -> validate_packs.Report:
    report = validate_packs.Report()
    tools = pack_common.known_mcp_tools()
    core = validate_packs.validate_core_skills(report, tools)
    validate_packs.validate_pack(pack_dir, report, tools, core)
    return report


def edit_yaml(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def assert_error(report: validate_packs.Report, needle: str) -> None:
    assert any(needle in e for e in report.errors), f"expected error containing {needle!r}; got {report.errors}"


# ---------------------------------------------------------------------------
# platform facts parsed from source
# ---------------------------------------------------------------------------


def test_known_tools_cover_the_documented_surface():
    tools = pack_common.known_mcp_tools()
    for t in ("ask_kb", "get_constitution", "memory_recall", "find_changes", "memory_writeback",
              "capture_thought", "add_call_transcript", "record_memory_usage", "query_graph_cypher"):
        assert t in tools


def test_content_sources_match_rest_enum():
    assert {"email", "document", "local_recording"} <= pack_common.content_sources()


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------


def test_shipped_packs_and_core_skills_validate():
    assert validate_packs.main([]) == 0


def test_shipped_pack_validates_without_errors():
    report = run_validate(ROOT / "use-cases" / PACK)
    assert report.errors == []


def test_rejects_python_in_a_pack(pack_copy: Path):
    (pack_copy / "helper.py").write_text("print('no')\n")
    assert_error(run_validate(pack_copy), "packs contain no Python")


def test_rejects_requiring_an_optional_feature(pack_copy: Path):
    edit_yaml(pack_copy / "pack.yaml", lambda d: d["requires"].append("decision_model"))
    assert_error(run_validate(pack_copy), "may only require stock features")


def test_optional_feature_must_say_what_changes_without_it(pack_copy: Path):
    edit_yaml(pack_copy / "pack.yaml", lambda d: d["optional"][0].pop("without"))
    assert_error(run_validate(pack_copy), "needs `used_for` and `without`")


def test_domain_is_validated_against_the_pydantic_model(pack_copy: Path):
    def break_inverse(d):
        d["domain"]["entities"]["client"]["relationships"][0]["inverse_name"] = "nope"

    edit_yaml(pack_copy / "domain.yaml", break_inverse)
    assert_error(run_validate(pack_copy), "does not validate against DomainConfiguration")


def test_domain_id_must_match_manifest(pack_copy: Path):
    edit_yaml(pack_copy / "pack.yaml", lambda d: d["domain"].update(id="something_else"))
    assert_error(run_validate(pack_copy), "!= pack.yaml domain.id")


def test_lanes_rejects_unknown_keys_and_lane_values(pack_copy: Path):
    def bad(d):
        d["lane_overrides"] = {}
        d["sources"]["gmail"] = "inbox"

    edit_yaml(pack_copy / "lanes.yaml", bad)
    report = run_validate(pack_copy)
    assert_error(report, "unknown key 'lane_overrides'")
    assert_error(report, "sources.gmail: lane 'inbox'")


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda w: w.update(source="mail"), "must equal the connector"),
        (lambda w: w.update(source_id="{message_id}"), "must be 'gmail:<native id>'"),
        (lambda w: w.update(source_id="gmail:static"), "must template the native id"),
        (lambda w: w["event_time"].update(field="start_time"), "event_time.field must be 'source_date'"),
        (lambda w: w["event_time"].update(**{"from": "the time the task fetched it"}), "looks like fetch/ingest time"),
        (lambda w: w.update(tool="graph_add_node"), "must be one of"),
    ],
)
def test_inbound_recipe_follows_the_adr007_contract(pack_copy: Path, mutate, needle):
    edit_yaml(pack_copy / "inbound" / "gmail.yaml", lambda d: mutate(d["writes"][0]))
    assert_error(run_validate(pack_copy), needle)


def test_inbound_recipe_may_not_require_the_remote_tier(pack_copy: Path):
    edit_yaml(pack_copy / "inbound" / "gmail.yaml", lambda d: d["tiers"].append("remote"))
    assert_error(run_validate(pack_copy), "remote is ADR-008")


def test_inbound_prompt_must_state_the_contract(pack_copy: Path):
    edit_yaml(pack_copy / "inbound" / "gcal.yaml", lambda d: d.update(prompt="Read my calendar into imi."))
    report = run_validate(pack_copy)
    assert_error(report, "prompt never mentions `source_id`")
    assert_error(report, "prompt never names the intake tool `capture_thought`")


def test_skill_frontmatter_and_tool_names(pack_copy: Path):
    skill = pack_copy / "skills" / "scope-check" / "SKILL.md"
    text = skill.read_text().replace("`ask_kb`", "`ask_the_kb`", 1)
    skill.write_text(text)
    assert_error(run_validate(pack_copy), "unknown MCP tool `ask_the_kb`")

    skill.write_text("# no frontmatter\n")
    assert_error(run_validate(pack_copy), "no YAML frontmatter")


def test_tool_params_are_not_mistaken_for_tools(pack_copy: Path):
    skill = pack_copy / "skills" / "scope-check" / "SKILL.md"
    skill.write_text(skill.read_text() + "\nPass `memory_payload` and `record_kinds` as needed.\n")
    assert run_validate(pack_copy).errors == []


def test_pack_skill_may_not_shadow_a_core_skill(pack_copy: Path):
    src = pack_copy / "skills" / "scope-check"
    dst = pack_copy / "skills" / "brief"
    src.rename(dst)
    (dst / "SKILL.md").write_text((dst / "SKILL.md").read_text().replace("name: scope-check", "name: brief"))
    edit_yaml(pack_copy / "pack.yaml", lambda d: d.update(skills=["skills/brief"]))
    assert_error(run_validate(pack_copy), "collides with a core skill")


def test_sample_event_time_must_come_from_content(pack_copy: Path):
    edit_yaml(pack_copy / "sample" / "manifest.yaml",
              lambda d: d["documents"][0].update(timestamp="2026-01-15T09:00:00+00:00"))
    assert_error(run_validate(pack_copy), "event time must come from content")


def test_sample_rejects_naive_timestamps_and_bad_sources(pack_copy: Path):
    def bad(d):
        d["documents"][1]["timestamp"] = "2026-03-04T17:00:00"
        d["documents"][2]["source"] = "gmail"
        d["documents"][3]["source_id"] = "msg-4"

    edit_yaml(pack_copy / "sample" / "manifest.yaml", bad)
    report = run_validate(pack_copy)
    assert_error(report, "must carry a timezone")
    assert_error(report, "is not a REST ContentSource value")
    assert_error(report, "must start with 'sample:freelance-implementation:'")


def test_proof_needs_scorable_facts(pack_copy: Path):
    def bad(d):
        d["questions"][0]["facts"] = [{"any": []}]
        d["questions"][1]["min_facts"] = 5

    edit_yaml(pack_copy / "proof.yaml", bad)
    report = run_validate(pack_copy)
    assert_error(report, "non-empty `any:` list")
    assert_error(report, "min_facts must be between 1 and 2")


def test_readme_must_follow_the_five_section_spine(pack_copy: Path):
    readme = pack_copy / "README.md"
    readme.write_text(readme.read_text().replace("## 3. Lanes", "## 3. Sources"))
    assert_error(run_validate(pack_copy), "five sections in order")


# ---------------------------------------------------------------------------
# copy-and-stamp
# ---------------------------------------------------------------------------


def test_stamp_roundtrip_and_states():
    body = "domain:\n  id: x\n"
    stamped = pack_common.stamp(body, PACK, "1.2.3")
    assert stamped.startswith(f"# pack: {PACK}@1.2.3 sha256=")
    assert pack_common.stamp_state(stamped)[0] == "untouched"
    assert pack_common.strip_stamp(stamped)[1] == body
    assert pack_common.stamp_state(stamped + "# mine\n")[0] == "edited"
    assert pack_common.stamp_state(body)[0] == "unstamped"
    # Re-stamping a stamped text keeps exactly one stamp line.
    assert pack_common.stamp(stamped, PACK, "1.2.4").count("# pack:") == 1


def test_install_status_lifecycle(tmp_path: Path, capsys):
    root = tmp_path / "imi"
    args = ["--root", str(root)]
    assert install_pack.main(["install", PACK, *args]) == 0
    domain = root / "config" / "domains" / "freelance_implementation.yaml"
    lanes = root / "config" / "lanes.yaml"
    assert domain.read_text().startswith(f"# pack: {PACK}@")

    # The installed domain still loads the way the app loads it.
    from app.model_schemas.domain_config import DomainConfiguration

    DomainConfiguration(**yaml.safe_load(domain.read_text())["domain"])

    capsys.readouterr()
    install_pack.main(["status", PACK, *args])
    assert capsys.readouterr().out.count(": current") == 2

    # Untouched copy of an older version -> upgradable, and install upgrades it.
    lanes.write_text(pack_common.stamp(pack_common.strip_stamp(lanes.read_text())[1], PACK, "0.0.1"))
    install_pack.main(["status", PACK, *args])
    assert "upgradable" in capsys.readouterr().out
    assert install_pack.main(["install", PACK, *args]) == 0

    # Local edit -> refused without --force, diff shows it, --force replaces it.
    lanes.write_text(lanes.read_text() + "  my-crm: record\n")
    assert install_pack.main(["install", PACK, *args]) == 2
    capsys.readouterr()
    install_pack.main(["diff", PACK, *args, "--only", "lanes"])
    assert "-  my-crm: record" in capsys.readouterr().out
    assert install_pack.main(["install", PACK, *args, "--force"]) == 0
    assert "my-crm" not in lanes.read_text()

    # Hand-made file and another pack's file are both refused.
    lanes.write_text("sources: {}\n")
    assert install_pack.main(["install", PACK, *args, "--only", "lanes"]) == 2
    lanes.write_text(pack_common.stamp("sources: {}\n", "other-pack", "1.0.0"))
    capsys.readouterr()
    install_pack.main(["status", PACK, *args, "--only", "lanes"])
    assert "foreign" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# plugin generator
# ---------------------------------------------------------------------------


def test_build_plugin_layout(tmp_path: Path):
    market = build_plugin.build(PACK, tmp_path, "http://localhost:8080/api/mcp/sse")
    plugin = market / "imi"
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "imi"
    assert PACK in manifest["version"]
    skills = {p.name for p in (plugin / "skills").iterdir()}
    assert {"brief", "constitution-review", "what-changed", "memory-wrap", "scope-check"} <= skills
    for s in skills:
        assert (plugin / "skills" / s / "SKILL.md").is_file()
    mkt = json.loads((market / ".claude-plugin" / "marketplace.json").read_text())
    assert mkt["name"] == "imi-local"
    assert mkt["plugins"][0]["source"] == "./imi"
    mcp = json.loads((plugin / ".mcp.json").read_text())
    assert mcp["mcpServers"]["imi"]["type"] == "sse"


def test_build_plugin_core_only_has_no_mcp_json(tmp_path: Path):
    market = build_plugin.build(None, tmp_path, None)
    assert not (market / "imi" / ".mcp.json").exists()
    assert "scope-check" not in {p.name for p in (market / "imi" / "skills").iterdir()}


def test_build_plugin_refuses_skill_name_collisions(tmp_path: Path, pack_copy: Path):
    (pack_copy / "skills" / "scope-check" / "SKILL.md").write_text(
        "---\nname: brief\ndescription: duplicate of a core skill name for this test only\n---\nbody\n"
    )
    with pytest.raises(SystemExit, match="collision"):
        build_plugin.build(str(pack_copy), tmp_path, None)


# ---------------------------------------------------------------------------
# proof runner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, alts, hit",
    [
        ("The fixed fee is $48,000.", ["48,000"], True),
        ("Total: 48 000 USD", ["48,000"], True),
        ("Cutover moved to April 28, 2026.", ["April 28"], True),
        ("Cutover is on April 2.", ["April 28"], False),
        ("Tomas Reyes owns VPN", ["Tomás Reyes"], True),
        ("It cost 13,500", ["3,500"], False),
    ],
)
def test_fact_matching(answer, alts, hit):
    assert bool(proof.fact_matched(answer, alts)) is hit


def test_question_and_proof_thresholds():
    q = {"id": "q", "ask": "?", "facts": [{"any": ["a"]}, {"any": ["b"]}, {"any": ["c"]}], "min_facts": 2}
    assert proof.score_question(q, "a and b").passed
    assert not proof.score_question(q, "only a").passed
    assert not proof.score_question(q, "a b c", error="boom").passed
    p = proof.ProofResult(
        results=[proof.score_question(q, "a b"), proof.score_question(q, "a"),
                 proof.score_question(q, "b c"), proof.score_question(q, "a c")],
        min_pass_rate=0.75,
    )
    assert p.pass_rate == 0.75 and p.passed


def test_sample_documents_are_in_event_time_order():
    docs = proof.sample_documents(pack_common.load_pack(PACK))
    assert len(docs) == 8
    assert [d["ts"] for d in docs] == sorted(d["ts"] for d in docs)
    assert all(d["body"]["source_id"].startswith(f"sample:{PACK}:") for d in docs)


def _fake_instance(state: dict):
    """A MockTransport imi: dedups by source_id, completes jobs, fails one on demand."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/ingest":
            body = json.loads(request.content)
            state["posts"].append(body["source_id"])
            if body["source_id"] in state["seen"]:
                return httpx.Response(200, json={"job_id": "j-dup", "status": "duplicate", "poll_url": ""})
            state["seen"].add(body["source_id"])
            job = f"j-{len(state['seen'])}"
            state["jobs"][job] = "failed" if body["source_id"] in state.get("fail", ()) else "completed"
            return httpx.Response(202, json={"job_id": job, "status": "accepted", "poll_url": ""})
        if request.url.path.endswith("/status"):
            job = request.url.path.split("/")[3]
            return httpx.Response(200, json={"job_id": job, "status": state["jobs"][job], "content_type": "document"})
        return httpx.Response(404)

    return httpx.Client(base_url="http://imi.test", transport=httpx.MockTransport(handler))


def test_ingest_sample_is_idempotent(tmp_path: Path):
    docs = proof.sample_documents(pack_common.load_pack(PACK))
    state = {"posts": [], "seen": set(), "jobs": {}}
    client = _fake_instance(state)
    quiet = lambda *_: None  # noqa: E731

    ledger, errors = proof.ingest_sample(client, docs, {}, timeout=5, log=quiet, poll=0)
    assert errors == [] and len(ledger) == 8 and len(state["posts"]) == 8

    # Second run with the ledger: nothing is posted at all.
    ledger, errors = proof.ingest_sample(client, docs, ledger, timeout=5, log=quiet, poll=0)
    assert errors == [] and len(state["posts"]) == 8

    # Ledger lost (new machine): the server's source_id dedup answers, no new jobs.
    ledger, errors = proof.ingest_sample(client, docs, {}, timeout=5, log=quiet, poll=0)
    assert errors == [] and len(state["jobs"]) == 8 and len(ledger) == 8

    # The ledger file round-trips.
    path = proof.ledger_path("http://localhost:8080", PACK, base=tmp_path)
    proof.save_ledger(path, ledger)
    assert proof.load_ledger(path) == ledger


def test_ingest_sample_reports_failed_jobs():
    docs = proof.sample_documents(pack_common.load_pack(PACK))
    bad = docs[2]["body"]["source_id"]
    state = {"posts": [], "seen": set(), "jobs": {}, "fail": {bad}}
    ledger, errors = proof.ingest_sample(_fake_instance(state), docs, {}, timeout=5, log=lambda *_: None, poll=0)
    assert len(errors) == 1 and bad in errors[0]
    assert bad not in ledger and len(ledger) == 7


def test_proof_runner_dry_run():
    assert proof.main([PACK, "--dry-run"]) == 0
