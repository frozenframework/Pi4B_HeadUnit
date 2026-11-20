#!/usr/bin/env bash
# Stage 50: gpsd picks up any USB GPS automatically (udev/61-headunit-gps.rules tells it about the
# receivers Debian's list misses, like the Adafruit Ultimate GPS USB).
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
cat > /etc/default/gpsd <<'G'
# Managed by headunit setup
START_DAEMON="true"
USBAUTO="true"
DEVICES=""
GPSD_OPTIONS="-n"
OPTIONS=""
G

# receivers to hand to gpsd; GPS_USB=1234:abcd[,5678:ef01] in headunit.conf adds more
extra=""
for id in $(conf_get GPS_USB | tr ',' ' '); do
  if [[ $id =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$ ]]; then
    extra+="ATTRS{idVendor}==\"${id%%:*}\", ATTRS{idProduct}==\"${id##*:}\", GOTO=\"hu_gps\"\n"
  else
    warn "gps: GPS_USB=$id isn't a USB id like 1234:abcd (see lsusb); left out"
  fi
done
rules=/etc/udev/rules.d/61-headunit-gps.rules
new=$(awk -v extra="$extra" '{print} /^# GPS_USB from headunit.conf$/ && extra != "" {printf "%s", extra}' \
      "$HU_ROOT/udev/61-headunit-gps.rules")
if [ "$new" != "$(cat "$rules" 2>/dev/null)" ]; then
  printf '%s\n' "$new" > "$rules"; chmod 644 "$rules"
  udevadm control --reload 2>/dev/null || true
  # a receiver that's already plugged in is handed over now, not at the next start
  udevadm trigger --subsystem-match=tty --action=add 2>/dev/null || true
fi

systemctl enable gpsd.socket gpsd.service >/dev/null 2>&1 || true
say "gps: gpsd with USB auto-detect (u-blox, Adafruit / CP210x, PL2303 and any receiver named GPS)"
