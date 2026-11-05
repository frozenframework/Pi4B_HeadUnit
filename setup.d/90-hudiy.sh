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
touch "$AS"; sed -i -e "\|hu-hudiy-theme.py|d" -e "\|hu-wait-bridge.sh|d" -e "\|hu-hudiy-start.py|d" "$AS"
# Start screen: Hudiy opens the offline map first (Settings > Driving > Start on the map). --config sets
# Hudiy's defaultAction before it starts; --open waits in the background and opens the map once Hudiy is up.
as_user python3 /opt/headunit/bin/hu-hudiy-start.py --config || true
sed -i "1i python3 /opt/headunit/bin/hu-hudiy-start.py --open \&" "$AS"
sed -i "1i python3 /opt/headunit/bin/hu-hudiy-start.py --config" "$AS"
# ...and Hudiy waits (up to 30 s) for the head-unit bridge, so the Drive menu apps load on the first try.
sed -i "1i /opt/headunit/bin/hu-wait-bridge.sh" "$AS"; sed -i "1i $THEME_LINE" "$AS"
chown "$(hu_user):$(hu_user)" "$AS"
# Start Hudiy (CarPlay / Android Auto) when the desktop starts. Hudiy's installer normally adds this
# itself; this only fills it in if it's missing.
RUN="$H/.hudiy/share/hudiy_run.sh"
if [ -f "$RUN" ] && ! grep -qs hudiy_run "$H/.config/labwc/autostart" /etc/xdg/labwc/autostart \
     && [ ! -f "$H/.config/autostart/hudiy.desktop" ]; then
  udir "$H/.config/labwc"
  echo "$RUN &" >> "$H/.config/labwc/autostart"
  chown "$(hu_user):$(hu_user)" "$H/.config/labwc/autostart"
  say "hudiy: added to the desktop autostart"
fi
say "hudiy: added Navigation, iPod & USB, AM/FM Radio and Head Unit Settings to the menu"
