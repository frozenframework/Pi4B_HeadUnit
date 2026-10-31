#!/usr/bin/env bash
# Stage 77: steering-wheel controls through an MCP3008 ADC on SPI (SWC=1).
source /opt/headunit/lib/common.sh
if [ "$(conf_get SWC 0)" != 1 ]; then say "steering wheel: skipped (SWC=0)"; exit 0; fi
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq --no-install-recommends python3-spidev
put_block "$BOOT_CFG" swc "dtparam=spi=on"
install -m 644 /opt/headunit/systemd/headunit-swc.service /etc/systemd/system/
systemctl daemon-reload 2>/dev/null || true
systemctl enable headunit-swc.service >/dev/null 2>&1 || true
if [ -f /etc/headunit/swc.json ]; then
  say "steering wheel: using learned buttons in /etc/headunit/swc.json"
else
  say "steering wheel: after a reboot, with the key on, run  sudo /opt/headunit/bin/hu-swc.py --learn  and press each button when asked"
fi
