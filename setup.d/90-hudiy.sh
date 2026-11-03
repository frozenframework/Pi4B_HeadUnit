#!/usr/bin/env bash
# Stage 90: register the head-unit apps in Hudiy's menu and point Hudiy at gpsd.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
H=$(hu_home)
if [ ! -d "$H/.hudiy/share/config" ]; then
  warn "Hudiy isn't installed yet. Install it (see README), then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  exit 0
fi
as_user python3 /opt/headunit/bin/hu-hudiy-config.py
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
