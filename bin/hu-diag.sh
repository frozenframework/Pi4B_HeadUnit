#!/bin/bash
# Head-unit diagnostics.
#
#   hu-diag.sh [delay]          at boot (headunit-diag.service, while bootfs/headunit/collect-diagnostics
#                               exists): a report on the SD card's boot partition, bootfs/headunit/diag.txt,
#                               readable on a computer when the screen shows nothing
#   hu-diag.sh --bundle [why]   a diagnostics bundle for debugging (Settings > Developer > Collect
#                               diagnostics, tools/get-diag.sh from the Mac over Wi-Fi, or by itself after
#                               a slow / failed start): /var/lib/headunit/diag/headunit-diag-<car>-<version>-<time>.tar.gz
#                               with the report, this and the previous start's logs, the start-up timeline,
#                               routing / contacts / Bluetooth / audio / GPS state and the usage logs.
#                               The newest three are also copied to the SD card (bootfs/headunit/diag/) and go
#                               to a HUEXPORT USB stick with the usage logs.
# Kept out: passwords, Wi-Fi keys, the Hudiy order number, contacts, saved places and positions (rounded
# away), and most of each Bluetooth address.
set -u
B=${HU_BOOTFS:-/boot/firmware/headunit}; DIAG=${HU_DIAG_DIR:-/var/lib/headunit/diag}
U=$(grep -E '^HU_USER=' /etc/headunit/headunit.conf 2>/dev/null | tail -1 | cut -d= -f2); U=${U:-$(getent passwd 1000 | cut -d: -f1)}
UID_=$(id -u "$U" 2>/dev/null); H=$(getent passwd "$U" | cut -d: -f6)
WL=$(ls /run/user/"$UID_" 2>/dev/null | grep -m1 -E '^wayland-[0-9]+$')
asuser() { sudo -u "$U" XDG_RUNTIME_DIR="/run/user/$UID_" WAYLAND_DISPLAY="${WL:-wayland-0}" \
           DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" "$@"; }
sec() { printf '\n===== %s\n' "$1"; }
conf() { grep -E "^$1=" /etc/headunit/headunit.conf 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'; }

# Secrets and personal details out of everything that leaves the head unit
redact() {
  sed -E -e 's/((psk|password|passwd|order|token|secret|license|licence|api[_-]?key)[^=:"]*"?[[:space:]]*[=:][[:space:]]*).*/\1(hidden)/I' \
         -e 's/(([0-9A-Fa-f]{2}[:_]){4})([0-9A-Fa-f]{2}[:_][0-9A-Fa-f]{2})/XX:XX:XX:XX:\3/g' \
         -e 's/("?(lat|lon|latitude|longitude)"?[[:space:]]*[:=][[:space:]]*-?[0-9]+\.[0-9])[0-9]+/\1/g' \
         -e 's/(lastPos|lnglat|from|to|near)("?[[:space:]]*[:=][[:space:]]*\[)-?[0-9.]+,[[:space:]]*-?[0-9.]+/\1\2(hidden)/g'
}

report() {
  echo "head-unit diagnostics $(date)  version $(cat /opt/headunit/VERSION 2>/dev/null)  vehicle $(conf VEHICLE_ID)  user $U"
  sec "uptime / load / temperature / throttling / memory / disks"; uptime; vcgencmd measure_temp 2>&1; vcgencmd get_throttled 2>&1
  free -m; df -h / /boot/firmware /var/lib/headunit 2>&1
  sec "screens (kernel)"; for c in /sys/class/drm/card*-*; do [ -f "$c/status" ] && echo "$(basename "$c"): $(cat "$c/status") $(head -3 "$c/modes" | tr '\n' ' ')"; done
  sec "screens (desktop, wlr-randr) on $WL"; asuser wlr-randr 2>&1 | head -40
  sec "boot settings"; cat /boot/firmware/cmdline.txt; sed -n '/>>> headunit/,/<<< headunit/p' /boot/firmware/config.txt
  sec "head-unit settings"; grep -vE '^\s*#|^\s*$' /etc/headunit/headunit.conf
  sec "start-up timeline (seconds since Linux started)"; cat "/run/user/$UID_/headunit-boot.json" 2>&1; echo
  echo "read-ahead this start: $(cat /run/headunit-preload.json 2>&1)"
  python3 -c "import json; d=json.load(open('/var/lib/headunit/preload.json')); print('read-ahead list:', len(d['files']), 'files,', d['bytes']>>20, 'MB, starts counted', d.get('boots'), 'version', d.get('version')); [print('  ', f[1]>>20, 'MB file,', sum(r[1] for r in f[3])>>20, 'MB listed', f[0]) for f in sorted(d['files'], key=lambda f: -sum(r[1] for r in f[3]))[:25]]" 2>&1
  echo "last starts:"; python3 -c "import json; [print('  ', h) for h in json.load(open('$H/.config/headunit/boot-times.json'))[-10:]]" 2>&1
  systemd-analyze 2>&1; systemd-analyze blame 2>/dev/null | head -25
  systemd-analyze critical-chain graphical.target 2>/dev/null | head -30
  sec "desktop autostart"; echo "-- $H/.config/labwc/autostart"; cat "$H/.config/labwc/autostart" 2>&1; echo "-- /etc/xdg/labwc/autostart"; cat /etc/xdg/labwc/autostart 2>&1
  sec "programs running"; ps -eo pid,etime,pcpu,rss,user,args --sort=start_time | grep -iE 'hudiy|labwc|lightdm|kanshi|pcmanfm|wf-panel|hu-|squeek|splash|obexd|pipewire|gpsd|mpd' | grep -v grep
  sec "failed services"; systemctl --failed --no-pager; asuser systemctl --user --failed --no-pager 2>&1
  sec "head-unit services"; for s in headunit-power headunit-update-apply headunit-update-verify headunit-swc; do systemctl status "$s" --no-pager -n 0 2>&1 | head -3; done
  for s in headunit-bridge headunit-dimmer mpd; do asuser systemctl --user status "$s" --no-pager -n 15 2>&1 | head -25; done
  sec "maps / routing / search"; cat /var/lib/headunit/maps/update-status.json 2>&1; echo; cat /var/lib/headunit/maps/manifest.json 2>&1; echo
  ls -la /var/lib/headunit/maps /var/lib/headunit/routing /var/lib/headunit/search 2>&1
  cat /var/lib/headunit/routing/index-status.json 2>&1; echo
  python3 -c "import sys; sys.path.insert(0,'/opt/headunit/bin'); import hu_valhalla_tar as V; print('routing index.bin:', V.has_index('/var/lib/headunit/routing/valhalla_tiles.tar'))" 2>&1
  journalctl -u headunit-update-maps -u headunit-maps-firstrun -b --no-pager -n 40 2>&1 | grep -v 'fetching chunks' | tail -25
  sec "software updates"; cat /var/lib/headunit/update/status.json /var/lib/headunit/update/state.json 2>&1; echo; ls /opt/headunit-releases 2>&1; readlink -f /opt/headunit
  sec "network"; nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>&1; ip -br addr 2>&1
  curl -s -o /dev/null -w "github.com: %{http_code}\n" -m 8 -I https://github.com/ 2>&1
  sec "Bluetooth"; bluetoothctl show 2>&1 | grep -E 'Name|Powered|Discoverable|Pairable'; bluetoothctl devices Paired 2>&1 || bluetoothctl paired-devices 2>&1
  for d in $(bluetoothctl devices Connected 2>/dev/null | awk '{print $2}'); do bluetoothctl info "$d" 2>&1 | grep -E 'Name|Connected|Trusted|UUID: (Phonebook|Handsfree|Audio Sink|A/V)'; done
  sec "phone contacts (counts only)"; asuser python3 /opt/headunit/bin/hu_contacts.py 2>&1 | tail -15
  python3 - "$H/.config/headunit/contacts" <<'PY' 2>&1
import json, sys, pathlib, time
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    try: d = json.loads(f.read_text())
    except Exception as e: print(f.name, e); continue
    adr = sum(len(c["addresses"]) for c in d.get("contacts", [])); onmap = sum(1 for c in d.get("contacts", []) for a in c["addresses"] if "lon" in a)
    print(f"{d.get('phone')}: {d.get('count')} contacts with an address, {adr} addresses, {onmap} on the map, updated {time.ctime(d.get('updated', 0))}")
PY
  sec "audio"; asuser wpctl status 2>&1 | head -60
  sec "GPS (receivers and satellites; no positions)"
  echo "services: gpsd.socket $(systemctl is-active gpsd.socket 2>&1), gpsd $(systemctl is-active gpsd 2>&1)"
  systemctl list-units --all --no-legend --no-pager 'gpsdctl@*' 2>&1 | head -5
  echo "USB devices:"; for d in /sys/bus/usb/devices/*; do [ -f "$d/idVendor" ] && [ "$(cat "$d/idVendor")" != "1d6b" ] &&
    echo "  $(cat "$d/idVendor"):$(cat "$d/idProduct") $(cat "$d/product" 2>/dev/null) [$(basename "$d"), $(cat "$d/bMaxPower" 2>/dev/null)]"; done
  ls -l /dev/gps-usb* /dev/ttyUSB* /dev/ttyACM* /dev/serial/by-id/ 2>&1 | grep -v "^total" | head -10
  ls -l /etc/udev/rules.d/61-headunit-gps.rules 2>&1; grep -E '^[A-Z_]+=' /etc/default/gpsd 2>&1
  dmesg 2>/dev/null | grep -iE 'cp210x|pl2303|cdc_acm|ttyUSB|ttyACM|u-blox|gps' | tail -8
  timeout 8 gpspipe -w -n 20 2>&1 | python3 -c '
import json, sys
mode = None
for line in sys.stdin:
    try: m = json.loads(line)
    except ValueError: print(line.strip()[:120]); continue
    c = m.get("class")
    if c == "DEVICES": print("gpsd has", len(m.get("devices", [])), "receiver(s):", [(d.get("path"), d.get("driver", "?"), d.get("bps")) for d in m.get("devices", [])])
    elif c == "DEVICE": print("receiver:", m.get("path"), m.get("driver", "?"), "activated" if m.get("activated") else "removed")
    elif c == "SKY" and ("nSat" in m or m.get("satellites")): print("satellites: seen", m.get("nSat", len(m.get("satellites", []))), "used", m.get("uSat", sum(1 for s in m.get("satellites", []) if s.get("used"))))
    elif c == "TPV": mode = m.get("mode")
print("fix:", {None: "no report", 0: "none", 1: "none yet", 2: "2D", 3: "3D"}.get(mode, mode))' 2>&1 | sort -u | head -12
  journalctl -b --no-pager -u gpsd -u 'gpsdctl@*' -n 25 2>&1 | tail -25
  sec "Hudiy"; ls -la "$H/.hudiy/share" 2>&1 | head -40
  (exec 3<>/dev/tcp/127.0.0.1/44405) 2>/dev/null && echo "Hudiy API (44405): answering" || echo "Hudiy API (44405): not answering"
  sec "accounts / desktop login"; getent passwd 1000 "$U"; grep -E '^(autologin-user|autologin-session|user-session)=' /etc/lightdm/lightdm.conf
  sec "lightdm (first problems, then the end)"; grep -m 15 -iE "fail|error|exit|crit" /var/log/lightdm/lightdm.log 2>&1; echo ...; tail -15 /var/log/lightdm/lightdm.log 2>&1
  sec "starts recorded by the journal"; journalctl --list-boots --no-pager 2>&1 | tail -6
  sec "user journal (this start, last 200)"; journalctl -b --no-pager _UID="$UID_" -n 200 2>&1
  sec "system warnings (this start, last 200)"; journalctl -b --no-pager -p warning -n 200 2>&1
}

[ "${1:-}" = "--redact-test" ] && { redact; exit 0; }      # (tools/tests/test_diag.py)
if [ "${1:-}" != "--bundle" ]; then                       # ---- the boot report on the SD card
  OUT=$B/diag.txt
  sleep "${1:-90}"     # let the desktop and Hudiy start (or fail) first
  renice -n 15 $$ >/dev/null 2>&1; ionice -c 3 -p $$ >/dev/null 2>&1   # and never slow the screens down
  report 2>&1 | redact > "$OUT.tmp"; mv "$OUT.tmp" "$OUT"; sync
  exit 0
fi

# ---- a bundle for debugging
WHY=$(printf '%s' "${2:-manual}" | tr -cd 'a-z0-9_-' | cut -c1-20)
STAMP=$(date +%Y%m%d-%H%M%S); NAME="headunit-diag-$(conf VEHICLE_ID)-$(cat /opt/headunit/VERSION 2>/dev/null)-$STAMP-$WHY"
T=$(mktemp -d); W=$T/$NAME; mkdir -p "$W/logs" "$W/data"
trap 'rm -rf "$T"' EXIT
renice -n 15 $$ >/dev/null 2>&1; ionice -c 3 -p $$ >/dev/null 2>&1   # never slows the screens down
{ echo "why: $WHY"; report; } 2>&1 | redact > "$W/report.txt"
journalctl -b --no-pager -o short-monotonic -n 4000 2>&1 | redact > "$W/logs/journal-this-start.txt"
journalctl -b --no-pager -o short-monotonic _UID="$UID_" -n 3000 2>&1 | redact > "$W/logs/journal-user-this-start.txt"
if journalctl -b -1 -n 1 --no-pager >/dev/null 2>&1; then          # the start before (a drive that went wrong)
  journalctl -b -1 --no-pager -o short-monotonic -n 4000 2>&1 | redact > "$W/logs/journal-previous-start.txt"
fi
journalctl -u 'headunit-*' --no-pager --since "-7 days" -n 1500 2>&1 | redact > "$W/logs/headunit-services-7-days.txt"
for f in /var/log/headunit-setup.log /var/log/lightdm/lightdm.log "$B/firstboot.log"; do
  [ -f "$f" ] && tail -n 1500 "$f" | redact > "$W/logs/$(basename "$f")"
done
find "$H/.hudiy" "$H/.cache" -maxdepth 5 -name '*.log' -mmin -1440 2>/dev/null | head -8 | while read -r f; do
  tail -n 800 "$f" | redact > "$W/logs/hudiy-$(basename "$(dirname "$f")")-$(basename "$f")"
done
for f in /var/lib/headunit/update/status.json /var/lib/headunit/maps/update-status.json /var/lib/headunit/maps/manifest.json \
         /var/lib/headunit/routing/index-status.json "$H/.config/headunit/boot-times.json" "$H/.config/headunit/display-status.json" \
         "/run/user/$UID_/headunit-boot.json"; do
  [ -f "$f" ] && redact < "$f" > "$W/data/$(basename "$f")"
done
# settings without the car's last position; usage logs (last 7 days) with positions rounded away
python3 - "$H/.config/headunit/settings.json" "$W/data/settings.json" <<'PY' 2>/dev/null
import json, sys
s = json.load(open(sys.argv[1])); s.pop("lastPos", None); json.dump(s, open(sys.argv[2], "w"), indent=1)
PY
USAGE=$(grep -E '^USAGE_LOG_DIR=' /etc/headunit/headunit.conf | cut -d= -f2); USAGE=${USAGE:-$H/headunit-data/usage}
find "$USAGE" -name '*.jsonl' -mtime -7 2>/dev/null | while read -r f; do redact < "$f" > "$W/data/usage-$(basename "$f")"; done
mkdir -p "$DIAG"
tar -C "$T" -czf "$DIAG/$NAME.tar.gz.part" "$NAME" && mv "$DIAG/$NAME.tar.gz.part" "$DIAG/$NAME.tar.gz"
chown -R "$U:$U" "$DIAG" 2>/dev/null
ls -1t "$DIAG"/headunit-diag-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f      # keep the newest 10
if [ -d "$B" ]; then                                                                 # and 3 on the SD card
  mkdir -p "$B/diag" && cp "$DIAG/$NAME.tar.gz" "$B/diag/" 2>/dev/null
  ls -1t "$B"/diag/headunit-diag-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f
fi
sync
echo "$DIAG/$NAME.tar.gz"
