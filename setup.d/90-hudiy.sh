#!/usr/bin/env bash
# Stage 90: register the head-unit apps in Hudiy's menu and point Hudiy at gpsd.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
H=$(hu_home)
if [ ! -d "$H/.hudiy/share/config" ]; then
  warn "Hudiy isn't installed yet. Install it (see README), then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  exit 0
fi
as_user python3 /opt/headunit/bin/hu-hudiy-config.py
# Skin Hudiy (and CarPlay / Android Auto's night look) with the map skin: now, and every time the desktop
# starts, before Hudiy does (first line of the desktop autostart).
as_user python3 /opt/headunit/bin/hu-hudiy-theme.py --startup || true
AS="$H/.config/labwc/autostart"; THEME_LINE="python3 /opt/headunit/bin/hu-hudiy-theme.py --startup"
touch "$AS"; sed -i -e "\|hu-hudiy-theme.py|d" -e "\|hu-wait-bridge.sh|d" -e "\|hu-hudiy-start.py|d" -e "\|hu-clear-webcache.sh|d" "$AS"
# Start screen: Hudiy opens the offline map first (Settings > Driving > Start on the map). --config sets
# Hudiy's defaultAction before it starts; --open waits in the background and opens the map once Hudiy is up.
as_user python3 /opt/headunit/bin/hu-hudiy-start.py --config || true
sed -i "\|hu-desktop-start.py|d" "$AS"
sed -i "1i python3 /opt/headunit/bin/hu-hudiy-start.py --open \&" "$AS"
# ...and Hudiy waits (up to 30 s) for the head-unit bridge, so the Drive menu apps load on the first try
# (the bridge starts with the system - setup.d/22 - so it normally answers at once).
sed -i "1i /opt/headunit/bin/hu-wait-bridge.sh" "$AS"
# Hudiy's skin colors and first screen, in one quick program (only written when they changed)
sed -i "1i python3 /opt/headunit/bin/hu-desktop-start.py" "$AS"
# After an update (or going back a version), Hudiy's browser forgets its cached copies of the apps first.
sed -i "1i /opt/headunit/bin/hu-clear-webcache.sh" "$AS"
chown "$(hu_user):$(hu_user)" "$AS"
# Hudiy reads the phone's contacts through lib/hu-vcf-filter.so (built here): contacts with neither a
# number nor an address left out, blank numbers taken off - a blank number made Hudiy's call screen
# show the same wrong name for every caller. Built again when the source changes.
SO="$HU_ROOT/lib/hu-vcf-filter.so"; SRC="$HU_ROOT/lib/hu-vcf-filter.c"
if [ ! -f "$SO" ] || [ "$SRC" -nt "$SO" ]; then
  if command -v gcc >/dev/null && gcc -O2 -shared -fPIC -o "$SO.tmp" "$SRC" -ldl 2>>"$HU_LOG"; then
    chmod 755 "$SO.tmp"; mv -f "$SO.tmp" "$SO"; say "hudiy: contacts clean-up built"
  else
    rm -f "$SO.tmp"; warn "hudiy: couldn't build the contacts clean-up (gcc); Hudiy reads the contacts as they come"
  fi
fi
# Start Hudiy (CarPlay / Android Auto) when the desktop starts, through bin/hu-hudiy-run.sh (Hudiy's own
# hudiy_run.sh with the contacts clean-up). Hudiy's installer adds its own line: that one is switched over.
RUN="$H/.hudiy/share/hudiy_run.sh"; WRAP=/opt/headunit/bin/hu-hudiy-run.sh
if [ -f "$RUN" ]; then
  udir "$H/.config/labwc"; touch "$AS"
  sed -i -E "s#^([[:space:]]*)(\\\$HOME|$H)/\.hudiy/share/hudiy_run\.sh#\1$WRAP#" "$AS"
  if ! grep -qsE "hudiy_run|hu-hudiy-run" "$AS" && [ ! -f "$H/.config/autostart/hudiy.desktop" ]; then
    echo "$WRAP &" >> "$AS"; say "hudiy: added to the desktop autostart"
  fi
  [ -f "$H/.config/autostart/hudiy.desktop" ] && sed -i -E "s#^Exec=.*hudiy_run\.sh#Exec=$WRAP#" "$H/.config/autostart/hudiy.desktop"
  chown "$(hu_user):$(hu_user)" "$AS"
fi
say "hudiy: added Map, Music, Radio and Head Unit Settings to the menu"
