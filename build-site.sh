#!/usr/bin/env bash
# Cloudflare Pages build. Publishes ONLY what the app fetches.
#
# Without this, Pages would upload the entire raw archive on every deploy --
# thousands of HTML files that no client ever requests. The archive belongs in
# git (versioned, permanent); the site is just the app's data endpoint.
set -euo pipefail

mkdir -p dist
for f in waters.json stockings.json quarantine.json unmatched.json; do
  [ -f "$f" ] && cp "$f" dist/ && echo "  published $f" || echo "  (skipped $f -- not present)"
done
cp index.html dist/ 2>/dev/null || true
cp _headers   dist/ 2>/dev/null || true

# A tiny machine-readable heartbeat so the app (and you) can see freshness
# without parsing the big files.
NEWEST=$(ls -1 archive/raw 2>/dev/null | sort | tail -1 || echo "none")
python3 - "$NEWEST" > dist/status.json <<'PY'
import json,sys,os,datetime
newest=sys.argv[1]
def count(p,key):
    try:
        d=json.load(open(p)); return d.get(key)
    except Exception: return None
json.dump({
 "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
 "newest_zone_capture": newest,
 "states": count("waters.json","states"),
 "water_count": count("waters.json","water_count"),
 "event_count": count("stockings.json","event_count"),
 "stockings_generated": count("stockings.json","generated"),
 "kind": "planned",
}, sys.stdout, indent=1)
PY
echo "  published status.json"
echo "dist/ contains: $(ls dist | tr '\n' ' ')"
