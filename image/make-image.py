#!/usr/bin/env python3
"""
Build ready-to-flash head-unit images WITHOUT Docker or root (works on macOS, Linux, Windows).

  python3 -m pip install pyfatfs
  python3 image/make-image.py --base ~/Downloads/<raspios trixie arm64 desktop>.img.xz --vehicle both

How it works (only the FAT "bootfs" partition is touched; the Linux partition stays the official one):
  1. decompresses the official Raspberry Pi OS (64-bit, Desktop) image
  2. copies this project to bootfs:/headunit/package, plus headunit.conf, README-FIRST.txt, hudiy/
  3. adds a one-shot hook to cmdline.txt (systemd.run=...firstrun.sh, the same mechanism Raspberry Pi
     Imager used on Bookworm). On first power-up firstrun.sh installs the package to /opt/headunit,
     enables headunit-firstboot.service, removes itself from cmdline.txt and reboots.
     It also does what Raspberry Pi's setup wizard would ask (no wizard appears): user "headunit"
     (password "headunit", change it with passwd), US / American English / America/Chicago / US
     keyboard, all set in headunit.conf and changeable later in Settings > Region & language.
  4. second boot: the normal first-boot flow (Wi-Fi -> Hudiy -> setup.sh -> maps -> reboot)
Output: out/headunit-<vehicle>-<date>.img.xz + .sha256
Run it on a fresh copy of the base image each time (it does that for you); don't re-patch a patched image.
"""
import argparse, datetime, hashlib, lzma, os, shutil, struct, subprocess, sys, tempfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "out", "node_modules", "__pycache__", "docs", "hudiy", "private"}   # hudiy/, private/: your licensed / personal files
SKIP_EXT = {".stl", ".step", ".img", ".xz"}

