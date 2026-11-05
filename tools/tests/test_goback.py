"""Going back to an earlier version (Settings > Updates): switches /opt/headunit to the kept older
release, re-runs its setup, never reinstalls the version you left (newer ones still install), drops
a downloaded-but-not-installed update, refuses versions that aren't there, and stays put if the old
version's setup fails. Plus the helper that clears Hudiy's web cache once per version."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp()); REL = T / "releases"; LINK = T / "headunit"; ST = T / "state"
NEEDED = ["VERSION", "setup.sh", "bin/hu-update.py", "config/release-signers", "config/packages.txt"]
def release(v, setup_ok=True):
    d = REL / v
    for n in NEEDED: (d / n).parent.mkdir(parents=True, exist_ok=True); (d / n).write_text("")
    (d / "VERSION").write_text(v + "\n")
    (d / "setup.sh").write_text(f"#!/bin/sh\necho setup {v} >> {T}/setup.log\nexit {0 if setup_ok else 1}\n"); (d / "setup.sh").chmod(0o755)
for v in ("1.2.6", "1.2.7", "1.2.8"): release(v)
(REL / ".1.2.9.incoming").mkdir()
LINK.symlink_to(REL / "1.2.8")
env = {**os.environ, "HU_LINK": str(LINK), "HU_RELEASES": str(REL), "HU_UPDATE_STATE": str(ST), "HU_UPDATE_LIB": str(T / "lib"),
       "HU_UPDATE_LOG": str(T / "update.log"), "HU_NO_REBOOT": "1", "HU_CONF": str(T / "none.conf")}
def up(*a): return subprocess.run([sys.executable, str(ROOT / "bin/hu-update.py"), *a], env=env, capture_output=True, text=True)
rj = lambda p: json.loads(Path(p).read_text())

st = json.loads(up("status").stdout)
assert st["current"] == "1.2.8" and st["older"] == ["1.2.7", "1.2.6"], st
print("1) lists the earlier versions still on the head unit:", st["older"])

ST.mkdir(); (ST / "state.json").write_text(json.dumps({"pending": "1.2.9"}))      # an update waiting to install
r = up("go-back", "1.2.7"); assert r.returncode == 0, r.stdout + r.stderr
assert os.readlink(LINK) == str(REL / "1.2.7") and (T / "setup.log").read_text().split("\n")[-2] == "setup 1.2.7"
assert rj(ST / "bad.json")["1.2.8"] == "you went back to 1.2.7"
assert not (ST / "state.json").exists(), "the waiting update is dropped"
s = rj(ST / "status.json"); assert s["state"] == "went_back" and s["history"][-1] == {**s["history"][-1], "event": "went_back", "version": "1.2.7", "from": "1.2.8"}
print("2) went back to 1.2.7: switched, its setup ran, 1.2.8 marked never-again, waiting update dropped")

r = up("go-back", "1.2.8"); assert r.returncode == 1 and os.readlink(LINK) == str(REL / "1.2.7")
r = up("go-back", "../../etc"); assert r.returncode == 2
print("3) refuses a newer version and anything that isn't a version number")

release("1.2.6", setup_ok=False)
r = up("go-back", "1.2.6"); assert r.returncode == 1 and os.readlink(LINK) == str(REL / "1.2.7"), r.stdout
assert "1.2.7" not in rj(ST / "bad.json") and rj(ST / "status.json")["state"] == "error"
print("4) if the older version's setup fails, it stays on the current one")

# web cache helper: clears QtWebEngine caches once per version, keeps everything else
H = T / "home"; c = H / ".cache/hudiy/QtWebEngine/Default/Cache/Cache_Data"; c.mkdir(parents=True); (c / "f").write_text("x")
keep = H / ".local/share/hudiy/QtWebEngine/Default/Local Storage"; keep.mkdir(parents=True)
r = subprocess.run(["bash", str(ROOT / "bin/hu-clear-webcache.sh")], env={**os.environ, "HOME": str(H)}, capture_output=True, text=True)
assert "cleared" in r.stdout and not (H / ".cache/hudiy/QtWebEngine/Default/Cache").exists() and keep.exists()
r = subprocess.run(["bash", str(ROOT / "bin/hu-clear-webcache.sh")], env={**os.environ, "HOME": str(H)}, capture_output=True, text=True)
assert r.stdout == ""
print("5) Hudiy's web cache is emptied once per version; local storage is kept")
print("go back: all tests passed")
