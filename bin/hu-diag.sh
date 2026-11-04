#!/bin/bash
# Writes a diagnostics report to the SD card's boot partition (bootfs/headunit/diag.txt), so you can
# read it on your computer when the screen shows nothing. Runs once at boot when the file
# bootfs/headunit/collect-diagnostics exists. The image creates it, so every boot writes a fresh report
# until you delete that file (make an empty file with that name to turn reports back on).
B=/boot/firmware/headunit; OUT=$B/diag.txt
sleep "${1:-90}"     # let the desktop and Hudiy start (or fail) first
U=$(grep -E '^HU_USER=' /etc/headunit/headunit.conf 2>/dev/null | tail -1 | cut -d= -f2); U=${U:-$(getent passwd 1000 | cut -d: -f1)}
UID_=$(id -u "$U" 2>/dev/null); H=$(getent passwd "$U" | cut -d: -f6)
asuser() { sudo -u "$U" XDG_RUNTIME_DIR="/run/user/$UID_" WAYLAND_DISPLAY="${WL:-wayland-0}" "$@"; }
WL=$(ls /run/user/"$UID_" 2>/dev/null | grep -m1 -E '^wayland-[0-9]+$')
sec() { printf '\n===== %s\n' "$1"; }
{
  echo "head-unit diagnostics $(date)  version $(cat /opt/headunit/VERSION 2>/dev/null)  user $U"
  sec "uptime / load / temperature / throttling"; uptime; vcgencmd measure_temp 2>&1; vcgencmd get_throttled 2>&1
  sec "screens (kernel)"; for c in /sys/class/drm/card*-*; do [ -f "$c/status" ] && echo "$(basename "$c"): $(cat "$c/status") $(head -3 "$c/modes" | tr '\n' ' ')"; done
  sec "screens (desktop, wlr-randr) on $WL"; asuser wlr-randr 2>&1 | head -40
  sec "boot settings"; cat /boot/firmware/cmdline.txt; sed -n '/>>> headunit/,/<<< headunit/p' /boot/firmware/config.txt
  sec "head-unit settings"; grep -vE '^\s*#|^\s*$' /etc/headunit/headunit.conf
  sec "desktop autostart"; echo "-- $H/.config/labwc/autostart"; cat "$H/.config/labwc/autostart" 2>&1; echo "-- /etc/xdg/labwc/autostart"; cat /etc/xdg/labwc/autostart 2>&1
  sec "programs running"; ps -eo pid,etime,user,args --sort=start_time | grep -iE 'hudiy|labwc|lightdm|kanshi|pcmanfm|wf-panel|hu-|squeek|splash' | grep -v grep
  sec "failed services"; systemctl --failed --no-pager; asuser systemctl --user --failed --no-pager 2>&1
  sec "head-unit services"; for s in headunit-power headunit-update-apply headunit-update-verify; do systemctl status "$s" --no-pager -n 0 2>&1 | head -3; done
  for s in headunit-bridge headunit-dimmer mpd; do asuser systemctl --user status "$s" --no-pager -n 15 2>&1 | head -25; done
  sec "map updates"; cat /var/lib/headunit/maps/update-status.json 2>&1; echo; df -h /var/lib/headunit | tail -1
  journalctl -u headunit-update-maps -u headunit-maps-firstrun -b --no-pager -n 40 2>&1 | grep -v 'fetching chunks' | tail -25
  sec "network"; nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>&1; curl -s -o /dev/null -w "build.protomaps.com: %{http_code}\n" -m 10 -I https://build.protomaps.com/ 2>&1
  sec "dimmer status"; cat "$H/.config/headunit/display-status.json" 2>&1
  sec "accounts / desktop login"; getent passwd 1000 "$U"; grep -E '^(autologin-user|autologin-session|user-session)=' /etc/lightdm/lightdm.conf
  sec "lightdm (first problems, then the end)"; grep -m 15 -iE "fail|error|exit|crit" /var/log/lightdm/lightdm.log 2>&1; echo ...; tail -15 /var/log/lightdm/lightdm.log 2>&1
  sec "Hudiy files"; ls -la "$H/.hudiy/share" 2>&1 | head -40
  for f in $(find "$H/.hudiy" "$H/.cache" -maxdepth 4 -name '*.log' -mmin -30 2>/dev/null | head -5); do sec "log $f"; tail -60 "$f"; done
  sec "user journal (this boot)"; journalctl -b --no-pager _UID="$UID_" -n 200 2>&1
  sec "system warnings (this boot)"; journalctl -b --no-pager -p warning -n 200 2>&1
} 2>&1 | sed -E 's/(psk|password|passwd|order)([^=:]*)[=:].*/\1\2=(hidden)/I' > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"; sync
