"""Map push over Wi-Fi: tools/push-maps.py sends HEADUNIT_MAPS through ssh, survives a dropped connection
mid-file (continues from the last byte), skips files already there, then runs the importer, which moves
the files into place. A fake `ssh` runs the commands locally against a temp folder standing in for the Pi."""
import json, os, random, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp()); PI = T / "pi"; BIN = T / "bin"; BIN.mkdir(); (PI / "var/lib/headunit").mkdir(parents=True)
pack = T / "HEADUNIT_MAPS"; pack.mkdir()
tiles = random.Random(1).randbytes(7_300_000); db = random.Random(2).randbytes(2_100_000)
(pack / "valhalla_tiles.tar").write_bytes(tiles); (pack / "search.sqlite").write_bytes(db)
(pack / "manifest.json").write_text(json.dumps({"format": 1, "region": "texas-plus", "name": "Texas+", "built": "2026-11-05T10:00:00"}))
# importer stand-in: the real script's move logic with the Pi's paths pointed into PI
imp = (ROOT / "bin/hu-import-maps.sh").read_text().replace("/var/lib/headunit", f"{PI}/var/lib/headunit") \
      .replace('chown -R "$(stat -c %U $D)": "$D"', "true")
(BIN / "hu-import-maps.sh").write_text(imp); (BIN / "hu-import-maps.sh").chmod(0o755)
fake = f'''#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
while args and args[0].startswith("-"): args = args[2:]          # drop -o X / -p N
cmd = args[1].replace("/var/lib/headunit", "{PI}/var/lib/headunit").replace("sudo -n /opt/headunit/bin/", "{BIN}/")
cnt = "{T}/cat-calls"
if "cat >>" in cmd:
    n = int(open(cnt).read()) + 1 if os.path.exists(cnt) else 1
    open(cnt, "w").write(str(n))
    if n == 1:                                   # Wi-Fi drops 3 MB into the first file
        data = sys.stdin.buffer.read(3_000_000)
        with open(cmd.split(">> ")[1].strip("'"), "ab") as f: f.write(data)
        sys.exit(255)
sys.exit(subprocess.run(["bash", "-c", cmd]).returncode)
'''
(BIN / "ssh").write_text(fake); (BIN / "ssh").chmod(0o755)
env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}"}
src = (ROOT / "tools/push-maps.py").read_text().replace("time.sleep(wait)", "time.sleep(0.1)")
(T / "push.py").write_text(src)
r = subprocess.run([sys.executable, str(T / "push.py"), str(pack), "--host", "test"], env=env, capture_output=True, text=True, timeout=60)
print(r.stdout[-900:]); assert r.returncode == 0, r.stderr
D = PI / "var/lib/headunit"
assert (D / "routing/valhalla_tiles.tar").read_bytes() == tiles and (D / "search/search.sqlite").read_bytes() == db
assert json.loads((D / "maps/manifest.json").read_text())["name"] == "Texas+"
assert not (D / "incoming/HEADUNIT_MAPS").exists(), "inbox should be emptied"
assert "continuing from" in r.stdout and "interrupted" in r.stdout
print("1) dropped connection mid-file: continued from the last byte; files moved into place; inbox emptied")
# Running it again with a newer build only sends what's missing; a half-sent file from an older run restarts
(D / "incoming/HEADUNIT_MAPS").mkdir(parents=True)
(D / "incoming/HEADUNIT_MAPS/search.sqlite").write_bytes(db)            # already there
r = subprocess.run([sys.executable, str(T / "push.py"), str(pack), "--host", "test"], env=env, capture_output=True, text=True, timeout=60)
assert r.returncode == 0 and "search.sqlite: already on the head unit" in r.stdout, r.stdout + r.stderr
print("2) files already on the head unit are skipped")
print("push maps: all tests passed")
