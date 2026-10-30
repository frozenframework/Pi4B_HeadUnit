#!/usr/bin/env bash
# Imports a HEADUNIT_MAPS folder (from maps/build-routing.py) from a USB stick.
set -euo pipefail
PACK=${1:?usage: hu-import-maps.sh /media/hu/STICK/HEADUNIT_MAPS}
case "$PACK" in /media/hu/*/HEADUNIT_MAPS) ;; *) echo "refusing: not a HEADUNIT_MAPS folder under /media/hu" >&2; exit 2;; esac
D=/var/lib/headunit
python3 -c "import json,sys; m=json.load(open(sys.argv[1])); assert m.get('format')==1" "$PACK/manifest.json"
need=$(( $(du -sm "$PACK" | cut -f1) / 1024 + 1 ))
free=$(df -BG --output=avail "$D" | tail -1 | tr -dc 0-9)
[ "$free" -gt "$need" ] || { echo "not enough space (${free} GB free, ${need} GB needed)" >&2; exit 1; }
copy() { [ -f "$PACK/$1" ] || return 0; install -d "$D/$2"; cp "$PACK/$1" "$D/$2/$1.part"; mv -f "$D/$2/$1.part" "$D/$2/$1"; }
copy valhalla_tiles.tar routing
copy search.sqlite search
copy basemap.pmtiles maps
python3 - "$PACK/manifest.json" "$D/maps/manifest.json" "$D/maps/imported.json" <<'PY'
import json, sys, os
src, dst, stamp = sys.argv[1:]
m = json.load(open(src)); cur = json.load(open(dst)) if os.path.exists(dst) else {}
cur.update({k: m[k] for k in ("region", "name", "built", "bbox") if k in m})
json.dump(cur, open(dst, "w"), indent=1); json.dump(m, open(stamp, "w"), indent=1)
PY
chown -R "$(stat -c %U $D)": "$D"
echo "imported $(python3 -c "import json;print(json.load(open('$PACK/manifest.json'))['name'])")"
