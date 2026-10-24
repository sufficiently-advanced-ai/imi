#!/usr/bin/env python3
"""Restore drill: back up a KB, destroy it, restore it, rebuild, prove it works.

"Files are the source of truth" is only true if a restore from files gives the
same knowledge base back. This drill exercises the whole path against a running
stack (docker compose by default):

  seed      generate a small synthetic KB into the corpus (scripts/
            generate_synthetic_kb.py), clean-rebuild the graph from it, confirm
            one decision through the REST review endpoint (so governance state
            and an audit JSONL row exist), backfill the memory index
  snapshot  graph node ids+labels and edge triples (kb_parity_check.snapshot),
            audit rows per signal, vectors per content type
  backup    stop the app, tar ``repo/`` + ``data/``
  wipe      delete the corpus, the data dir and every Neo4j node
  restore   untar, start the app, wait for startup
  rebuild   POST /api/admin/rebuild-graph (clean, signals re-ingested), then
            POST /api/admin/backfill-memory-index; wait for both
  verify    snapshot parity, audit rows unchanged, the confirmed decision still
            ``can_use_as_instruction``, an entity query answers, vectors back,
            ``/health/ready`` ``corpus_sync.status == synced``

Run from the repo root on the docker host (needs the ``neo4j`` Python driver
and write access to ``repo/`` and ``data/`` — files the container wrote are
root-owned, so CI runs it with sudo):

    NEO4J_PASSWORD=... python scripts/restore_drill.py \\
        --base-url http://127.0.0.1:8080 --repo-dir repo --data-dir data

THE DRILL DESTROYS THE KB IT RUNS AGAINST (it restores it from its own backup,
but run it on a scratch stack, never on one whose data you care about). It
refuses to seed a non-empty corpus unless ``--allow-existing`` is given.

Exit 0 when every assertion holds, 1 with a readable diff otherwise, 2 when a
step could not run (stack not up, timeout).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

DRILL_ACTOR = "restore-drill"
BACKUP_MEMBERS = ("repo", "data")
SYNCED = "synced"


class DrillError(RuntimeError):
    """A step could not run (exit 2), as opposed to an assertion failing."""


# ── pure helpers (unit-tested in tests/test_restore_drill.py) ─────────────────


def count_audit_rows(repo_dir: Path) -> dict[str, int]:
    """Non-empty JSONL rows per signal id under ``signals/audit``."""
    audit_dir = Path(repo_dir) / "signals" / "audit"
    counts: dict[str, int] = {}
    if not audit_dir.is_dir():
        return counts
    for path in sorted(audit_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            counts[path.stem] = sum(1 for line in f if line.strip())
    return counts


def count_vectors(data_dir: Path) -> dict[str, int]:
    """Rows per ``content_type`` in the sqlite vector store (``vectors.db``).

    Opened read-only; an absent store (another VECTOR_BACKEND) is ``{}``.
    """
    db = Path(data_dir) / "vectors.db"
    if not db.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    try:
        rows = conn.execute(
            "SELECT coalesce(content_type, ''), count(*) FROM vectors GROUP BY 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {str(kind): int(n) for kind, n in rows}


def make_backup(tar_path: Path, dirs: dict[str, Path]) -> dict[str, int]:
    """Tar each directory's *contents* under its member name. Returns file counts."""
    tar_path = Path(tar_path)
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, src in dirs.items():
            src = Path(src)
            if not src.is_dir():
                raise DrillError(f"backup source {src} is not a directory")
            tar.add(src, arcname=name)
            counts[name] = sum(1 for p in src.rglob("*") if p.is_file())
    return counts


def _safe_members(tar: tarfile.TarFile, allowed: set[str]) -> list[tarfile.TarInfo]:
    members = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if not parts or parts[0] not in allowed:
            raise DrillError(f"unexpected member in backup: {m.name!r}")
        if m.name.startswith("/") or ".." in parts:
            raise DrillError(f"unsafe path in backup: {m.name!r}")
        if m.issym() or m.islnk():
            target = Path(m.name).parent / m.linkname
            if os.path.isabs(m.linkname) or ".." in Path(os.path.normpath(target)).parts:
                raise DrillError(f"link escapes the backup: {m.name!r} -> {m.linkname!r}")
        if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
            raise DrillError(f"special file in backup: {m.name!r}")
        members.append(m)
    return members


