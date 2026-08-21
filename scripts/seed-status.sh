#!/usr/bin/env bash
# Progress of the drip ingestion (reads the seed manifest).
#
# SEED_MANIFEST overrides the manifest path; SEED_TOTAL overrides the
# denominator when the corpus size is known ahead of the manifest.
python3 - <<'EOF'
import collections
import json
import os

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
    raise SystemExit(f"seed manifest not readable right now ({type(e).__name__}); "
                     "it is probably mid-write — try again in a moment")

counts = collections.Counter(v.get("status") for v in manifest.values())
done = counts.get("completed", 0) + counts.get("duplicate", 0)

# Default to the manifest's own size rather than a hardcoded corpus count:
# the manifest has an entry per discovered document.
total = int(os.environ.get("SEED_TOTAL") or len(manifest))
pct = f" ({done / total:.1%})" if total else ""
print(f"{done}/{total} ingested{pct}  {dict(counts)}")
EOF

LOG="$HOME/Developer/imi/logs/drip-seed.log"
[ -f "$LOG" ] && tail -1 "$LOG" | cut -c1-120
