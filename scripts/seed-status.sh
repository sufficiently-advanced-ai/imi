#!/usr/bin/env bash
# Progress of the drip ingestion (reads the seed manifest).
#
# SEED_MANIFEST — path to the manifest. Applies to THIS script only; the seeder
#   (scripts/rebuild_kb.py) derives its manifest path from --folder / SEED_DIR,
#   so point this at the manifest inside the folder you are seeding.
# SEED_TOTAL — corpus size for the percentage. Without it no percentage is
#   shown, because the manifest only contains sources that have been LAUNCHED:
#   a pass that stopped early omits the rest, and dividing by len(manifest)
#   would read 100% on an incomplete corpus.
python3 - <<'EOF'
import collections
import json
import os
import sys

path = os.environ.get(
    "SEED_MANIFEST",
    os.path.expanduser("~/Developer/imi/data/imi-seed/.kb_seed_manifest.json"),
)

# The manifest is written by the seeder as it goes, so a status check can
# legitimately land before the first pass creates it, or midway through a
# write. Neither is an error worth a traceback.
try:
    with open(path) as fh:
        manifest = json.load(fh)
except FileNotFoundError:
    raise SystemExit(f"no seed manifest yet at {path} — has a pass run?")
except (OSError, json.JSONDecodeError) as e:
    raise SystemExit(
        f"seed manifest not readable right now ({type(e).__name__}); "
        "it is probably mid-write — try again in a moment"
    )

# json.load only guarantees syntax. A valid `null`, a list, or an entry whose
# value is not an object all parse fine and then blow up on .values()/.get().
if not isinstance(manifest, dict):
    raise SystemExit(f"unexpected manifest shape in {path}: expected an object, "
                     f"got {type(manifest).__name__}")
bad = next((k for k, v in manifest.items() if not isinstance(v, dict)), None)
if bad is not None:
    raise SystemExit(f"malformed manifest entry {bad!r} in {path}: expected an object")

counts = collections.Counter(v.get("status") for v in manifest.values())
done = counts.get("completed", 0) + counts.get("duplicate", 0)

raw_total = os.environ.get("SEED_TOTAL")
if raw_total:
    try:
        total = int(raw_total)
    except ValueError:
        raise SystemExit(f"SEED_TOTAL must be an integer, got {raw_total!r}")
    if total < 0:
        raise SystemExit(f"SEED_TOTAL must be non-negative, got {total}")
    # The corpus cannot be smaller than what a pass has already launched;
    # otherwise the denominator produces nonsense like "2/1 ingested (200.0%)".
    if total < len(manifest):
        raise SystemExit(
            f"SEED_TOTAL={total} is below the {len(manifest)} sources already in the "
            f"manifest — the corpus cannot be smaller than what has been launched"
        )
    pct = f" ({done / total:.1%})" if total else ""
    print(f"{done}/{total} ingested{pct}  {dict(counts)}")
else:
    # Deliberately no percentage: launched != discovered.
    print(f"{done}/{len(manifest)} launched sources ingested  {dict(counts)}")
    print("  (set SEED_TOTAL=<corpus size> for a true percentage — the manifest "
          "only lists sources a pass has reached)", file=sys.stderr)
EOF
status=$?

# Optional tail, but it must not decide the script's exit status.
LOG="$HOME/Developer/imi/logs/drip-seed.log"
[ -f "$LOG" ] && tail -1 "$LOG" | cut -c1-120

exit "$status"
