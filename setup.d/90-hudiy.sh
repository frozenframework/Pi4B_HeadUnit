#!/usr/bin/env bash
# Stage 90: register the head-unit apps in Hudiy's menu and point Hudiy at gpsd.
source /opt/headunit/lib/common.sh
H=$(hu_home)
if [ ! -d "$H/.hudiy/share/config" ]; then
  warn "Hudiy isn't installed yet. Install it (see README), then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  exit 0
fi
as_user python3 /opt/headunit/bin/hu-hudiy-config.py
say "hudiy: added Navigation, iPod & USB, AM/FM Radio and Head Unit Settings to the menu"