FIRSTRUN = r"""#!/bin/bash
# One-shot: runs once from the kernel command line, before the normal first boot.
# It sets up what Raspberry Pi's on-screen setup wizard would otherwise ask for (user, country,
# language, time zone, keyboard), installs the head-unit files, then removes itself and reboots.
B=/boot/firmware/headunit
LOG=$B/firstboot.log
exec >>"$LOG" 2>&1
echo "==== firstrun $(date)"
CONF=$B/headunit.conf
get() { local v; v=$(grep -E "^$1=" "$CONF" 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"//; s/"$//'); echo "${v:-$2}"; }

# Each version lives in its own folder; /opt/headunit is a symlink to the one in use (see bin/hu-update.py).
V=$(cat "$B/package/VERSION" 2>/dev/null || echo 0.0.0)
mkdir -p "/opt/headunit-releases/$V"
cp -r "$B/package/." "/opt/headunit-releases/$V/"
rm -rf /opt/headunit; ln -sfn "/opt/headunit-releases/$V" /opt/headunit
chmod +x /opt/headunit/setup.sh /opt/headunit/bin/* /opt/headunit/setup.d/*.sh /opt/headunit/image/*.sh /opt/headunit/tools/*.sh 2>/dev/null

# ---- country, language, time zone, keyboard (defaults: United States, American English, Chicago)
COUNTRY=$(get WIFI_COUNTRY US); TZONE=$(get TIMEZONE America/Chicago)
LOCALE=$(get LOCALE en_US.UTF-8); KBD=$(get KEYBOARD us)
raspi-config nonint do_change_locale "$LOCALE" || echo "!! locale $LOCALE failed"
raspi-config nonint do_change_timezone "$TZONE" || echo "!! time zone $TZONE failed"
raspi-config nonint do_configure_keyboard "$KBD" >/dev/null || echo "!! keyboard $KBD failed"
raspi-config nonint do_wifi_country "$COUNTRY" || echo "!! Wi-Fi country $COUNTRY failed"
echo "region: $COUNTRY, $LOCALE, $TZONE, keyboard $KBD"

# ---- default user (skipped if Raspberry Pi Imager's OS customisation set one up instead)
if grep -qE '^[[:space:]]*(users|user):' /boot/firmware/user-data 2>/dev/null; then
  echo "Raspberry Pi Imager customisation found: it creates the user"
else
  U=$(get HU_USERNAME headunit); P=$(get HU_PASSWORD headunit)
  [[ "$U" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "!! HU_USERNAME '$U' isn't valid; using headunit"; U=headunit; }
  G=""; for g in adm dialout cdrom audio users sudo video games plugdev input gpio spi i2c netdev render lpadmin; do
    getent group "$g" >/dev/null && G="$G,$g"; done
  FIRST=$(getent passwd 1000 | cut -d: -f1)
  mkdir -p /var/lib/userconf-pi; touch /var/lib/userconf-pi/autologin   # = desktop autologin, not a login screen
  if ! id "$U" >/dev/null 2>&1; then
    if [ -n "$FIRST" ]; then
      # The image ships a placeholder account (uid 1000, "pi", no login) that Raspberry Pi's setup
      # wizard renames. Do the same: rename it, give it a shell, then cancel the wizard.
      /usr/lib/userconf-pi/userconf "$FIRST" "$U" "" || { usermod -l "$U" -d "/home/$U" -m "$FIRST"; groupmod -n "$U" "$FIRST"; }
    else
      useradd -m -u 1000 -U -s /bin/bash "$U"
    fi
  fi
  usermod -s /bin/bash -aG "${G#,}" "$U"
  [ -d "/home/$U" ] || { cp -a /etc/skel "/home/$U"; chown -R "$U:$U" "/home/$U"; }
  echo "$U:$P" | chpasswd
  echo "$U ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/010_pi-nopasswd; chmod 440 /etc/sudoers.d/010_pi-nopasswd
  # Raspberry Pi's own "skip the wizard" path: desktop autologin as this user, remove the wizard.
  mkdir -p /var/lib/userconf-pi; touch /var/lib/userconf-pi/autologin
  SUDO_USER="$U" /usr/bin/cancel-rename "$U" || { SUDO_USER="$U" raspi-config nonint do_boot_behaviour B4; rm -f /etc/xdg/autostart/piwiz.desktop; }
  touch /etc/cloud/cloud-init.disabled        # nothing left for cloud-init to set up (it would add a locked "pi" user)
  sed -i -E 's/^(HU_PASSWORD)=.*/\1="(set on first boot)"/' "$CONF"
  echo "user: $U (uid $(id -u "$U"), desktop autologin: $(grep '^autologin-user=' /etc/lightdm/lightdm.conf | cut -d= -f2))"
  # While setup runs (next boot), show its progress on the desktop instead of a plain desktop.
  install -d -o "$U" -g "$U" "/home/$U/.config" "/home/$U/.config/labwc"
  echo "/opt/headunit/bin/hu-setup-progress.sh &" >> "/home/$U/.config/labwc/autostart"
  chown "$U:$U" "/home/$U/.config/labwc/autostart"
fi
H=$(get HOSTNAME "$(get VEHICLE car)-headunit")
[[ "$H" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] && raspi-config nonint do_hostname "$H" && echo "hostname: $H"
[ "$(get ENABLE_SSH 0)" = 1 ] && raspi-config nonint do_ssh 0 && echo "ssh: on"

# Diagnostics report on the card (bootfs/headunit/diag.txt) every boot until collect-diagnostics is deleted.
install -m 644 /opt/headunit/systemd/headunit-diag.service /etc/systemd/system/
mkdir -p /etc/systemd/system/graphical.target.wants
ln -sf /etc/systemd/system/headunit-diag.service /etc/systemd/system/graphical.target.wants/headunit-diag.service
touch "$B/collect-diagnostics"
install -m 644 /opt/headunit/systemd/headunit-firstboot.service /etc/systemd/system/
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/headunit-firstboot.service /etc/systemd/system/multi-user.target.wants/headunit-firstboot.service
sed -i 's| systemd.run=[^ ]*firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target||' /boot/firmware/cmdline.txt
echo "firstrun done $(date)"
sync
exit 0
"""
HOOK = " systemd.run=/boot/firmware/headunit/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"

