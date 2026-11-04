#!/usr/bin/env bash
# Downloads the latest offline map DISPLAY data (Protomaps daily OpenStreetMap build) for the
# configured region, cut to the region's bounding box. Safe to run any time; the old map keeps
# working until the new file is complete. Progress: /var/lib/headunit/maps/update-status.json
set -euo pipefail
CONF=/etc/headunit/headunit.conf
D=/var/lib/headunit/maps; mkdir -p "$D"
REGION=$(grep -E '^MAP_REGION=' "$CONF" | tail -1 | cut -d= -f2 | tr -d '"'); REGION=${REGION:-texas-plus}
status() { printf '{"message": "%s", "t": %s}\n' "$1" "$(date +%s)" > "$D/update-status.json"; echo "$1"; }
STEP="starting"
trap 'status "Map update failed while $STEP (log: journalctl -u headunit-update-maps)"' ERR

read -r BBOX MAXZ NAME < <(python3 - "$REGION" <<'PY'
import json, sys
r = json.load(open("/opt/headunit/maps/regions.json"))[sys.argv[1]]
print(",".join(map(str, r["bbox"])), r.get("maxzoom", 15), r["name"].replace(" ", "_"))
PY
)

# 1) pmtiles command-line tool (single static binary from the official go-pmtiles releases)
PMT=/opt/headunit/tools/pmtiles
if [ ! -x "$PMT" ]; then
  status "Installing the pmtiles tool"; STEP="installing the pmtiles tool (needs internet)"
  url=$(curl -fsSL https://api.github.com/repos/protomaps/go-pmtiles/releases/latest | python3 -c "import json,sys; print(next(a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if a['name'].endswith('Linux_arm64.tar.gz')))")
  tmp=$(mktemp -d); curl -fsSL "$url" -o "$tmp/p.tgz"; tar -xzf "$tmp/p.tgz" -C "$tmp"
  install -D -m 755 "$tmp/pmtiles" "$PMT"; rm -rf "$tmp"
fi

# 2) newest daily planet build (checks back up to 10 days)
STEP="looking for the newest map build"
BUILD=""
for i in $(seq 0 10); do
  day=$(date -u -d "-$i day" +%Y%m%d)
  if curl -fsI "https://build.protomaps.com/$day.pmtiles" >/dev/null 2>&1; then BUILD=$day; break; fi
done
[ -n "$BUILD" ] || { status "Couldn't reach build.protomaps.com (check Wi-Fi)"; exit 1; }

# 3) space check (rough: texas-plus ~5 GB, us at z14 ~20 GB) and extract
need=$([ "$REGION" = us ] && echo 30 || echo 8)
free=$(df -BG --output=avail "$D" | tail -1 | tr -dc 0-9)
[ "$free" -ge "$need" ] || { status "Not enough space: ${free} GB free, about ${need} GB needed"; exit 1; }
status "Downloading ${NAME//_/ } map (build $BUILD). This can take a while"
# A Wi-Fi drop ends a download (it can't resume), so try up to 3 times.
for try in 1 2 3; do
  STEP="downloading (try $try of 3)"
  if "$PMT" extract "https://build.protomaps.com/$BUILD.pmtiles" "$D/basemap.pmtiles.part" --bbox="$BBOX" --maxzoom="$MAXZ" --download-threads=4; then break; fi
  [ "$try" = 3 ] && { status "Map download kept failing (Wi-Fi dropped?). Try again later"; exit 1; }
  status "Download interrupted; retrying (try $((try+1)) of 3)"; sleep 30
done
STEP="checking the downloaded file"
"$PMT" verify "$D/basemap.pmtiles.part" >/dev/null
mv -f "$D/basemap.pmtiles.part" "$D/basemap.pmtiles"
python3 - "$D/manifest.json" "$REGION" "$BUILD" "${NAME//_/ }" <<'PY'
import json, sys, os
p, region, build, name = sys.argv[1:]
m = json.load(open(p)) if os.path.exists(p) else {}
m.update({"region": region, "name": name, "basemap_date": f"{build[:4]}-{build[4:6]}-{build[6:]}"})
json.dump(m, open(p, "w"), indent=1)
PY
chown -R "$(stat -c %U /var/lib/headunit)": "$D"
status "Map display data updated ($(du -h "$D/basemap.pmtiles" | cut -f1), OSM build $BUILD)"
