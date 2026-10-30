#!/usr/bin/env bash
# Stage 95: summary.
source /opt/headunit/lib/common.sh
D=/var/lib/headunit
say "maps: display $( [ -f $D/maps/basemap.pmtiles ] && echo installed || echo 'not yet (Settings > Maps > Update, or sudo systemctl start headunit-update-maps)')"
say "      routing $( [ -f $D/routing/valhalla_tiles.tar ] && echo installed || echo 'not yet (USB stick with HEADUNIT_MAPS from maps/build-routing.py)')"
say "logs: test-drive usage logs in $(hu_home)/headunit-data/usage (USB stick named HUEXPORT copies them out)"