def log(*a):
    print(datetime.datetime.now().strftime("%H:%M:%S"), *a, flush=True)

def download_latest(cache):
    url = "https://downloads.raspberrypi.com/raspios_arm64_latest"
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
        real = r.geturl()
    dest = cache / Path(real).name
    if not dest.exists():
        log("downloading", real)
        with urllib.request.urlopen(real, timeout=120) as r, open(str(dest) + ".part", "wb") as f:
            shutil.copyfileobj(r, f, 1 << 22)
        os.replace(str(dest) + ".part", dest)
    want = urllib.request.urlopen(real + ".sha256", timeout=60).read().decode().split()[0]
    check_sha(dest, want)
    return dest

def check_sha(p, want):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""): h.update(b)
    if h.hexdigest() != want:
        sys.exit(f"SHA-256 mismatch for {p.name}")
    log("sha256 ok", p.name)

def decompress(src, dst):
    log("decompressing", src.name)
    if src.suffix == ".xz":
        if shutil.which("xz"):
            with open(dst, "wb") as out:
                subprocess.run(["xz", "-dc", "-T0", str(src)], stdout=out, check=True)
        else:
            with lzma.open(src) as fi, open(dst, "wb") as fo:
                shutil.copyfileobj(fi, fo, 1 << 22)
    else:
        shutil.copyfile(src, dst)

def boot_partition(img):
    with open(img, "rb") as f:
        mbr = f.read(512)
    if mbr[510:512] != b"\x55\xaa":
        sys.exit("not an MBR disk image")
    for i in range(4):
        e = mbr[446 + 16 * i: 462 + 16 * i]
        ptype = e[4]; start, size = struct.unpack("<II", e[8:16])
        if ptype in (0x0B, 0x0C, 0x0E) and size:
            return start * 512
    sys.exit("no FAT boot partition found")

def fat_mkdirs(fs, path):
    parts = [p for p in path.split("/") if p]
    cur = ""
    for p in parts:
        cur += "/" + p
        if not fs.exists(cur):
            fs.makedir(cur)

def fat_write(fs, path, data: bytes):
    fat_mkdirs(fs, path.rsplit("/", 1)[0])
    if fs.exists(path):
        fs.remove(path)        # pyfatfs can corrupt a file it overwrites in place; delete and recreate instead
    with fs.openbin(path, "w") as f:
        f.write(data)

def package_files():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in files:
            p = Path(root) / n
            if p.suffix.lower() in SKIP_EXT or n == ".DS_Store":
                continue
            yield p, p.relative_to(HERE).as_posix()

def patch_boot(img, vehicle, tier, region, hudiy, sets=None, splash=None):
    """Write the head-unit files + first-run hook into the image's FAT boot partition (in place)."""
    from pyfatfs.PyFatFS import PyFatFS
    off = boot_partition(img)
    log("bootfs at offset", off)
    fs = PyFatFS(str(img), offset=off, preserve_case=True)
    try:
        # FAT short names may be stored upper-case (CMDLINE.TXT); find the file case-insensitively.
        cmd_name = next((n for n in fs.listdir("/") if n.lower() == "cmdline.txt"), None)
        if not cmd_name:
            sys.exit("cmdline.txt not found on bootfs: is this a Raspberry Pi OS image?")
        cmd = fs.readtext("/" + cmd_name).strip()
        if "headunit/firstrun.sh" not in cmd:
            fat_write(fs, "/" + cmd_name, (cmd + HOOK + "\n").encode())
        fat_write(fs, "/headunit/firstrun.sh", FIRSTRUN.encode())
        conf = (HERE / "config" / "headunit.conf.example").read_text()
        sets = {"VEHICLE": vehicle, "TIER": tier, "MAP_REGION": region, **(sets or {})}
        lines = conf.splitlines()
        for k, v in sets.items():                      # replace KEY=... in place, or add it at the end
            i = next((n for n, l in enumerate(lines) if l.startswith(k + "=")), None)
            if i is None: lines.append(f"{k}={v}")
            else: lines[i] = f"{k}={v}"
        conf = "\n".join(lines) + "\n"
        fat_write(fs, "/headunit/headunit.conf", conf.encode())
        vname = next((l.split('"')[1] for l in (HERE / "config" / f"{vehicle}.conf").read_text().splitlines() if l.startswith("VEHICLE_NAME")), vehicle)
        fat_write(fs, "/headunit/README-FIRST.txt", (
            f"Head unit image for: {vname}\r\n"
            "1. Edit headunit.conf in this folder if needed (tier, map region, Wi-Fi).\r\n"
            "2. Put the Hudiy installer from your purchase email into the 'hudiy' folder.\r\n"
            "3. Eject, put the card in the Pi, and power it on the bench with Wi-Fi in range.\r\n"
            "   It reboots twice by itself. First boot takes 15-45 minutes.\r\n"
            "   Progress log: firstboot.log in this folder.\r\n").encode())
        if splash:   # your boot image (stays out of the project and GitHub; setup.d/25-bootsplash.sh uses it)
            fat_write(fs, "/headunit/boot-splash.png", Path(splash).read_bytes())
        fat_mkdirs(fs, "/headunit/hudiy")
        if hudiy:
            fat_write(fs, f"/headunit/hudiy/{Path(hudiy).name}", Path(hudiy).read_bytes())
        n = 0
        for p, rel in package_files():
            fat_write(fs, f"/headunit/package/{rel}", p.read_bytes()); n += 1
        log(f"wrote {n} package files to bootfs:/headunit/package")
    finally:
        fs.close()