def restore_backup(tar_path: Path, dirs: dict[str, Path]) -> dict[str, int]:
    """Extract each member back into its (existing, emptied) directory.

    Directories are restored *into*, not replaced, so bind-mount points stay
    the same inode. Returns restored file counts per member.
    """
    with tempfile.TemporaryDirectory(prefix="restore-drill-") as tmp, tarfile.open(tar_path, "r:gz") as tar:
        members = _safe_members(tar, set(dirs))
        # Members are validated above; the stdlib "data" filter (3.12+, and
        # backported) is a second line of defence where available.
        extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(tmp, members=members, **extra)  # noqa: S202
        counts: dict[str, int] = {}
        for name, dest in dirs.items():
            dest = Path(dest)
            dest.mkdir(parents=True, exist_ok=True)
            src = Path(tmp) / name
            counts[name] = 0
            if not src.is_dir():
                continue
            for child in src.iterdir():
                shutil.move(str(child), str(dest / child.name))
            counts[name] = sum(1 for p in dest.rglob("*") if p.is_file())
    return counts


def wipe_dir_contents(path: Path) -> int:
    """Delete everything inside ``path`` (keeping ``path`` itself). Returns entries removed."""
    path = Path(path)
    if not path.is_dir():
        return 0
    removed = 0
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return removed


def snapshot_to_json(nodes: set, edges: set) -> dict[str, list]:
    return {
        "nodes": sorted([list(n[:1]) + [list(n[1])] for n in nodes]),
        "edges": sorted([list(e) for e in edges]),
    }


def _as_set(items: list, kind: str) -> set:
    if kind == "nodes":
        return {(n[0], tuple(n[1])) for n in items}
    return {tuple(e) for e in items}


