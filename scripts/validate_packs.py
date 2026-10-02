#!/usr/bin/env python3
"""Validate use-case packs (ADR-005) and the core consumption skills.

Zero-API, no running instance: checks structure only. Run before opening any
PR that touches ``use-cases/`` or ``skills/``; CI runs it nightly and on
changes (``.github/workflows/pack-proof.yml``).

    python scripts/validate_packs.py                 # every pack + core skills
    python scripts/validate_packs.py freelance-implementation
    python scripts/validate_packs.py --strict        # warnings fail too

Checks:
  - pack.yaml: required fields, id == directory, semver-ish version, every
    listed file exists, ``requires`` names only stock features, optional
    features say what changes without them
  - no Python anywhere in a pack (ADR-005 §1)
  - domain.yaml validates against app/model_schemas/domain_config.py (the
    authoritative model) and its id matches the manifest; or a named shipped
    domain exists
  - lanes.yaml: known top-level keys, lane values in record|library|per_item
  - skills (core + pack): SKILL.md with name/description frontmatter, name ==
    directory, every backticked imi tool reference names a real MCP tool
  - inbound recipes follow the ADR-007 intake contract (source = connector,
    source_id = <connector>:<native id>, event time from content)
  - sample/manifest.yaml: files exist, REST source values are valid, stable
    unique source_ids, timezone-aware event times that appear in the content
  - proof.yaml: questions with scorable facts and sane thresholds

Exit 0 when valid, 1 on errors (or warnings under --strict).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pack_common import (  # noqa: E402
    CONNECTORS,
    CORE_SKILLS_DIR,
    INTAKE_TOOLS,
    LANE_VALUES,
    LANES_KEYS,
    OPTIONAL_FEATURES,
    PACK_ID_RE,
    PACKS_DIR,
    REPO_ROOT,
    SKILL_NAME_RE,
    STOCK_FEATURES,
    TIERS,
    content_sources,
    known_mcp_tools,
    known_tool_params,
    lanes_keys_read_by_server,
    list_packs,
    load_pack,
    load_yaml,
    read_frontmatter,
)

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
# Backticked tokens that look like an imi MCP tool call. ``record_`` is left
# out of the prefix list because ``record_kinds`` is a parameter; the one
# record_* tool is listed explicitly.
TOOL_LIKE_RE = re.compile(
    r"`((?:get|list|search|find|ask|memory|capture|add|inspect|query|graph|delete|update|extract|read)_[a-z_]+"
    r"|record_memory_usage)(?:\([^`]*\))?`"
)
FETCH_TIME_WORDS = re.compile(r"\b(now|fetch(ed)?|ingest(ed|ion)?|run|retriev(al|ed)|today)\b", re.I)
# The five-section spine every pack README follows (ADR-005 §4).
SPINE_RE = re.compile(r"^## (?:\d+\.\s*)?(Install|Ontology|Lanes|Inbound|Use)\b")
MONTHS = (
    "January February March April May June July August September October November December".split()
)


_PARAMS: set[str] | None = None


def _tool_params() -> set[str]:
    global _PARAMS
    if _PARAMS is None:
        _PARAMS = known_tool_params()
    return _PARAMS


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def validate_skill(skill_dir: Path, report: Report, tools: set[str]) -> str | None:
    where = rel(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        report.error(where, "missing SKILL.md")
        return None
    meta, body = read_frontmatter(skill_md)
    if meta is None:
        report.error(where, "SKILL.md has no YAML frontmatter (--- name/description ---)")
        return None
    name = meta.get("name")
    desc = meta.get("description")
    if not name or not isinstance(name, str):
        report.error(where, "frontmatter is missing `name`")
    elif not SKILL_NAME_RE.match(name):
        report.error(where, f"skill name {name!r} must be kebab-case")
    elif name != skill_dir.name:
        report.error(where, f"skill name {name!r} must equal its directory name {skill_dir.name!r}")
    if not desc or not isinstance(desc, str) or len(desc.strip()) < 40:
        report.error(where, "frontmatter `description` missing or too short to trigger on (<40 chars)")
    for m in TOOL_LIKE_RE.finditer(body):
        if m.group(1) not in tools and m.group(1) not in _tool_params():
            report.error(where, f"references unknown MCP tool `{m.group(1)}`")
    return name if isinstance(name, str) else None


def validate_core_skills(report: Report, tools: set[str], core_dir: Path = CORE_SKILLS_DIR) -> list[str]:
    names: list[str] = []
    if not core_dir.is_dir():
        report.error(rel(core_dir), "core skills directory missing")
        return names
    for d in sorted(p for p in core_dir.iterdir() if p.is_dir()):
        name = validate_skill(d, report, tools)
        if name:
            names.append(name)
    if not names:
        report.error(rel(core_dir), "no core skills found")
    return names


# ---------------------------------------------------------------------------
# Pack pieces
# ---------------------------------------------------------------------------


def validate_domain(pack, report: Report) -> None:
    where = f"{pack.id}/pack.yaml:domain"
    domain = pack.domain
    if not domain:
        report.error(where, "missing `domain` (either `file` + `id`, or `shipped`)")
        return
    if "shipped" in domain:
        shipped = REPO_ROOT / "config" / "domains" / f"{domain['shipped']}.yaml"
        if not shipped.is_file():
            report.error(where, f"shipped domain {domain['shipped']!r} not found in config/domains/")
        return
    file_, did = domain.get("file"), domain.get("id")
    if not file_ or not did:
        report.error(where, "`domain` needs `file` and `id` (or `shipped`)")
        return
    path = pack.path / file_
    if not path.is_file():
        report.error(where, f"{file_} not found")
        return
    from pydantic import ValidationError

    from app.model_schemas.domain_config import DomainConfiguration

    data = load_yaml(path)
    if isinstance(data, dict) and "domain" in data:
        data = data["domain"]
    try:
        cfg = DomainConfiguration(**(data or {}))
    except ValidationError as e:
        report.error(f"{pack.id}/{file_}", f"does not validate against DomainConfiguration:\n{e}")
        return
    except TypeError as e:
        report.error(f"{pack.id}/{file_}", f"not a mapping: {e}")
        return
    if cfg.id != did:
        report.error(f"{pack.id}/{file_}", f"domain id {cfg.id!r} != pack.yaml domain.id {did!r}")
    for etype, ent in cfg.entities.items():
        for r in ent.relationships:
            if r.target not in cfg.entities:
                report.error(f"{pack.id}/{file_}", f"{etype}.{r.type} targets undefined entity {r.target!r}")
    if not cfg.entities:
        report.error(f"{pack.id}/{file_}", "defines no entities")


def validate_lanes(pack, report: Report) -> None:
    lanes_file = pack.manifest.get("lanes")
    if not lanes_file:
        return
    where = f"{pack.id}/{lanes_file}"
    path = pack.path / lanes_file
    if not path.is_file():
        report.error(where, "not found")
        return
    data = load_yaml(path) or {}
    if not isinstance(data, dict):
        report.error(where, "top level must be a mapping")
        return
    read_now = lanes_keys_read_by_server()
    for key in data:
        if key not in LANES_KEYS:
            report.error(where, f"unknown key {key!r} (known: {', '.join(sorted(LANES_KEYS))})")
        elif key not in read_now:
            report.warn(where, f"{key!r} is not read by app/services/lane_admission.py yet (ADR-006/007); ignored until then")
    for src, lane in (data.get("sources") or {}).items():
        if lane not in LANE_VALUES:
            report.error(where, f"sources.{src}: lane {lane!r} not in {sorted(LANE_VALUES)}")
    for key in ("drop_senders", "mcp_trusted_sources"):
        val = data.get(key)
        if val is not None and not (isinstance(val, list) and all(isinstance(v, str) for v in val)):
            report.error(where, f"{key} must be a list of strings")


def validate_inbound(pack, report: Report, tools: set[str]) -> None:
    files = pack.inbound_files()
    if not files:
        report.warn(f"{pack.id}/pack.yaml", "no inbound recipes listed")
    seen: set[str] = set()
    for path in files:
        where = f"{pack.id}/{path.relative_to(pack.path)}"
        if not path.is_file():
            report.error(where, "not found")
            continue
        r = load_yaml(path) or {}
        for k in ("id", "connector", "schedule", "tiers", "window", "writes", "prompt"):
            if k not in r:
                report.error(where, f"missing `{k}`")
        rid = r.get("id")
        if rid in seen:
            report.error(where, f"duplicate recipe id {rid!r}")
        seen.add(rid)
        conn = r.get("connector")
        if conn not in CONNECTORS:
            report.error(where, f"connector {conn!r} not one of {CONNECTORS} (ADR-007 §1)")
        tiers = r.get("tiers") or []
        bad = [t for t in tiers if t not in TIERS]
        if bad:
            report.error(where, f"unknown tiers {bad} (known: {TIERS})")
        unknown_needs = [n for n in r.get("needs") or [] if n not in OPTIONAL_FEATURES | STOCK_FEATURES]
        if unknown_needs:
            report.error(where, f"unknown needs {unknown_needs} (known: {sorted(OPTIONAL_FEATURES | STOCK_FEATURES)})")
        if "remote" in tiers:
            report.error(where, "packs document the local and relayed tiers; remote is ADR-008 — link it, don't require it")
        for i, w in enumerate(r.get("writes") or []):
            wwhere = f"{where}:writes[{i}]"
            tool = w.get("tool")
            if tool not in INTAKE_TOOLS:
                report.error(wwhere, f"tool {tool!r} must be one of {sorted(INTAKE_TOOLS)}")
                continue
            if tool not in tools:
                report.error(wwhere, f"tool {tool!r} is not registered on the MCP surface")
            if w.get("source") != conn:
                report.error(wwhere, f"source {w.get('source')!r} must equal the connector {conn!r} (ADR-007 §4)")
            sid = str(w.get("source_id") or "")
            if not sid.startswith(f"{conn}:") or len(sid) <= len(conn) + 1:
                report.error(wwhere, f"source_id {sid!r} must be '{conn}:<native id>' (ADR-007 §4)")
            if not re.search(r"\{[a-z_]+\}", sid):
                report.error(wwhere, f"source_id {sid!r} must template the native id, e.g. '{conn}:{{message_id}}'")
            et = w.get("event_time") or {}
            want = INTAKE_TOOLS[tool]
            if et.get("field") != want:
                report.error(wwhere, f"event_time.field must be {want!r} for {tool}")
            src = str(et.get("from") or "")
            if not src:
                report.error(wwhere, "event_time.from must say where in the content the time comes from")
            elif FETCH_TIME_WORDS.search(src) and "never" not in src.lower():
                report.error(wwhere, f"event_time.from {src!r} looks like fetch/ingest time; ADR-004 requires content time")
        prompt = str(r.get("prompt") or "")
        for needle in ("source_id", "source"):
            if needle not in prompt:
                report.error(where, f"prompt never mentions `{needle}` — the agent must be told the contract")
        for w in r.get("writes") or []:
            if w.get("tool") in INTAKE_TOOLS and w["tool"] not in prompt:
                report.error(where, f"prompt never names the intake tool `{w['tool']}`")
            ef = (w.get("event_time") or {}).get("field")
            if ef and ef not in prompt:
                report.error(where, f"prompt never mentions `{ef}` (event time from content)")


def _date_in_content(ts: datetime, text: str) -> bool:
    forms = {
        ts.strftime("%Y-%m-%d"),
        f"{MONTHS[ts.month - 1]} {ts.day}, {ts.year}",
        f"{ts.day} {MONTHS[ts.month - 1]} {ts.year}",
        ts.strftime("%d %b %Y"),  # RFC 2822 Date: header, e.g. "02 Mar 2026"
        f"{ts.day} {ts.strftime('%b')} {ts.year}",
    }
    return any(f in text for f in forms)


def validate_sample(pack, report: Report) -> None:
    sample = pack.manifest.get("sample")
    if not sample:
        report.error(f"{pack.id}/pack.yaml", "missing `sample` (path to sample/manifest.yaml)")
        return
    path = pack.path / sample
    where = f"{pack.id}/{sample}"
    if not path.is_file():
        report.error(where, "not found")
        return
    data = load_yaml(path) or {}
    docs = data.get("documents") or []
    if not docs:
        report.error(where, "no documents")
    sources = content_sources()
    seen: set[str] = set()
    prefix = f"sample:{pack.id}:"
    for i, d in enumerate(docs):
        dwhere = f"{where}:documents[{i}]"
        f = path.parent / str(d.get("file", ""))
        if not d.get("file") or not f.is_file():
            report.error(dwhere, f"file {d.get('file')!r} not found")
            continue
        for k in ("title", "source", "source_id", "timestamp"):
            if not d.get(k):
                report.error(dwhere, f"missing `{k}`")
        if d.get("source") not in sources:
            report.error(dwhere, f"source {d.get('source')!r} is not a REST ContentSource value {sorted(sources)}")
        sid = str(d.get("source_id") or "")
        if not sid.startswith(prefix):
            report.error(dwhere, f"source_id {sid!r} must start with {prefix!r}")
        if sid in seen:
            report.error(dwhere, f"duplicate source_id {sid!r}")
        seen.add(sid)
        ts_raw = d.get("timestamp")
        try:
            ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            report.error(dwhere, f"timestamp {ts_raw!r} is not ISO-8601")
            continue
        if ts.tzinfo is None:
            report.error(dwhere, "timestamp must carry a timezone (event time is UTC in the graph)")
        if not _date_in_content(ts, f.read_text()):
            report.error(dwhere, "timestamp's date does not appear in the document — event time must come from content (ADR-004)")
        if d.get("source") == "local_recording" and not d.get("participants"):
            report.error(dwhere, "transcripts need `participants`")


def validate_proof(pack, report: Report) -> None:
    proof = pack.manifest.get("proof")
    if not proof:
        report.error(f"{pack.id}/pack.yaml", "missing `proof`")
        return
    path = pack.path / proof
    where = f"{pack.id}/{proof}"
    if not path.is_file():
        report.error(where, "not found")
        return
    data = load_yaml(path) or {}
    rate = data.get("min_pass_rate", 1.0)
    if not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        report.error(where, "min_pass_rate must be in (0, 1]")
    qs = data.get("questions") or []
    if len(qs) < 3:
        report.error(where, "needs at least 3 questions")
    ids: set[str] = set()
    for i, q in enumerate(qs):
        qwhere = f"{where}:questions[{i}]"
        qid = q.get("id")
        if not qid:
            report.error(qwhere, "missing `id`")
        elif qid in ids:
            report.error(qwhere, f"duplicate id {qid!r}")
        ids.add(qid)
        if not q.get("ask"):
            report.error(qwhere, "missing `ask`")
        facts = q.get("facts") or []
        if not facts:
            report.error(qwhere, "missing `facts`")
        for j, fact in enumerate(facts):
            alts = fact.get("any") if isinstance(fact, dict) else None
            if not alts or not all(isinstance(a, str) and a.strip() for a in alts):
                report.error(f"{qwhere}:facts[{j}]", "each fact needs a non-empty `any:` list of strings")
        mf = q.get("min_facts", len(facts))
        if not isinstance(mf, int) or not 1 <= mf <= max(len(facts), 1):
            report.error(qwhere, f"min_facts must be between 1 and {len(facts)}")


def validate_manifest(pack, report: Report) -> None:
    m = pack.manifest
    where = f"{pack.id}/pack.yaml"
    for k in ("id", "name", "version", "audience", "status", "domain", "sample", "proof"):
        if k not in m:
            report.error(where, f"missing `{k}`")
    if m.get("id") != pack.path.name:
        report.error(where, f"id {m.get('id')!r} must equal the directory name {pack.path.name!r}")
    if not PACK_ID_RE.match(str(m.get("id", ""))):
        report.error(where, "id must be kebab-case")
    if not VERSION_RE.match(str(m.get("version", ""))):
        report.error(where, f"version {m.get('version')!r} must be semver (x.y.z)")
    if m.get("status") not in ("draft", "stable"):
        report.error(where, "status must be draft or stable")
    req = m.get("requires") or []
    for f in req:
        if f not in STOCK_FEATURES:
            report.error(where, f"requires {f!r}: packs may only require stock features {sorted(STOCK_FEATURES)} — "
                         "everything else is optional (ADR-005 §4)")
    for i, opt in enumerate(m.get("optional") or []):
        oid = opt.get("id") if isinstance(opt, dict) else None
        if oid not in OPTIONAL_FEATURES:
            report.error(where, f"optional[{i}]: id {oid!r} not one of {sorted(OPTIONAL_FEATURES)}")
        if not isinstance(opt, dict) or not opt.get("used_for") or not opt.get("without"):
            report.error(where, f"optional[{i}]: needs `used_for` and `without` (what changes when it is off)")
    readme = pack.path / "README.md"
    if not readme.is_file():
        report.error(where, "missing README.md")
    else:
        spine = ["install", "ontology", "lanes", "inbound", "use"]
        order = [
            m.group(1).lower()
            for m in (SPINE_RE.match(ln) for ln in readme.read_text().splitlines())
            if m
        ]
        if order != spine:
            report.error(f"{pack.id}/README.md", f"must have the five sections in order {spine}; found {order}")
    py = [p for p in pack.path.rglob("*") if p.suffix in (".py", ".pyc", ".pyw")]
    if py:
        report.error(where, f"packs contain no Python (ADR-005 §1): {[rel(p) for p in py]}")


def validate_pack(pack_ref, report: Report, tools: set[str], core_names: list[str]) -> None:
    try:
        pack = load_pack(pack_ref)
    except (FileNotFoundError, ValueError) as e:
        report.error(str(pack_ref), str(e))
        return
    validate_manifest(pack, report)
    validate_domain(pack, report)
    validate_lanes(pack, report)
    validate_inbound(pack, report, tools)
    for d in pack.skill_dirs():
        name = validate_skill(d, report, tools)
        if name and name in core_names:
            report.error(rel(d), f"pack skill {name!r} collides with a core skill of the same name")
    validate_sample(pack, report)
    validate_proof(pack, report)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packs", nargs="*", help="pack ids or paths (default: all under use-cases/)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    report = Report()
    tools = known_mcp_tools()
    if not tools:
        report.error("app/services/mcp_tool_definitions.py", "could not read MCP tool names")
    core_names = validate_core_skills(report, tools)
    refs = args.packs or [p.name for p in list_packs(PACKS_DIR)]
    for ref in refs:
        validate_pack(ref, report, tools, core_names)

    for w in report.warnings:
        print(f"WARN  {w}")
    for e in report.errors:
        print(f"ERROR {e}")
    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    print(
        f"{'FAIL' if failed else 'OK'}: {len(refs)} pack(s), {len(core_names)} core skill(s), "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
