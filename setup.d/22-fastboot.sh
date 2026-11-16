#!/usr/bin/env bash
# Stage 22: a quick, seamless start from key-on to the map.
#   - firmware: no 1 s pause before Linux, no rainbow screen, full CPU speed while starting, no probing
#     for a ribbon-cable camera or display that isn't there (the backup camera is a USB stick; the
#     official touch display has its own overlay from stage 20)
#   - Linux: no boot messages or blinking cursor on the screen (they slow the start and flash between
#     the boot image and the map)
#   - the head-unit bridge, voices and audio start with the system instead of after the desktop logs in
#     ("linger"), so they are ready by the time Hudiy and the map open
#   - read-ahead: what the last start needed from the SD card is read in one pass while the system starts
#     (bin/hu-preload.py), Python programs are compiled once instead of at every start, and services
#     that only slow a car head unit's start are off
#   - the desktop behind Hudiy: the boot image (or black) as its background and the taskbar hidden, so
#     boot image -> map is one picture to the next, with no Raspberry Pi desktop in between
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
U=$(hu_user); H=$(hu_home)
R=${HU_SYSROOT:-}                       # (tests point this at a scratch folder)

put_block "$BOOT_CFG" fastboot "boot_delay=0
disable_splash=1
initial_turbo=60
camera_auto_detect=0
display_auto_detect=0"

# Kernel command line (one line): quiet console, no logo or cursor. Only adds what's missing.
for opt in quiet loglevel=3 logo.nologo vt.global_cursor_default=0; do
  grep -qE "(^| )${opt%%=*}(=| |$)" "$BOOT_CMDLINE" || sed -i "1s/\$/ $opt/" "$BOOT_CMDLINE"
done

# The user's services (bridge, audio) start at boot, before the desktop.
# (the file is what "linger" is: written directly, it also works while an update installs early in a start,
# before the login service is up)
install -d "$R/var/lib/systemd/linger" && touch "$R/var/lib/systemd/linger/$U"
loginctl enable-linger "$U" 2>/dev/null || true

# Read-ahead service (root, as early as the system allows)
install -d "$R/etc/systemd/system" && install -m 644 "$HU_ROOT/systemd/headunit-preload.service" "$R/etc/systemd/system/"
systemctl daemon-reload 2>/dev/null || true
systemctl enable headunit-preload.service >/dev/null 2>&1 || warn "couldn't enable the read-ahead service"

# Python programs compiled once, here, instead of every time they start (the head-unit user can't write
# next to them, so without this every start compiled them again)
python3 -m compileall -q "$(readlink -f "$HU_ROOT")/bin" >/dev/null 2>&1 || true

# Services that only add to the start time on a head unit:
#   NetworkManager-wait-online  holds "network is up" until Wi-Fi connects; in a car there's often none,
#                               and everything here that needs the internet retries by itself
#   rpi-eeprom-update           checks for Raspberry Pi firmware updates at every start
#   e2scrub_reap                clean-up for a disk check that isn't used on SD cards
for u in NetworkManager-wait-online.service rpi-eeprom-update.service e2scrub_reap.service; do
  systemctl list-unit-files "$u" >/dev/null 2>&1 && systemctl mask "$u" >/dev/null 2>&1 || true
done
# cloud-init did its work on the first start (Raspberry Pi Imager's settings); after that its four steps
# only cost time at every start. This is its own documented off switch; delete the file to turn it back on.
if [ -d "$R/etc/cloud" ] && [ -d "$H/.hudiy" -o -f /var/lib/headunit/maps/manifest.json ]; then
  touch "$R/etc/cloud/cloud-init.disabled"
fi

# Desktop background = the boot image on black; taskbar hidden (Hudiy covers the screen anyway).
SPLASH=/etc/headunit/boot-splash.png
udir "$H/.config/pcmanfm/default"
for f in desktop-items-0.conf desktop-items-HDMI-A-1.conf desktop-items-HDMI-A-2.conf desktop-items-DSI-1.conf; do
  cat > "$H/.config/pcmanfm/default/$f" <<P
[*]
desktop_bg=#000000
desktop_shadow=#000000
desktop_fg=#000000
wallpaper_mode=$( [ -f "$SPLASH" ] && echo center || echo color )
wallpaper_common=1
wallpaper=$SPLASH
show_documents=0
show_trash=0
show_mounts=0
P
done
WF="$H/.config/wf-panel-pi.ini"
touch "$WF"
python3 - "$WF" <<'PY'
import configparser, sys
p = sys.argv[1]; c = configparser.ConfigParser(); c.optionxform = str; c.read(p)
if not c.has_section("panel"): c.add_section("panel")
c.set("panel", "autohide", "true"); c.set("panel", "autohide_duration", "0")
with open(p, "w") as f: c.write(f)
PY
chown -R "$U:$U" "$H/.config/pcmanfm" "$WF"
say "fast start: no boot pause or messages, services start with the system, read-ahead on, boot image stays up until the map"
