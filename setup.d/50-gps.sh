#!/usr/bin/env bash
# Stage 50: gpsd picks up any USB GPS automatically.
source /opt/headunit/lib/common.sh
cat > /etc/default/gpsd <<'G'
# Managed by headunit setup
START_DAEMON="true"
USBAUTO="true"
DEVICES=""
GPSD_OPTIONS="-n"
G
systemctl enable gpsd.socket gpsd.service >/dev/null 2>&1 || true
say "gps: gpsd with USB auto-detect"
