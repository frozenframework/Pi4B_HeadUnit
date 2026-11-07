"""Fast image builds (image/xz_splice.py): only the xz blocks holding the boot partition are redone, the
rest of the official .xz is copied as it is; the result is one valid xz stream whose contents equal the
base image except inside the boot partition, which has the head-unit files; a single-block base falls
back to the full rebuild. Also: --release builds from the tag, and the region follows the tier."""
import os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
if not shutil.which("mkfs.fat") or not shutil.which("xz"):
    print("image fast: skipped (needs mkfs.fat and xz)"); sys.exit(0)

# a small "Raspberry Pi OS": MBR, a 64 MB FAT boot partition at 4 MB, then 60 MB of root data
fat = T / "boot.fat"; subprocess.run(["truncate", "-s", "64M", str(fat)], check=True)
subprocess.run(["mkfs.fat", "-F", "32", "-n", "bootfs", str(fat)], check=True, capture_output=True)
img = T / "base.img"
with open(img, "wb") as f:
    mbr = bytearray(512)
    def part(i, ptype, start, size): mbr[446 + 16 * i: 462 + 16 * i] = bytes([0, 0, 0, 0, ptype, 0, 0, 0]) + struct.pack("<II", start, size)
    part(0, 0x0C, 8192, 64 * 2048); part(1, 0x83, 8192 + 64 * 2048, 60 * 2048); mbr[510:512] = b"\x55\xaa"
    f.write(mbr); f.write(b"\0" * (4 * 2**20 - 512)); f.write(fat.read_bytes()); f.write(os.urandom(60 * 2**20))
from pyfatfs.PyFatFS import PyFatFS
fs = PyFatFS(str(img), offset=4 * 2**20); fs.writetext("/cmdline.txt", "console=tty1 root=PARTUUID=x rootwait"); fs.close()
multi = T / "multi.img.xz"; single = T / "single.img.xz"
subprocess.run(["sh", "-c", f"xz -T0 -1 --block-size=16MiB -c {img} > {multi} && xz -1 -c {img} > {single}"], check=True)

# the project at a release tag
repo = T / "repo"; shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "out", "__pycache__"))
(repo / "VERSION").write_text("9.9.9\n")
g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
g("init", "-q"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "r"); g("tag", "v9.9.9")
(repo / "bin" / "not-released.txt").write_text("x")
inst = T / "hudiy_installer.sh"; inst.write_text("#!/bin/sh\n")

def build(base, *extra):
    r = subprocess.run([sys.executable, str(repo / "image/make-image.py"), "--base", str(base), "--vehicle", "f250", "--tier", "3",
                        "--release", "9.9.9", "--out", str(T / "out"), *extra], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:] + r.stdout[-2000:]
    return r.stdout

out = build(multi, "--hudiy", str(inst))
assert "fast build: the boot partition is in the first" in out, out
xzf = T / "out" / "headunit-f250-9.9.9-with-hudiy.img.xz"
subprocess.run(["xz", "-t", str(xzf)], check=True)
lst = subprocess.run(["xz", "--robot", "--list", str(xzf)], capture_output=True, text=True, check=True).stdout
assert lst.splitlines()[1].split()[1] == "1", ("one stream", lst)
new = T / "new.img"
with open(new, "wb") as f: subprocess.run(["xz", "-dc", str(xzf)], stdout=f, check=True)
a, b = img.read_bytes(), new.read_bytes()
assert len(a) == len(b) and a[:512] == b[:512] and a[(4 + 64) * 2**20:] == b[(4 + 64) * 2**20:], "only the boot partition changed"
assert a[4 * 2**20:(4 + 64) * 2**20] != b[4 * 2**20:(4 + 64) * 2**20]
fs = PyFatFS(str(new), offset=4 * 2**20, preserve_case=True)
assert fs.readtext("/headunit/package/VERSION").strip() == "9.9.9" and not fs.exists("/headunit/package/bin/not-released.txt")
conf = fs.readtext("/headunit/headunit.conf")
assert "TIER=3" in conf and "MAP_REGION=us" in conf and "VEHICLE=f250" in conf and fs.listdir("/headunit/hudiy") == ["hudiy_installer.sh"]
assert "headunit/firstrun.sh" in fs.readtext("/cmdline.txt")
fs.close()
assert (T / "out" / "headunit-f250-9.9.9-with-hudiy.img.xz.sha256").read_text().split()[1] == xzf.name
print("1) fast build: one valid xz stream; same as the official image outside the boot partition; release files, tier 3, Hudiy")

out = build(single)
assert "fast build" not in out and (T / "out" / "headunit-f250-9.9.9.img.xz").exists()
subprocess.run(["xz", "-t", str(T / "out" / "headunit-f250-9.9.9.img.xz")], check=True)
print("2) a single-block base image: the full rebuild, as before")
print("image fast: all tests passed")
