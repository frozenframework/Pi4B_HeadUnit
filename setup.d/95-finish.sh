#!/usr/bin/env bash
# Stage 95: summary.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
D=/var/lib/headunit
say "maps: display $( [ -f $D/maps/basemap.pmtiles ] && echo installed || echo 'not yet (Settings > Maps > Update, or sudo systemctl start headunit-update-maps)')"
say "      routing $( [ -f $D/routing/valhalla_tiles.tar ] && echo installed || echo 'not yet (USB stick with HEADUNIT_MAPS from maps/build-routing.py)')"
say "logs: test-drive usage logs in $(hu_home)/headunit-data/usage (USB stick named HUEXPORT copies them out)"

# Diagnostics report on the SD card (bootfs/headunit/diag.txt) at boot, when asked for with an empty
# file named collect-diagnostics in that folder (first boot asks for one automatically).
install -m 644 /opt/headunit/systemd/headunit-diag.service /etc/systemd/system/
systemctl daemon-reload 2>/dev/null || true
systemctl enable headunit-diag.service >/dev/null 2>&1 || true
