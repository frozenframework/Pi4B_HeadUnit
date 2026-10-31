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
  4. second boot: the normal first-boot flow (Wi-Fi -> Hudiy -> setup.sh -> maps -> reboot)
Output: out/headunit-<vehicle>-<date>.img.xz + .sha256
Run it on a fresh copy of the base image each time (it does that for you); don't re-patch a patched image.
"""
import argparse, datetime, hashlib, lzma, os, shutil, struct, subprocess, sys, tempfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "out", "node_modules", "__pycache__", "docs"}
SKIP_EXT = {".stl", ".step", ".img", ".xz"}

FIRSTRUN = r"""#!/bin/bash
# One-shot: runs once from the kernel command line, before the normal first boot.
B=/boot/firmware/headunit
mkdir -p /opt/headunit
cp -r "$B/package/." /opt/headunit/
chmod +x /opt/headunit/setup.sh /opt/headunit/bin/* /opt/headunit/setup.d/*.sh /opt/headunit/image/*.sh 2>/dev/null
install -m 644 /opt/headunit/systemd/headunit-firstboot.service /etc/systemd/system/
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/headunit-firstboot.service /etc/systemd/system/multi-user.target.wants/headunit-firstboot.service
sed -i 's| systemd.run=[^ ]*firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target||' /boot/firmware/cmdline.txt
echo "firstrun done $(date)" >> "$B/firstboot.log"
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

def patch_boot(img, vehicle, tier, region, hudiy):
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
        conf = "\n".join(
            f"VEHICLE={vehicle}" if l.startswith("VEHICLE=") else f"TIER={tier}" if l.startswith("TIER=")
            else f"MAP_REGION={region}" if l.startswith("MAP_REGION=") else l for l in conf.splitlines()) + "\n"
        fat_write(fs, "/headunit/headunit.conf", conf.encode())
        vname = next((l.split('"')[1] for l in (HERE / "config" / f"{vehicle}.conf").read_text().splitlines() if l.startswith("VEHICLE_NAME")), vehicle)
        fat_write(fs, "/headunit/README-FIRST.txt", (
            f"Head unit image for: {vname}\r\n"
            "1. Edit headunit.conf in this folder if needed (tier, map region, Wi-Fi).\r\n"
            "2. Put the Hudiy installer from your purchase email into the 'hudiy' folder.\r\n"
            "3. Eject, put the card in the Pi, and power it on the bench with Wi-Fi in range.\r\n"
            "   It reboots twice by itself. First boot takes 15-45 minutes.\r\n"
            "   Progress log: firstboot.log in this folder.\r\n").encode())
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

def build(base_img, vehicle, tier, region, hudiy, out, do_compress=True):
    img = out / f"headunit-{vehicle}-{datetime.date.today():%Y%m%d}.img"
    decompress(base_img, img)
    patch_boot(img, vehicle, tier, region, hudiy)
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
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--no-compress", action="store_true", help="leave a plain .img (Raspberry Pi Imager flashes it too)")
    a = ap.parse_args()
    out = Path(a.out); (out / "cache").mkdir(parents=True, exist_ok=True)
    base = Path(a.base).expanduser() if a.base else download_latest(out / "cache")
    if a.sha256:
        check_sha(base, a.sha256)
    for v in {"both": ["f250", "miata"], "all": ["f250", "miata", "frontier"]}.get(a.vehicle, [a.vehicle]):
        build(base, v, a.tier, a.region, a.hudiy, out, do_compress=not a.no_compress)

if __name__ == "__main__":
    main()
