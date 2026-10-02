#!/usr/bin/env python3
"""Copy a pack's domain.yaml / lanes.yaml into config/ with a version stamp (ADR-005 §2).

Copied files start with ``# pack: <id>@<version> sha256=<hash of the rest>``.
The hash tells an untouched copy (safe to upgrade) from one an operator edited
(show the diff, ask). Used by the ``imi-onboarding`` skill; safe to run by hand.

    python scripts/install_pack.py status  freelance-implementation
    python scripts/install_pack.py diff    freelance-implementation
    python scripts/install_pack.py install freelance-implementation [--force] [--only domain|lanes]

States reported by ``status`` (per target):
  absent       nothing at the target path — install copies it
  current      untouched copy of this pack version — nothing to do
  upgradable   untouched copy of another version of this pack — install upgrades it
  edited       stamped copy whose content was changed locally — install refuses without --force
  foreign      stamped by a different pack — install refuses without --force
  unstamped    a hand-made file at the target path — install refuses without --force

``install`` never touches ACTIVE_DOMAIN or restarts anything: domain switching
is restart-only, and the skill does that step with the operator.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pack_common import REPO_ROOT, load_pack, stamp, stamp_state, strip_stamp  # noqa: E402

SAFE_STATES = {"absent", "current", "upgradable"}


@dataclass
class Target:
    kind: str  # domain | lanes
    source: Path
    dest: Path


def targets(pack, root: Path) -> list[Target]:
    out: list[Target] = []
    dom = pack.domain
    if dom.get("file"):
        out.append(Target("domain", pack.path / dom["file"], root / "config" / "domains" / f"{dom['id']}.yaml"))
    if pack.manifest.get("lanes"):
        out.append(Target("lanes", pack.path / pack.manifest["lanes"], root / "config" / "lanes.yaml"))
    return out


def state_of(t: Target, pack) -> tuple[str, str]:
    if not t.dest.exists():
        return "absent", ""
    state, fields = stamp_state(t.dest.read_text())
    if state == "unstamped":
        return "unstamped", "no pack stamp"
    if fields["id"] != pack.id:
        return "foreign", f"stamped by {fields['id']}@{fields['version']}"
    if state == "edited":
        return "edited", f"from {fields['id']}@{fields['version']}, changed locally"
    if fields["version"] == pack.version:
        # Same version, untouched — but the pack file itself may have moved on
        # without a version bump (a dev checkout). Treat that as upgradable.
        if strip_stamp(t.dest.read_text())[1] != t.source.read_text():
            return "upgradable", f"{fields['version']} (pack content changed without a version bump)"
        return "current", fields["version"]
    return "upgradable", f"{fields['version']} -> {pack.version}"


def diff(t: Target) -> str:
    old = strip_stamp(t.dest.read_text())[1].splitlines(keepends=True) if t.dest.exists() else []
    new = t.source.read_text().splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile=f"{t.dest} (installed)", tofile=f"{t.source} (pack)"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "diff", "install"])
    ap.add_argument("pack", help="pack id (directory under use-cases/) or path")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="imi checkout to install into (default: this repo)")
    ap.add_argument("--only", choices=["domain", "lanes"], help="limit to one target")
    ap.add_argument("--force", action="store_true", help="overwrite edited/foreign/unstamped copies")
    args = ap.parse_args(argv)

    pack = load_pack(args.pack)
    ts = [t for t in targets(pack, args.root) if not args.only or t.kind == args.only]
    if not ts:
        print(f"{pack.id}: nothing to copy (domain is shipped and no lanes.yaml)")
        return 0

    rc = 0
    for t in ts:
        state, detail = state_of(t, pack)
        label = f"{t.kind:6} {t.dest.relative_to(args.root) if t.dest.is_relative_to(args.root) else t.dest}"
        if args.command == "status":
            print(f"{label}: {state}{' (' + detail + ')' if detail else ''}")
        elif args.command == "diff":
            out = diff(t)
            print(f"--- {label}: {state}")
            print(out or "(no differences)")
        else:
            if state == "current":
                print(f"{label}: already current ({detail})")
                continue
            if state not in SAFE_STATES and not args.force:
                print(f"{label}: {state} ({detail}) — refusing; run `diff`, then `install --force` to overwrite")
                rc = 2
                continue
            t.dest.parent.mkdir(parents=True, exist_ok=True)
            t.dest.write_text(stamp(t.source.read_text(), pack.id, pack.version))
            print(f"{label}: installed {pack.id}@{pack.version} (was {state})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
