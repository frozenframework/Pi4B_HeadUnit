#!/usr/bin/env bash
# Stage 75: factory backup camera -> USB composite capture stick -> Hudiy's reverse-camera view.
# The reverse wire (through the optocoupler on REVERSE_GPIO) makes hu-power show/hide the camera.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
if [ "$(conf_get CAMERA 0)" != 1 ]; then say "camera: skipped (CAMERA=0)"; exit 0; fi
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq --no-install-recommends v4l-utils gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-base
# Stable name for the capture stick (UTV007 "usbtv", STK1160 or EM28xx chips), set to NTSC on plug-in.
cat > /etc/udev/rules.d/91-headunit-camera.rules <<'R'
SUBSYSTEM=="video4linux", ATTR{index}=="0", DRIVERS=="usbtv|stk1160|em28xx", SYMLINK+="hu-rearcam", RUN+="/usr/bin/v4l2-ctl -d /dev/%k --set-standard=ntsc"
R
udevadm control --reload 2>/dev/null || true
say "camera: capture stick will appear as /dev/hu-rearcam (NTSC). Reverse wire -> GPIO$(conf_get REVERSE_GPIO 5)"
