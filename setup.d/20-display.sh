#!/usr/bin/env bash
# Stage 20: screen setup. DISPLAY_TYPE in /etc/headunit/headunit.conf picks the panel:
#   waveshare-7.9       Waveshare 7.9" HDMI (400x1280 portrait panel, shown as 1280x400) - F-250, Miata
#   hdmi-1024x600       any 7" 1024x600 HDMI + USB-touch panel - Frontier (2-DIN)
#   pi-touch-display-2  official Raspberry Pi Touch Display 2 (DSI, 720x1280 portrait, shown as 1280x720)
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
TYPE=$(conf_get DISPLAY_TYPE waveshare-7.9)
ROT=$(conf_get DISPLAY_ROTATE 0)
U=$(hu_user); H=$(hu_home)

case "$TYPE" in
  waveshare-7.9)
    OUT=HDMI-A-1; MODE=400x1280@60; ROT=$(conf_get DISPLAY_ROTATE 90)
    BLOCK="hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_timings=400 0 70 10 60 1280 0 20 10 12 0 0 0 60 0 43000000 3"
    CMDV="video=HDMI-A-1:400x1280M@60,rotate=$ROT";;
  hdmi-1024x600)
    OUT=HDMI-A-1; MODE=1024x600@60
    BLOCK="hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0"
    CMDV="video=HDMI-A-1:1024x600M@60,rotate=$ROT";;
  pi-touch-display-2)
    # Portrait panel on the DSI connector; the overlay's rotation turns it landscape.
    OUT=DSI-1; MODE=720x1280@60; ROT=$(conf_get DISPLAY_ROTATE 90)
    BLOCK="dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=$ROT"
    CMDV="";;
  *) die "unknown DISPLAY_TYPE $TYPE";;
esac
put_block "$BOOT_CFG" display "$BLOCK"

# Console rotation (boot text) on the single cmdline.txt line - remove any previous headunit entry first.
sed -i -E -e 's/ ?video=HDMI-A-1:[^ ]*//' -e 's/^ +//' "$BOOT_CMDLINE"
[ -n "$CMDV" ] && sed -i "1s|^|$CMDV |" "$BOOT_CMDLINE"

# Desktop (labwc) rotation via kanshi, the same file Raspberry Pi's Screen Configuration writes.
udir "$H/.config/kanshi"
KT=$ROT; [ "$TYPE" = pi-touch-display-2 ] && KT=0      # the DSI overlay already rotates
cat > "$H/.config/kanshi/config" <<KAN
profile headunit {
    output $OUT enable mode $MODE position 0,0 transform $KT
}
KAN
chown "$U:$U" "$H/.config/kanshi/config"

# Touch follows the screen's rotation when it's mapped to the output.
RC="$H/.config/labwc/rc.xml"
udir "$H/.config/labwc"
[ -f "$RC" ] || printf '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n' > "$RC"
python3 - "$RC" "$OUT" <<'PY'
import sys, xml.etree.ElementTree as ET
p, out = sys.argv[1:]; t = ET.parse(p); r = t.getroot()
for el in r.findall("touch"):
    r.remove(el)
ET.SubElement(r, "touch", {"mapToOutput": out, "mouseEmulation": "no"})
t.write(p, xml_declaration=True, encoding="UTF-8")
PY
chown "$U:$U" "$RC"

if command -v raspi-config >/dev/null; then
  raspi-config nonint do_boot_behaviour B4 || warn "couldn't set desktop autologin"
  raspi-config nonint do_blanking 1 || true
fi
say "display: $TYPE on $OUT, rotation $ROT (change DISPLAY_ROTATE if the picture is upside down)"
