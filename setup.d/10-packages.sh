#!/usr/bin/env bash
# Stage 10: system packages + a Python virtual environment for the offline router (Valhalla).
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
export DEBIAN_FRONTEND=noninteractive
PKGS=(
  mpd mpc python3-mpd            # music library (iPod / USB) read by song tags
  gpsd gpsd-clients python3-gps  # GPS receiver -> nav app and Hudiy's Android Auto
  python3-aiohttp python3-gpiozero python3-venv
  espeak-ng                      # spoken turn-by-turn prompts
  hfsplus                        # Mac-formatted iPods (HFS+) - read only
  exfatprogs jq curl rsync wlr-randr
  git openssh-client             # software updates: signed release tags from GitHub
)
[ "$(conf_get RADIO 0)" = 1 ] && PKGS+=(rtl-sdr)
say "installing: ${PKGS[*]}"
# Offline is fine when everything is already installed (or was pre-downloaded by the updater).
apt-get update -qq || warn "no internet: using the package lists already on the Pi"
apt-get install -y -qq --no-install-recommends "${PKGS[@]}"

# Valhalla (routing) isn't packaged for Debian 13, but an official arm64 wheel exists on PyPI.
# The venv lives outside the version folders so software updates don't have to rebuild it.
VENV=/opt/headunit-venv
if [ ! -x $VENV/bin/python ]; then
  python3 -m venv --system-site-packages $VENV
fi
[ -d "$HU_ROOT/venv" ] && [ ! -L "$HU_ROOT/venv" ] || ln -sfn $VENV "$HU_ROOT/venv"
for spec in $(sed -n 's/^pip://p' "$HU_ROOT/config/packages.txt"); do
  name=${spec%%==*} want=${spec#*==}
  have=$($VENV/bin/python -c "import importlib.metadata as m; print(m.version('$name'))" 2>/dev/null || true)
  [ "$have" = "$want" ] && continue
  $VENV/bin/pip install -q --no-index --find-links /var/cache/headunit/wheels "$spec" 2>/dev/null \
    || { $VENV/bin/pip install -q --upgrade pip || true; $VENV/bin/pip install -q "$spec"; }
done
$VENV/bin/python -c "import valhalla; print('valhalla ok')"
