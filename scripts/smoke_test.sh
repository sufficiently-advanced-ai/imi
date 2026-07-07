#!/usr/bin/env bash
# smoke_test.sh — verify the imi community core actually builds, boots, and ingests.
#
# Catches the class of bug where the open-core sync drops a symbol (e.g. a
# module-level singleton) whose dependency is still core: the file imports fine
# in isolation, the app may even start, but a consumer explodes at runtime.
#
# Two phases:
#   1. import-sweep  — build the image, import every app.services/app.routes
#                      module inside it. NO API key required. Catches dropped
#                      symbols / broken imports deterministically and fast.
#   2. pipeline      — boot the full stack and drive a real ingestion end to
#                      end, asserting the knowledge graph populates. Requires
#                      ANTHROPIC_API_KEY (skipped with a warning if unset).
#
# Usage:
#   ./scripts/smoke_test.sh              # both phases (pipeline auto-skips w/o key)
#   ./scripts/smoke_test.sh --quick      # import-sweep only (no docker run/up)
#   ANTHROPIC_API_KEY=sk-... ./scripts/smoke_test.sh
#
# Exit non-zero on any failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

PROJECT="imi-smoke-$$"
IMAGE="imi-smoke:$$"
QUICK_ONLY=0
[ "${1:-}" = "--quick" ] && QUICK_ONLY=1

PORT="${SMOKE_PORT:-8080}"
NEO4J_PW="smoke-$$"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
log()   { printf '\n==> %s\n' "$*"; }

cleanup() {
  log "Teardown"
  docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  docker rmi "$IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Phase 1: import sweep (no API key, no running services) ────────────────────
log "Phase 1: build image + import sweep"
docker build -q -f Dockerfile.dev -t "$IMAGE" \
  --build-arg NEXT_PUBLIC_API_URL=/api --build-arg NEXT_PUBLIC_AUTH_MODE=none . >/dev/null
green "  image built"

# Import every submodule of app.services and app.routes. A dropped module-global
# (the signal_store class of bug) surfaces here as an ImportError, even when the
# consumer's own import is lazy and the app would otherwise start cleanly.
# --entrypoint python: the image ENTRYPOINT (entrypoint.sh) ignores its args and
# boots supervisord, so without the override this command would hang forever.
docker run --rm --entrypoint python -e ANTHROPIC_API_KEY=import-check-only "$IMAGE" -c '
import importlib, pkgutil, sys, app

# Every module under app.services / app.routes must import standalone.
failures = []
for pkg in ("app.services", "app.routes"):
    try:
        mod = importlib.import_module(pkg)
    except Exception as e:
        failures.append(f"{pkg}: {type(e).__name__}: {e}")
        continue
    for info in pkgutil.walk_packages(mod.__path__, pkg + "."):
        try:
            importlib.import_module(info.name)
        except Exception as e:
            failures.append(f"{info.name}: {type(e).__name__}: {e}")
if failures:
    print("IMPORT SWEEP FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("import sweep OK")
'
green "  import sweep passed"

if [ "$QUICK_ONLY" = "1" ]; then
  green "PASS (import-sweep only)"
  exit 0
fi

# ── Phase 2: full pipeline ─────────────────────────────────────────────────────
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  red "  ANTHROPIC_API_KEY unset — skipping pipeline phase (import sweep passed)."
  exit 0
fi

log "Phase 2: boot stack + end-to-end ingestion"
export NEO4J_PASSWORD="$NEO4J_PW" PORT="$PORT"
docker compose -p "$PROJECT" up -d --build >/dev/null
green "  stack starting"

# Wait for app health (compose healthcheck hits /health).
log "Waiting for app to become healthy"
for i in $(seq 1 40); do
  state=$(docker inspect -f '{{.State.Health.Status}}' imi-app 2>/dev/null || echo "starting")
  [ "$state" = "healthy" ] && { green "  app healthy (~$((i*5))s)"; break; }
  [ "$i" = "40" ] && { red "  app never became healthy"; docker logs imi-app 2>&1 | tail -30; exit 1; }
  sleep 5
done

base="http://127.0.0.1:${PORT}"
log "Checking HTTP surface"
for path in "/" "/health" "/api/mcp/sse"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}${path}")
  [ "$code" = "200" ] && green "  ${path} -> ${code}" || { red "  ${path} -> ${code} (expected 200)"; exit 1; }
done

log "Driving an ingestion end to end"
resp=$(curl -s --max-time 60 -X POST "${base}/api/ingest" \
  -H 'Content-Type: application/json' \
  -d '{"content":"# Smoke Test\nAcme Corp engaged us. Dana Wu leads the project.","title":"Smoke Test","content_type":"document"}')
job=$(echo "$resp" | grep -oE 'ingest-[a-f0-9]+' | head -1)
[ -n "$job" ] || { red "  ingestion not accepted: $resp"; exit 1; }
green "  job accepted: $job"

for i in $(seq 1 18); do
  sleep 5
  status=$(curl -s --max-time 10 "${base}/api/ingest/${job}/status")
  echo "$status" | grep -qE '"status":"completed"' && { green "  ingestion completed (~$((i*5))s)"; break; }
  if echo "$status" | grep -qE '"status":"failed"'; then
    red "  ingestion FAILED: $(echo "$status" | grep -oE '"error":"[^"]*"')"; exit 1
  fi
  [ "$i" = "18" ] && { red "  ingestion did not complete in time"; exit 1; }
done

log "Asserting graph populated"
nodes=$(docker exec imi-neo4j cypher-shell -u neo4j -p "$NEO4J_PW" --format plain \
  "MATCH (n) RETURN count(n);" 2>/dev/null | tail -1 | tr -d ' ')
if [ "${nodes:-0}" -gt 0 ]; then
  green "  graph has ${nodes} nodes"
else
  red "  graph is empty after ingestion (expected > 0 nodes)"; exit 1
fi

green "PASS (build + boot + serve + ingest + graph)"
