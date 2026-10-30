#!/usr/bin/env bash
# Stage 10: system packages + a Python virtual environment for the offline router (Valhalla).
source /opt/headunit/lib/common.sh
export DEBIAN_FRONTEND=noninteractive
PKGS=(
  mpd mpc python3-mpd            # music library (iPod / USB) read by song tags
  gpsd gpsd-clients python3-gps  # GPS receiver -> nav app and Hudiy's Android Auto
  python3-aiohttp python3-gpiozero python3-venv
  espeak-ng                      # spoken turn-by-turn prompts
  hfsplus                        # Mac-formatted iPods (HFS+) - read only
  exfatprogs jq curl rsync wlr-randr
)
[ "$(conf_get RADIO 0)" = 1 ] && PKGS+=(rtl-sdr)
say "installing: ${PKGS[*]}"
apt-get update -qq
apt-get install -y -qq --no-install-recommends "${PKGS[@]}"

# Valhalla (routing) isn't packaged for Debian 13, but an official arm64 wheel exists on PyPI.
if [ ! -x /opt/headunit/venv/bin/python ]; then
  python3 -m venv --system-site-packages /opt/headunit/venv
fi
/opt/headunit/venv/bin/pip install -q --upgrade pip
/opt/headunit/venv/bin/pip install -q "pyvalhalla==3.9.1"
/opt/headunit/venv/bin/python -c "import valhalla; print('valhalla ok')"