def compare_states(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable differences between two drill states (empty = identical)."""
    problems: list[str] = []
    for kind in ("nodes", "edges"):
        b = _as_set(before["graph"][kind], kind)
        a = _as_set(after["graph"][kind], kind)
        if a != b:
            missing, extra = sorted(b - a), sorted(a - b)
            problems.append(
                f"graph {kind}: before={len(b)} after={len(a)} "
                f"missing={len(missing)} extra={len(extra)}"
            )
            problems += [f"  - missing {kind[:-1]}: {item}" for item in missing[:25]]
            problems += [f"  + extra {kind[:-1]}: {item}" for item in extra[:25]]
    if before["audit_rows"] != after["audit_rows"]:
        keys = sorted(set(before["audit_rows"]) | set(after["audit_rows"]))
        problems.append("audit rows differ (signal: before -> after):")
        problems += [
            f"  {k}: {before['audit_rows'].get(k, 0)} -> {after['audit_rows'].get(k, 0)}"
            for k in keys
            if before["audit_rows"].get(k, 0) != after["audit_rows"].get(k, 0)
        ]
    if before["vectors"] != after["vectors"]:
        problems.append(f"vector counts differ: before={before['vectors']} after={after['vectors']}")
    return problems


def pick_decision(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A decision the drill can confirm: not yet instruction-grade, not rejected."""
    for d in decisions:
        if not d.get("can_use_as_instruction") and d.get("review_status") in (None, "pending", "unreviewed"):
            return d
    for d in decisions:
        if not d.get("can_use_as_instruction"):
            return d
    return None


def pick_entity_id(nodes: list) -> str | None:
    """First person entity id in a snapshot (stable: snapshots are sorted)."""
    for node_id, labels in nodes:
        if str(node_id).startswith("person-") and "Signal" not in labels:
            return str(node_id)
    return None


def entity_name_from_id(entity_id: str) -> str:
    """``person-elena-voss`` → ``elena voss`` (the synthetic KB's id scheme)."""
    return entity_id.split("-", 1)[-1].replace("-", " ")


def search_finds(results: Any, entity_id: str, name: str) -> bool:
    """Search results are ``{id, name, ...}`` or wrap them as ``{entity: {...}}``."""
    if not isinstance(results, list):
        return False
    for r in results:
        if not isinstance(r, dict):
            continue
        for hit in (r, r.get("entity") if isinstance(r.get("entity"), dict) else None):
            if hit and (hit.get("id") == entity_id or str(hit.get("name") or "").lower() == name.lower()):
                return True
    return False


# ── live stack ────────────────────────────────────────────────────────────────


class Stack:
    def __init__(self, args: argparse.Namespace):
        self.base = args.base_url.rstrip("/")
        self.repo = Path(args.repo_dir).resolve()
        self.data = Path(args.data_dir).resolve()
        self.timeout = args.timeout
        self.stop_cmd = args.stop_cmd
        self.start_cmd = args.start_cmd
        self.neo4j_uri = args.neo4j_uri
        self.neo4j_user = args.neo4j_user

    # -- http --
    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(f"{self.base}{path}", method=method, data=data)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")

    def get(self, path: str) -> Any:
        code, body = self.request("GET", path)
        if code != 200:
            raise DrillError(f"GET {path} -> {code}: {body}")
        return body

    def wait_for(self, what: str, check, interval: float = 3.0) -> Any:
        deadline = time.monotonic() + self.timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                ok, last = check()
                if ok:
                    return last
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                last = repr(e)
            time.sleep(interval)
        raise DrillError(f"timed out after {self.timeout}s waiting for {what}; last={last}")

    def wait_startup(self) -> None:
        def check():
            code, body = self.request("GET", "/health/startup")
            return code == 200, body
        self.wait_for("/health/startup 200", check)

    def wait_rebuild_idle(self) -> dict:
        def check():
            code, status = self.request("GET", "/api/admin/rebuild-graph/status")
            scode, startup = self.request("GET", "/health/startup")
            if code != 200 or scode != 200:
                return False, {"rebuild_status": code, "startup": scode}
            sync = (startup.get("corpus_sync") or {}).get("status")
            return status.get("state") != "running" and sync not in ("pending", "reconciling"), status
        return self.wait_for("graph rebuild / startup reconcile to finish", check)

    def rebuild(self) -> dict:
        while True:
            previous = self.wait_rebuild_idle().get("started_at")
            code, body = self.request("POST", "/api/admin/rebuild-graph?clean=true&reingest_signals=true")
            if code == 409:  # a startup rebuild beat us to it
                continue
            if code != 200:
                raise DrillError(f"rebuild-graph -> {code}: {body}")
            break

        def check():
            # Our rebuild, not the previous one: its started_at differs.
            code, status = self.request("GET", "/api/admin/rebuild-graph/status")
            ok = code == 200 and status.get("started_at") != previous and status.get("state") != "running"
            return ok, status
        status = self.wait_for("the graph rebuild to finish", check)
        if status.get("state") != "completed":
            raise DrillError(f"graph rebuild did not complete: {status}")
        return status

    def backfill_memory_index(self) -> None:
        code, body = self.request("POST", "/api/admin/backfill-memory-index")
        if code != 200:
            raise DrillError(f"backfill-memory-index -> {code}: {body}")
        task_id = body["task_id"]

        def check():
            code, task = self.request("GET", f"/api/tasks/{task_id}")
            return code == 200 and task.get("status") in ("completed", "failed"), task
        task = self.wait_for(f"memory-index backfill {task_id}", check)
        if task.get("status") != "completed":
            raise DrillError(f"memory-index backfill failed: {task}")

    # -- process control --
    def _run(self, cmd: str) -> None:
        print(f"    $ {cmd}")
        subprocess.run(shlex.split(cmd), check=True)

    def stop(self) -> None:
        self._run(self.stop_cmd)

    def start(self) -> None:
        self._run(self.start_cmd)
        self.wait_startup()

    # -- graph --
    def _driver(self):
        from neo4j import GraphDatabase

        return GraphDatabase.driver(
            self.neo4j_uri, auth=(self.neo4j_user, os.environ["NEO4J_PASSWORD"]),
            notifications_min_severity="OFF",
        )

    def graph_snapshot(self) -> dict[str, list]:
        os.environ["NEO4J_URI"] = self.neo4j_uri
        os.environ["NEO4J_USERNAME"] = self.neo4j_user
        from kb_parity_check import snapshot

        nodes, edges = snapshot()
        return snapshot_to_json(nodes, edges)

    def wipe_graph(self) -> int:
        driver = self._driver()
        deleted = 0
        try:
            with driver.session() as s:
                while True:
                    n = s.run(
                        "MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS n"
                    ).single()["n"]
                    deleted += n
                    if n == 0:
                        break
                left = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        finally:
            driver.close()
        if left:
            raise DrillError(f"graph wipe left {left} nodes")
        return deleted

    def state(self) -> dict[str, Any]:
        return {
            "graph": self.graph_snapshot(),
            "audit_rows": count_audit_rows(self.repo),
            "vectors": count_vectors(self.data),
        }


# ── phases ────────────────────────────────────────────────────────────────────


def _step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def seed(stack: Stack, allow_existing: bool) -> dict[str, Any]:
    _step("seed: synthetic KB → clean rebuild → confirm a decision → backfill index")
    stack.wait_startup()
    stack.wait_rebuild_idle()
    existing = [p for p in stack.repo.rglob("*.md") if ".git" not in p.parts]
    if existing and not allow_existing:
        raise DrillError(
            f"{stack.repo} already holds {len(existing)} markdown files — the drill destroys "
            "the KB it runs on; use a scratch stack or pass --allow-existing"
        )
    from generate_synthetic_kb import generate

    generate(str(stack.repo))
    stack.rebuild()

    listing = stack.get("/api/decisions?limit=500")
    decision = pick_decision(listing.get("decisions") or [])
    if decision is None:
        raise DrillError(f"no reviewable decision after seeding (total={listing.get('total')})")
    code, body = stack.request(
        "POST", f"/api/decisions/{decision['id']}/review", {"action": "confirm", "actor": DRILL_ACTOR}
    )
    if code != 200:
        raise DrillError(f"confirming decision {decision['id']} -> {code}: {body}")
    confirmed = stack.get(f"/api/decisions/{decision['id']}")
    if not confirmed.get("can_use_as_instruction"):
        raise DrillError(f"confirmed decision is not instruction-grade: {confirmed}")
    stack.backfill_memory_index()
    print(f"    confirmed decision {decision['id']}: {decision.get('content', '')[:80]!r}")
    return {"decision_id": decision["id"]}


def verify(stack: Stack, before: dict[str, Any], decision_id: str, entity_id: str | None) -> list[str]:
    _step("verify: parity, governance, queries, corpus sync")
    after = stack.state()
    problems = compare_states(before, after)

    decision = stack.get(f"/api/decisions/{decision_id}")
    if not decision.get("can_use_as_instruction") or decision.get("provenance_status") != "user_confirmed":
        problems.append(
            f"decision {decision_id} lost its confirmation: provenance_status="
            f"{decision.get('provenance_status')!r} can_use_as_instruction={decision.get('can_use_as_instruction')!r}"
        )
    if not any(row.get("actor") == DRILL_ACTOR for row in decision.get("audit_history") or []):
        problems.append(f"decision {decision_id} audit history has no {DRILL_ACTOR!r} row")

    if entity_id is None:
        problems.append("no person entity in the pre-backup snapshot to query")
    else:
        name = entity_name_from_id(entity_id)
        path = f"/api/domain-graph/search?query={urllib.parse.quote(name)}&max_results=50"
        code, body = stack.request("GET", path)
        if code != 200 or not search_finds(body, entity_id, name):
            problems.append(f"graph search GET {path} -> {code} did not return {entity_id}: {str(body)[:300]}")

    if not after["graph"]["nodes"]:
        problems.append("graph is empty after the rebuild")
    if not after["audit_rows"].get(decision_id):
        problems.append(f"no audit rows for {decision_id} after restore")

    # Dependency health is not this drill's concern (CI has no LLM key, so a
    # dependency may be degraded); the corpus/graph sync verdict is.
    code, ready = stack.request("GET", "/health/ready")
    sync = ready.get("corpus_sync") if isinstance(ready, dict) else None
    if not sync or sync.get("status") != SYNCED:
        problems.append(f"/health/ready -> {code}, corpus_sync={sync!r} (expected status {SYNCED!r})")
    return problems


def run(args: argparse.Namespace) -> int:
    stack = Stack(args)
    work = Path(args.work_dir or tempfile.mkdtemp(prefix="restore-drill-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    tar_path = work / "kb-backup.tar.gz"
    print(f"restore drill against {stack.base}; repo={stack.repo} data={stack.data} work={work}")

    seeded = seed(stack, args.allow_existing)
    _step("snapshot: live graph, then a clean rebuild of the same files (the restore baseline)")
    live = stack.state()
    stack.rebuild()
    before = stack.state()
    # What live writes put in the graph that a rebuild from the same files does
    # not reproduce is a parity gap (scripts/kb_parity_check.py's domain), not a
    # restore failure — report it, and fail on it only with --strict-live-parity.
    live_gap = compare_states(live, before)
    if live_gap:
        print("    WARNING: live graph != rebuild of the same files (before any backup):")
        for p in live_gap:
            print(f"      {p}")
    entity_id = pick_entity_id(before["graph"]["nodes"])
    (work / "before.json").write_text(json.dumps({**before, **seeded}, indent=1))
    print(
        f"    nodes={len(before['graph']['nodes'])} edges={len(before['graph']['edges'])} "
        f"audit_rows={sum(before['audit_rows'].values())} vectors={before['vectors']}"
    )
    if not before["vectors"]:
        print("    (no sqlite vector store — vector parity is vacuous for this VECTOR_BACKEND)")

    _step("backup: stop app, tar repo/ + data/")
    stack.stop()
    print(f"    {make_backup(tar_path, {'repo': stack.repo, 'data': stack.data})} files → {tar_path}")

    _step("wipe: corpus, data dir, graph")
    print(f"    removed repo={wipe_dir_contents(stack.repo)} data={wipe_dir_contents(stack.data)} entries")
    print(f"    deleted {stack.wipe_graph()} graph nodes")

    _step("restore: untar, start app")
    print(f"    restored {restore_backup(tar_path, {'repo': stack.repo, 'data': stack.data})} files")
    stack.start()
    sync = (stack.get("/health/startup").get("corpus_sync") or {})
    print(f"    corpus_sync after boot: {sync.get('status')} ({sync.get('reason')})")

    _step("rebuild: graph from files, memory index")
    stack.rebuild()
    stack.backfill_memory_index()

    problems = verify(stack, before, seeded["decision_id"], entity_id)
    if live_gap and args.strict_live_parity:
        problems += ["live graph != rebuild of the same files (see WARNING above)"]
    if problems:
        print("\nRESTORE DRILL FAILED:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\nRESTORE DRILL PASSED: corpus, governance history and rebuilt indexes all came back")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("IMI_API", "http://127.0.0.1:8080"))
    ap.add_argument("--repo-dir", default="repo", help="host path of the corpus mount (/app/repo)")
    ap.add_argument("--data-dir", default="data", help="host path of the data mount (/app/data)")
    ap.add_argument("--work-dir", default=None, help="where the backup tar and snapshot go (default: a temp dir)")
    ap.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"))
    ap.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USERNAME", "neo4j"))
    ap.add_argument("--stop-cmd", default="docker compose stop app")
    ap.add_argument("--start-cmd", default="docker compose start app")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per wait")
    ap.add_argument("--allow-existing", action="store_true",
                    help="seed even though the corpus is not empty (it will be destroyed and restored)")
    ap.add_argument("--strict-live-parity", action="store_true",
                    help="also fail when the live graph differs from a rebuild of the same files")
    args = ap.parse_args(argv)
    if "NEO4J_PASSWORD" not in os.environ:
        print("NEO4J_PASSWORD must be set", file=sys.stderr)
        return 2
    try:
        return run(args)
    except (DrillError, subprocess.CalledProcessError) as e:
        print(f"\nRESTORE DRILL COULD NOT RUN: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
