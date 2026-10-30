#!/usr/bin/env bash
# Stage 40: make the USB audio adapter (feeds the amp) the default output instead of HDMI.
source /opt/headunit/lib/common.sh
U=$(hu_user); H=$(hu_home)
udir "$H/.config/wireplumber/wireplumber.conf.d"
cat > "$H/.config/wireplumber/wireplumber.conf.d/51-headunit.conf" <<'WP'
# Prefer any USB audio output (Adafruit adapter, Behringer UCA202, etc.) over HDMI and the headphone jack.
monitor.alsa.rules = [
  {
    matches = [ { node.name = "~alsa_output.usb-*" } ]
    actions = { update-props = { priority.session = 2000, priority.driver = 2000 } }
  }
  {
    matches = [ { node.name = "~alsa_output.platform-*hdmi*" } ]
    actions = { update-props = { priority.session = 100, priority.driver = 100 } }
  }
]
WP
chown -R "$U:$U" "$H/.config/wireplumber"
say "audio: USB output preferred (channels: $(conf_get AUDIO_CHANNELS 2))"
