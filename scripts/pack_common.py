"""Shared helpers for the use-case pack scripts (ADR-005).

Used by validate_packs.py, install_pack.py, build_plugin.py and
check_pack_proof.py. Deliberately light: only PyYAML is required here, and
nothing under ``app/`` is imported except the pure Pydantic domain model (by
validate_packs.py). Facts about the platform that a pack must agree with —
MCP tool names, REST ``ContentSource`` values, the lanes.yaml keys the server
reads — are parsed from the source files rather than imported, so the scripts
run in a bare virtualenv without the backend's dependencies.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "use-cases"
CORE_SKILLS_DIR = REPO_ROOT / "skills" / "core"

PACK_ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# ADR-007 §1: the connectors an MCP intake recipe may name as `source`.
CONNECTORS = ("gmail", "gdrive", "gcal", "slack", "web", "rss", "manual")
# ADR-008 access tiers. Packs document local + relayed; remote is opt-in.
TIERS = ("local", "relayed", "remote")
# Intake tools an inbound recipe may write through, and the event-time
# argument each one takes (ADR-004 / ADR-007 §4).
INTAKE_TOOLS = {"capture_thought": "source_date", "add_call_transcript": "start_time"}

# Platform features a pack may mention. `requires` may only name stock
# features — every pack must work on a community install with every
# optional feature off (ADR-005 §2, §4).
STOCK_FEATURES = {"rest_ingest", "mcp_local"}
OPTIONAL_FEATURES = {
    "decision_model",  # config/inference.yaml routes lane_admission etc. to a judge
    "mcp_relayed",  # cloud sessions reach imi through the desktop app (ADR-008)
    "mcp_remote",  # Streamable HTTP on a private network (ADR-008)
    "claude_connectors",  # Gmail / Calendar / Drive connectors in the Claude client
    "scheduled_tasks",  # a Claude client that runs prompts on a schedule
    "git_corpus",  # GIT_REPO_URL corpus sync
}

# Top-level lanes.yaml keys. ``sources``/``drop_senders``/``owner`` are read by
# app/services/lane_admission.py today; the others are introduced by ADR-006
# (library/recall policy) and ADR-007 (mcp_trusted_sources). The validator
# warns, not fails, when a known key is not yet read by the running code.
LANES_KEYS = {"owner", "sources", "drop_senders", "mcp_trusted_sources", "library", "recall"}
LANE_VALUES = {"record", "library", "per_item"}

STAMP_RE = re.compile(r"^# pack: (?P<id>[a-z0-9-]+)@(?P<version>\S+) sha256=(?P<hash>[0-9a-f]{16})\s*$")


# ---------------------------------------------------------------------------
# Platform facts parsed from source
# ---------------------------------------------------------------------------


def known_mcp_tools(root: Path = REPO_ROOT) -> set[str]:
    """Tool names registered on the MCP surface (TOOL_DEFS + inline Tool(...))."""
    names: set[str] = set()
    defs = root / "app" / "services" / "mcp_tool_definitions.py"
    if defs.is_file():
        names |= set(re.findall(r'^\s{8}"name": "([a-z_]+)"', defs.read_text(), re.M))
    server = root / "app" / "routes" / "mcp_server.py"
    if server.is_file():
        names |= set(re.findall(r'^\s+name="([a-z_]+)"', server.read_text(), re.M))
    return names


def known_tool_params(root: Path = REPO_ROOT) -> set[str]:
    """Input-property names of the shared tool definitions (to tell params from tools)."""
    defs = root / "app" / "services" / "mcp_tool_definitions.py"
    if not defs.is_file():
        return set()
    return set(re.findall(r'^\s{16,}"([a-z_]+)": \{', defs.read_text(), re.M))


def content_sources(root: Path = REPO_ROOT) -> set[str]:
    """String values of the REST ``ContentSource`` enum (POST /api/ingest ``source``)."""
    path = root / "app" / "models" / "ingestion" / "models.py"
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ContentSource":
            return {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
            }
    raise RuntimeError(f"ContentSource enum not found in {path}")


def lanes_keys_read_by_server(root: Path = REPO_ROOT) -> set[str]:
    """Which LANES_KEYS the current lane-admission code actually reads."""
    src = (root / "app" / "services" / "lane_admission.py").read_text()
    return {k for k in LANES_KEYS if f'"{k}"' in src or f"'{k}'" in src}


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------


@dataclass
class Pack:
    path: Path
    manifest: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.manifest.get("id", self.path.name))

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "0.0.0"))

    def file(self, rel: str | None) -> Path | None:
        return (self.path / rel) if rel else None

    @property
    def domain(self) -> dict[str, Any]:
        return self.manifest.get("domain") or {}

    def skill_dirs(self) -> list[Path]:
        return [self.path / s for s in self.manifest.get("skills") or []]

    def inbound_files(self) -> list[Path]:
        return [self.path / s for s in self.manifest.get("inbound") or []]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def load_pack(pack: str | Path, packs_dir: Path = PACKS_DIR) -> Pack:
    path = Path(pack)
    if not path.is_dir():
        path = packs_dir / str(pack)
    manifest_path = path / "pack.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no pack.yaml in {path}")
    data = load_yaml(manifest_path)
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path}: top level must be a mapping")
    return Pack(path=path, manifest=data)


def list_packs(packs_dir: Path = PACKS_DIR) -> list[Path]:
    if not packs_dir.is_dir():
        return []
    return sorted(p for p in packs_dir.iterdir() if (p / "pack.yaml").is_file())


# ---------------------------------------------------------------------------
# SKILL.md frontmatter
# ---------------------------------------------------------------------------


def read_frontmatter(skill_md: Path) -> tuple[dict[str, Any] | None, str]:
    """(frontmatter dict or None, body) for a SKILL.md file."""
    text = skill_md.read_text()
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    meta = yaml.safe_load(text[4:end]) or {}
    body = text[end + 4 :].lstrip("\n")
    return (meta if isinstance(meta, dict) else None), body


# ---------------------------------------------------------------------------
# Copy-and-stamp (ADR-005 §2)
# ---------------------------------------------------------------------------


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def stamp(text: str, pack_id: str, version: str) -> str:
    """Prefix ``text`` with a ``# pack: <id>@<version> sha256=<hash>`` line."""
    body = strip_stamp(text)[1]
    return f"# pack: {pack_id}@{version} sha256={body_hash(body)}\n{body}"


def strip_stamp(text: str) -> tuple[dict[str, str] | None, str]:
    """(stamp fields or None, body without the stamp line)."""
    first, _, rest = text.partition("\n")
    m = STAMP_RE.match(first)
    if not m:
        return None, text
    return m.groupdict(), rest


def stamp_state(text: str) -> tuple[str, dict[str, str] | None]:
    """Classify an installed copy: ``unstamped`` | ``untouched`` | ``edited``."""
    fields, body = strip_stamp(text)
    if fields is None:
        return "unstamped", None
    if body_hash(body) == fields["hash"]:
        return "untouched", fields
    return "edited", fields
