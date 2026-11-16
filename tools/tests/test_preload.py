"""Read-ahead at start-up (bin/hu-preload.py): it learns which parts of which files are in memory once
the map is up, reads exactly those ahead on the next start (and nothing of a file that changed since),
makes the list again after a software update, and every 10th start reads nothing ahead and learns
afresh; the bridge holds its own background jobs back until the map is on screen."""
import importlib.util, json, os, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp(dir=os.environ.get("HU_TEST_TMP") or str(Path.home())))      # a real disk (not memory-backed /tmp)
os.environ.update(HU_PRELOAD_LIST=str(T / "preload.json"), HU_PRELOAD_STATUS=str(T / "status.json"), HU_CONF=str(T / "hu.conf"),
                  HU_ROOT=str(T / "root"), HU_DATA=str(T / "data"), HU_BOOT_MARKS=str(T / "marks.json"), HU_PRELOAD_SETTLE="0", HU_PRELOAD_WAIT="3")
(T / "root").mkdir(); (T / "root/VERSION").write_text("1.0.0\n"); (T / "hu.conf").write_text("HU_USER=nobody-here\n")
spec = importlib.util.spec_from_file_location("pl", ROOT / "bin/hu-preload.py"); pl = importlib.util.module_from_spec(spec); spec.loader.exec_module(pl)

def evict(p):
    os.sync(); fd = os.open(p, os.O_RDONLY); os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED); os.close(fd)
def touch(p, off, n):
    fd = os.open(p, os.O_RDONLY); os.pread(fd, n, off); os.close(fd)
a, b = T / "program.bin", T / "data.bin"
a.write_bytes(os.urandom(6 << 20)); b.write_bytes(os.urandom(3 << 20))
evict(a); evict(b)
if pl.resident(str(a)):
    print("preload: skipped (this file system keeps everything in memory)"); sys.exit(0)
P = pl.PAGE
touch(a, 1 << 20, 200_000); touch(a, 4 << 20, 50_000); touch(b, 0, 10_000)
ra = pl.resident(str(a))
assert len(ra) == 2 and ra[0][0] <= 1 << 20 and ra[0][0] + ra[0][1] >= (1 << 20) + 200_000 and ra[1][0] <= 4 << 20, ra
assert sum(r[1] for r in ra) < 2 << 20, "only what was used, not the whole file"
print("1) the parts of a file that are in memory are found (two separate pieces of a 6 MB file)")

pl.candidates = lambda home: {str(a): 5, str(b): 9, str(T / "gone.bin"): 1}
d = pl.learn(None)
assert [f[0] for f in d["files"]] == [str(a), str(b)] and d["version"] == "1.0.0" and d["bytes"] == sum(r[1] for f in d["files"] for r in f[3])
evict(a); evict(b); assert pl.resident(str(a)) == [] and pl.resident(str(b)) == []
done, n = pl.preload(pl.read_list())
assert n == 2 and done == d["bytes"] and pl.resident(str(a)) == ra and pl.resident(str(b)), (done, d["bytes"])
b.write_bytes(os.urandom(3 << 20)); evict(a); evict(b); time.sleep(0.01); os.utime(b, (time.time() + 5, time.time() + 5))
done, n = pl.preload(pl.read_list()); assert n == 1 and pl.resident(str(b)) == [], "a file that changed since is left alone"
print("2) learned list -> exactly those pieces are read ahead; a changed file is skipped")

# the service: first start learns, later starts read ahead, the 10th measures and learns again, an update starts over
os.remove(T / "preload.json"); (T / "marks.json").write_text(json.dumps({"marks": {"map": 21.5}}))
status = lambda: json.loads((T / "status.json").read_text())
touch(a, 1 << 20, 200_000)
pl.service(); s = status(); assert s["mode"] == "learning" and s["learned_files"] >= 1, s
for i in range(1, 10):
    evict(a); pl.service(); s = status()
    assert s["mode"] == "preload" and s["files"] >= 1 and s["mb"] >= 0 and pl.resident(str(a)), (i, s)
assert pl.read_list()["boots"] == 9
learned_before = pl.read_list()["learned"]; time.sleep(1.1)
evict(a); touch(a, 2 << 20, 100_000)
pl.service(); s = status(); assert s["mode"] == "measuring" and "mb" not in s, s          # every 10th start
lst = pl.read_list(); assert lst["learned"] > learned_before and any(r[0] <= 2 << 20 < r[0] + r[1] for f in lst["files"] if f[0] == str(a) for r in f[3])
assert not any(r[0] <= 1 << 20 < r[0] + r[1] for f in lst["files"] if f[0] == str(a) for r in f[3]), "what's no longer used dropped out"
(T / "root/VERSION").write_text("1.0.1\n"); pl.service(); assert status()["mode"] == "learning" and pl.read_list()["version"] == "1.0.1"
(T / "marks.json").write_text(json.dumps({"marks": {}})); (T / "root/VERSION").write_text("1.0.2\n"); pl.service()
assert pl.read_list()["version"] == "1.0.1", "the map never came up: nothing learned from that start"
(T / "hu.conf").write_text("PRELOAD=0\n"); pl.service(); assert status() == {"mode": "off"}
print("3) service: learn, read ahead on the next starts, a measuring start every 10th, fresh list after an update, off switch")

unit = (ROOT / "systemd/headunit-preload.service").read_text()
assert "DefaultDependencies=no" in unit and "WantedBy=sysinit.target" in unit and "Conflicts=shutdown.target" in unit
br = (ROOT / "bin/hu-bridge.py").read_text()
assert "await map_up(120); await asyncio.sleep(5)" in br and "await map_up(120); ZIPS.get()" in br and 'await map_up(float(C("CONTACTS_START_WAIT"' in br
print("4) starts as early as the system allows; the bridge's own background jobs wait for the map")
print("preload: all tests passed")
