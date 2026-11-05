#!/usr/bin/env bash
# Stage 95: summary.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
D=/var/lib/headunit
say "maps: display $( [ -f $D/maps/basemap.pmtiles ] && echo installed || echo 'not yet (Settings > Maps > Update, or sudo systemctl start headunit-update-maps)')"
say "      routing $( [ -f $D/routing/valhalla_tiles.tar ] && echo installed || echo 'not yet (USB stick with HEADUNIT_MAPS from maps/build-routing.py)')"
say "logs: test-drive usage logs in $(hu_home)/headunit-data/usage (USB stick named HUEXPORT copies them out)"

# Lighter system: things a car head unit never uses that run in the background on Raspberry Pi OS.
# (Software updates do their own apt work; printing, modem and the daily apt/man-db jobs aren't needed.)
for u in apt-daily.timer apt-daily-upgrade.timer man-db.timer e2scrub_all.timer \
         cups.service cups.socket cups.path cups-browsed.service ModemManager.service packagekit.service; do
  systemctl list-unit-files "$u" >/dev/null 2>&1 && systemctl disable --now "$u" >/dev/null 2>&1 || true
done
# Keep the system log small (fewer SD-card writes, faster boots)
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=64M\nRuntimeMaxUse=24M\n' > /etc/systemd/journald.conf.d/headunit.conf
say "lighter system: printing, modem and daily apt/man-db jobs off; system log capped at 64 MB"

# Diagnostics report on the SD card (bootfs/headunit/diag.txt) at boot, when asked for with an empty
# file named collect-diagnostics in that folder (first boot asks for one automatically).
install -m 644 /opt/headunit/systemd/headunit-diag.service /etc/systemd/system/
systemctl daemon-reload 2>/dev/null || true
systemctl enable headunit-diag.service >/dev/null 2>&1 || true
