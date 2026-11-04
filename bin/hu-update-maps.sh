#!/usr/bin/env bash
# Downloads the latest offline map DISPLAY data (Protomaps daily OpenStreetMap build) for the
# configured region, cut to the region's bounding box. Safe to run any time; the old map keeps
# working until the new file is complete. Progress: /var/lib/headunit/maps/update-status.json
# (phase preparing/downloading/retrying/checking/done/failed; while downloading, bin/hu-maps-progress.py
# adds percent, size, speed and time left for the progress bar in Settings > Maps).
set -euo pipefail
CONF=/etc/headunit/headunit.conf
D=/var/lib/headunit/maps; mkdir -p "$D"
REGION=$(grep -E '^MAP_REGION=' "$CONF" | tail -1 | cut -d= -f2 | tr -d '"'); REGION=${REGION:-texas-plus}
status() {   # status "message" phase
  printf '{"message": "%s", "phase": "%s", "t": %s}\n' "$1" "${2:-preparing}" "$(date +%s)" > "$D/update-status.json.tmp"
  mv -f "$D/update-status.json.tmp" "$D/update-status.json"; echo "$1"
}
STEP="starting"
trap 'status "Map update failed while $STEP (log: journalctl -u headunit-update-maps)" failed' ERR

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

# 2) which planet build: one we already half-downloaded (if the server still has it), else the newest
STEP="looking for the newest map build"
CACHE="$D/download-cache"; mkdir -p "$CACHE"
BUILD=""
for c in $(ls -1 "$CACHE" 2>/dev/null | sort -r); do
  if [ -n "$(ls -A "$CACHE/$c" 2>/dev/null)" ] && curl -fsI "https://build.protomaps.com/$c.pmtiles" >/dev/null 2>&1; then
    BUILD=$c; echo "Resuming the download of build $BUILD"; break
  fi
done
if [ -z "$BUILD" ]; then
  for i in $(seq 0 10); do
    day=$(date -u -d "-$i day" +%Y%m%d)
    if curl -fsI "https://build.protomaps.com/$day.pmtiles" >/dev/null 2>&1; then BUILD=$day; break; fi
  done
fi
[ -n "$BUILD" ] || { status "Couldn't reach build.protomaps.com (check Wi-Fi)" failed; exit 1; }
for c in "$CACHE"/*; do [ "$c" = "$CACHE/$BUILD" ] || rm -rf "$c"; done   # parts of older builds are useless now

# 3) space check (rough: texas-plus ~5 GB, whole US ~15 GB; the download cache holds a second copy
#    until the map is finished) and extract
need=$([ "$REGION" = us ] && echo 40 || echo 12)
have_gb=$(( $(du -s --block-size=1G "$CACHE/$BUILD" 2>/dev/null | cut -f1 || echo 0) ))
free=$(df -BG --output=avail "$D" | tail -1 | tr -dc 0-9)
[ $((free + have_gb)) -ge "$need" ] || { status "Not enough space: ${free} GB free, about ${need} GB needed" failed; exit 1; }

# The extractor can't resume and gives up on the first dropped connection, so it reads through a local
# proxy (hu-range-proxy.py) that saves every piece to $CACHE/$BUILD, retries and continues dropped
# pieces from the last byte, and serves pieces it already has from disk. A failed or interrupted run
# (Wi-Fi drop, server reset, power off) picks up where it stopped the next time this runs.
PROXY_PORT=8766
python3 /opt/headunit/bin/hu-range-proxy.py --upstream https://build.protomaps.com --cache "$CACHE/$BUILD" \
  --port "$PROXY_PORT" --stats "$D/proxy-stats.json" 2> >(sed -u 's/^/proxy: /' >&2) &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -fsI "http://127.0.0.1:$PROXY_PORT/$BUILD.pmtiles" >/dev/null 2>&1 && break; sleep 0.5
done
status "Downloading ${NAME//_/ } map (build $BUILD). This can take a while; if it stops, it continues where it left off"
for try in 1 2 3 4 5; do
  STEP="downloading (try $try of 5)"
  rm -f "$D/basemap.pmtiles.part"                           # rebuilt from the cache in a minute or two
  if "$PMT" extract "http://127.0.0.1:$PROXY_PORT/$BUILD.pmtiles" "$D/basemap.pmtiles.part" --bbox="$BBOX" --maxzoom="$MAXZ" --download-threads=4 2>&1 \
     | python3 /opt/headunit/bin/hu-maps-progress.py --status "$D/update-status.json" --stats "$D/proxy-stats.json" \
         --name "$NAME" --try "$try" --tries 5; then break; fi
  [ "$try" = 5 ] && { status "Map download stopped (Wi-Fi dropped?). Run it again: it continues where it left off" failed; exit 1; }
  status "Download interrupted; continuing (try $((try+1)) of 5)" retrying; sleep 30
done
kill $PROXY_PID 2>/dev/null || true
STEP="checking the downloaded file"
status "Checking the map file" checking
"$PMT" verify "$D/basemap.pmtiles.part" >/dev/null
mv -f "$D/basemap.pmtiles.part" "$D/basemap.pmtiles"
rm -rf "$CACHE" "$D/proxy-stats.json"                       # finished: the saved pieces aren't needed any more
python3 - "$D/manifest.json" "$REGION" "$BUILD" "${NAME//_/ }" <<'PY'
import json, sys, os
p, region, build, name = sys.argv[1:]
m = json.load(open(p)) if os.path.exists(p) else {}
m.update({"region": region, "name": name, "basemap_date": f"{build[:4]}-{build[4:6]}-{build[6:]}"})
json.dump(m, open(p, "w"), indent=1)
PY
chown -R "$(stat -c %U /var/lib/headunit)": "$D"
status "Map display data updated ($(du -h "$D/basemap.pmtiles" | cut -f1), OSM build $BUILD)" done
