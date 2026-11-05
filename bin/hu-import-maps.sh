#!/usr/bin/env bash
# Imports a HEADUNIT_MAPS folder (routing + search from maps/build-routing.py):
#   from a USB stick:  /media/hu/<stick>/HEADUNIT_MAPS   (copied; the stick is left as it was)
#   over Wi-Fi:        /var/lib/headunit/incoming/HEADUNIT_MAPS   (sent by tools/push-maps.py; moved into
#                      place, so it needs no extra space, and the inbox is emptied afterwards)
set -euo pipefail
PACK=${1:?usage: hu-import-maps.sh /media/hu/STICK/HEADUNIT_MAPS | /var/lib/headunit/incoming/HEADUNIT_MAPS}
D=/var/lib/headunit
INBOX=$D/incoming/HEADUNIT_MAPS
case "$PACK" in
  /media/hu/*/HEADUNIT_MAPS) MODE=copy;;
  "$INBOX"|"$INBOX/") PACK=$INBOX; MODE=move;;
  *) echo "refusing: not a HEADUNIT_MAPS folder on a USB stick or in $INBOX" >&2; exit 2;;
esac
[[ "$PACK" != *..* ]] || { echo "refusing: bad path" >&2; exit 2; }
python3 -c "import json,sys; m=json.load(open(sys.argv[1])); assert m.get('format')==1" "$PACK/manifest.json"
if [ "$MODE" = copy ]; then
  need=$(( $(du -sm "$PACK" | cut -f1) / 1024 + 1 ))
  free=$(df -BG --output=avail "$D" | tail -1 | tr -dc 0-9)
  [ "$free" -gt "$need" ] || { echo "not enough space (${free} GB free, ${need} GB needed)" >&2; exit 1; }
fi
copy() {
  [ -f "$PACK/$1" ] || return 0
  [ ! -L "$PACK/$1" ] || { echo "refusing: $1 is a link" >&2; exit 2; }
  install -d "$D/$2"
  if [ "$MODE" = move ]; then mv -f "$PACK/$1" "$D/$2/$1"
  else cp "$PACK/$1" "$D/$2/$1.part"; mv -f "$D/$2/$1.part" "$D/$2/$1"; fi
  touch "$D/$2/$1"                      # new date: the bridge reloads routing on its next request
  echo "installed $2/$1 ($(du -h "$D/$2/$1" | cut -f1))"
}
copy valhalla_tiles.tar routing
copy search.sqlite search
copy basemap.pmtiles maps
install -d "$D/maps"
python3 - "$PACK/manifest.json" "$D/maps/manifest.json" "$D/maps/imported.json" <<'PY'
import json, sys, os
src, dst, stamp = sys.argv[1:]
m = json.load(open(src)); cur = json.load(open(dst)) if os.path.exists(dst) else {}
cur.update({k: m[k] for k in ("region", "name", "built", "bbox") if k in m})
json.dump(cur, open(dst, "w"), indent=1); json.dump(m, open(stamp, "w"), indent=1)
PY
NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" "$PACK/manifest.json")
if [ "$MODE" = move ]; then rm -rf "$INBOX"; fi
chown -R "$(stat -c %U $D)": "$D"
echo "imported $NAME"
