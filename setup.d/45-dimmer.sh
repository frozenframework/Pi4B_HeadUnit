#!/usr/bin/env bash
# Stage 45: screen dimmer for every screen (Head Unit Settings > Display).
#   Touch Display 2 -> real backlight (/sys/class/backlight)
#   HDMI screens that answer DDC/CI -> their own brightness control over the HDMI cable
#   everything else (Waveshare 7.9") -> a click-through black overlay above all apps
source /opt/headunit/lib/common.sh
U=$(hu_user)
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq --no-install-recommends ddcutil python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1
# Let the desktop user change the backlight without root.
cat > /etc/udev/rules.d/92-headunit-backlight.rules <<'R'
SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys%p/brightness", RUN+="/bin/chmod g+w /sys%p/brightness"
R
# DDC/CI goes over the HDMI cable's I2C lines: needs i2c-dev (separate from the GPIO I2C bus, which stays off).
echo i2c-dev > /etc/modules-load.d/headunit-i2c-dev.conf
modprobe i2c-dev 2>/dev/null || true
usermod -aG video,i2c "$U" 2>/dev/null || true
udevadm control --reload 2>/dev/null || true
enable_user_unit /opt/headunit/systemd/headunit-dimmer.service
say "dimmer: enabled (method picked automatically; change it in Head Unit Settings > Display)"
