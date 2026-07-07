#!/usr/bin/env bash
# Progress of the OpenBrain drip ingestion (reads the seed manifest).
python3 - <<'EOF'
import json, collections, os
path = os.path.expanduser("~/Developer/imi/data/imi-seed/.kb_seed_manifest.json")
m = json.load(open(path))
c = collections.Counter(v.get("status") for v in m.values())
total, done = 3392, c.get("completed", 0) + c.get("duplicate", 0)
print(f"{done}/{total} ingested ({done/total:.1%})  {dict(c)}")
EOF
tail -1 "$HOME/Developer/imi/logs/drip-seed.log" | cut -c1-120
