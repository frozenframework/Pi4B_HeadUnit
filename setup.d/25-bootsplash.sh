#!/usr/bin/env bash
# Stage 25: your own boot image instead of the Raspberry Pi logo / rainbow screen.
# The picture is a PNG you supply (it isn't part of this project): bootfs/headunit/boot-splash.png on the
# SD card (make-image.py --splash puts it there), or /etc/headunit/boot-splash.png. Without one, the boot
# screen is left as it is. To change it later: copy a new PNG to bootfs/headunit/boot-splash.png and run
#   sudo /opt/headunit/setup.sh --only 25-bootsplash
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
SRC=""
for f in /boot/firmware/headunit/boot-splash.png /etc/headunit/boot-splash.png; do [ -f "$f" ] && SRC=$f && break; done
if [ -z "$SRC" ]; then say "boot image: none supplied, keeping the current boot screen"; exit 0; fi
command -v plymouth-set-default-theme >/dev/null || { warn "plymouth isn't installed: boot image skipped"; exit 0; }
T=/usr/share/plymouth/themes/headunit
install -d "$T"
install -m 644 "$HU_ROOT/plymouth/headunit.plymouth" "$HU_ROOT/plymouth/headunit.script" "$T/"
# Keep a copy outside the card's boot folder, so the theme survives if that file is removed.
[ "$SRC" = /etc/headunit/boot-splash.png ] || install -m 644 "$SRC" /etc/headunit/boot-splash.png
# Flatten onto black (Plymouth draws transparent pixels unpredictably on some screens).
python3 - /etc/headunit/boot-splash.png "$T/splash.png" <<'PY' || install -m 644 /etc/headunit/boot-splash.png "$T/splash.png"
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGBA")
im.thumbnail((1920, 1080))
bg = Image.new("RGB", im.size, (0, 0, 0)); bg.paste(im, mask=im.split()[3]); bg.save(sys.argv[2])
PY
if [ "$(plymouth-set-default-theme)" != headunit ] || [ "$SRC" -nt /boot/firmware/initramfs8 ]; then
  say "boot image: installing (rebuilding the boot RAM disk, about a minute)"
  plymouth-set-default-theme -R headunit >/dev/null 2>&1 || plymouth-set-default-theme headunit
fi
# No rainbow test screen before it; keep "splash" on the kernel command line so Plymouth shows.
put_block "$BOOT_CFG" bootsplash "disable_splash=1"
grep -qw splash "$BOOT_CMDLINE" || sed -i '1s/$/ quiet splash/' "$BOOT_CMDLINE"
say "boot image: $(basename "$SRC") as the boot screen (Plymouth theme 'headunit')"