def compress(img, preset="6"):
    log("compressing (this takes a few minutes)")
    if shutil.which("xz"):
        subprocess.run(["xz", "-T0", f"-{preset}", "-f", str(img)], check=True)
    else:
        with open(img, "rb") as fi, lzma.open(str(img) + ".xz", "wb", preset=int(preset)) as fo:
            shutil.copyfileobj(fi, fo, 1 << 22)
        img.unlink()
    xz = Path(str(img) + ".xz")
    h = hashlib.sha256()
    with open(xz, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""): h.update(b)
    xz.with_name(xz.name + ".sha256").write_text(f"{h.hexdigest()}  {xz.name}\n")
    log("done:", xz, f"({xz.stat().st_size / 2**30:.2f} GB)")

def build(base_img, vehicle, tier, region, hudiy, out, do_compress=True, sets=None, splash=None):
    img = out / f"headunit-{vehicle}-{datetime.date.today():%Y%m%d}.img"
    decompress(base_img, img)
    patch_boot(img, vehicle, tier, region, hudiy, sets, splash)
    if do_compress:
        compress(img)
    else:
        log("done (uncompressed):", img)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="official Raspberry Pi OS 64-bit Desktop .img.xz (downloads the latest if omitted)")
    ap.add_argument("--sha256", help="expected SHA-256 of --base (from raspberrypi.com)")
    ap.add_argument("--vehicle", default="both", help="f250, miata, frontier, both (f250+miata) or all")
    ap.add_argument("--tier", default="2"); ap.add_argument("--region", default="texas-plus")
    ap.add_argument("--hudiy", help="Hudiy installer file to include")
    ap.add_argument("--splash", help="PNG to show while the Pi boots, instead of the Raspberry Pi logo")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--no-compress", action="store_true", help="leave a plain .img (Raspberry Pi Imager flashes it too)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="preset a line in the card's headunit.conf, e.g. --set DISPLAY_TYPE=monitor (repeatable)")
    a = ap.parse_args()
    out = Path(a.out); (out / "cache").mkdir(parents=True, exist_ok=True)
    base = Path(a.base).expanduser() if a.base else download_latest(out / "cache")
    if a.sha256:
        check_sha(base, a.sha256)
    for v in {"both": ["f250", "miata"], "all": ["f250", "miata", "frontier"]}.get(a.vehicle, [a.vehicle]):
        sets = dict(kv.split("=", 1) for kv in a.set)
        build(base, v, a.tier, a.region, a.hudiy, out, do_compress=not a.no_compress, sets=sets, splash=a.splash)

if __name__ == "__main__":
    main()
