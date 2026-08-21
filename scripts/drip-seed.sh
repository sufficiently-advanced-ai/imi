#!/usr/bin/env bash
# Drip the OpenBrain seed corpus through imi's ingestion pipeline on the
# subscription-authenticated agent_sdk endpoint (LCARS local instance).
#
# The seeder is resumable via <folder>/.kb_seed_manifest.json: completed files
# skip on --resume, failed ones retry. Without --continue-on-error the seeder
# stops on the first failure — which is exactly what a usage-window exhaustion
# looks like — so we sleep and try again next window.
#
#   nohup ~/Developer/imi/scripts/drip-seed.sh >> ~/Developer/imi/logs/drip-seed.log 2>&1 &
set -uo pipefail

SEED_DIR=${SEED_DIR:-/app/data/imi-seed}
SLEEP_BETWEEN=${SLEEP_BETWEEN:-1800}
CONCURRENCY=${CONCURRENCY:-3}

mkdir -p "$(dirname "$0")/../logs"

while true; do
  # Sync subscription creds into the container's auth dir (see compose
  # override: single-file mounts go stale when Claude Code rotates the token).
  #
  # NOT best-effort. A swallowed failure here is the exact bug this sync
  # exists to fix: the container keeps stale credentials, every pass dies on
  # auth, and the retry loop hides the cause behind hours of failed passes.
  if ! cp -f "$HOME/.claude/.credentials.json" \
              "$HOME/Developer/imi/.claude-auth/.credentials.json"; then
    echo "FATAL: could not sync credentials from $HOME/.claude/.credentials.json" >&2
    echo "       (is Claude Code logged in? is .claude-auth/ writable?)" >&2
    exit 1
  fi
  echo "=== $(date '+%F %T') seed pass starting ==="
  docker exec imi-app python scripts/rebuild_kb.py seed \
    --folder "$SEED_DIR" --resume --yes --concurrency "$CONCURRENCY"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "=== $(date '+%F %T') seed COMPLETE ==="
    break
  fi
  done_count=$(python3 -c "import json;m=json.load(open('$HOME/Developer/imi/data/imi-seed/.kb_seed_manifest.json'));print(sum(1 for v in m.values() if v.get('status')=='completed'))" 2>/dev/null || echo '?')
  echo "=== $(date '+%F %T') seed stopped (rc=$rc, completed=$done_count) — sleeping ${SLEEP_BETWEEN}s ==="
  sleep "$SLEEP_BETWEEN"
done
