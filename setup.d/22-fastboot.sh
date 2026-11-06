#!/usr/bin/env bash
# Stage 22: a quick, seamless start from key-on to the map.
#   - firmware: no 1 s pause before Linux, no rainbow screen, full CPU speed while starting, no probing
#     for a ribbon-cable camera or display that isn't there (the backup camera is a USB stick; the
#     official touch display has its own overlay from stage 20)
#   - Linux: no boot messages or blinking cursor on the screen (they slow the start and flash between
#     the boot image and the map)
#   - the head-unit bridge, voices and audio start with the system instead of after the desktop logs in
#     ("linger"), so they are ready by the time Hudiy and the map open
#   - the desktop behind Hudiy: the boot image (or black) as its background and the taskbar hidden, so
#     boot image -> map is one picture to the next, with no Raspberry Pi desktop in between
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
U=$(hu_user); H=$(hu_home)

put_block "$BOOT_CFG" fastboot "boot_delay=0
disable_splash=1
initial_turbo=30
camera_auto_detect=0
display_auto_detect=0"

# Kernel command line (one line): quiet console, no logo or cursor. Only adds what's missing.
for opt in quiet loglevel=3 logo.nologo vt.global_cursor_default=0; do
  grep -qE "(^| )${opt%%=*}(=| |$)" "$BOOT_CMDLINE" || sed -i "1s/\$/ $opt/" "$BOOT_CMDLINE"
done

# The user's services (bridge, audio) start at boot, before the desktop.
loginctl enable-linger "$U" 2>/dev/null || warn "couldn't turn on linger for $U (services start after login instead)"

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
say "fast start: no boot pause or messages, services start with the system, boot image stays up until the map"
