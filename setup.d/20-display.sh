#!/usr/bin/env bash
# Stage 20: Waveshare 7.9" HDMI (400x1280 portrait panel) shown as 1280x400 landscape, touch aligned.
source /opt/headunit/lib/common.sh
ROT=$(conf_get DISPLAY_ROTATE 90)
U=$(hu_user); H=$(hu_home)

# Waveshare's timings (used by the legacy firmware path; harmless under KMS, which reads EDID).
put_block "$BOOT_CFG" display "hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_timings=400 0 70 10 60 1280 0 20 10 12 0 0 0 60 0 43000000 3"

# Console rotation (boot text) - must be on the single cmdline.txt line.
if ! grep -q 'video=HDMI-A-1:400x1280M@60' "$BOOT_CMDLINE"; then
  sed -i "1s|^|video=HDMI-A-1:400x1280M@60,rotate=$ROT |" "$BOOT_CMDLINE"
else
  sed -i -E "s|video=HDMI-A-1:400x1280M@60,rotate=[0-9]+|video=HDMI-A-1:400x1280M@60,rotate=$ROT|" "$BOOT_CMDLINE"
fi

# Desktop (labwc) rotation via kanshi, the same file Raspberry Pi's Screen Configuration writes.
udir "$H/.config/kanshi"
cat > "$H/.config/kanshi/config" <<KAN
profile headunit {
    output HDMI-A-1 enable mode 400x1280@60 position 0,0 transform $ROT
}
KAN
chown "$U:$U" "$H/.config/kanshi/config"

# Touch: map the touchscreen to the HDMI output so it follows the rotation automatically.
RC="$H/.config/labwc/rc.xml"
udir "$H/.config/labwc"
if [ ! -f "$RC" ]; then
  printf '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n' > "$RC"
fi
python3 - "$RC" <<'PY'
import sys, xml.etree.ElementTree as ET
p = sys.argv[1]; t = ET.parse(p); r = t.getroot()
for el in r.findall("touch"):
    r.remove(el)
ET.SubElement(r, "touch", {"mapToOutput": "HDMI-A-1", "mouseEmulation": "no"})
t.write(p, xml_declaration=True, encoding="UTF-8")
PY
chown "$U:$U" "$RC"

# Boot straight to the desktop with auto-login, and never blank the screen.
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_boot_behaviour B4 || warn "couldn't set desktop autologin"
  raspi-config nonint do_blanking 1 || true
fi
say "display: rotation $ROT, touch mapped to HDMI-A-1 (change DISPLAY_ROTATE to 270 if the picture is upside down)"
