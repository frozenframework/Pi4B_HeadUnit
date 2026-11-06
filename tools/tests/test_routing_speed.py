"""Fast routing start: the map builder packs Valhalla's tiles with index.bin first (Valhalla then reads
one small index instead of every tile header across the SD card); a tar from an older build gets one
on the head unit, in the background, without changing a byte of the tiles; the real Valhalla routes
from both; and the screens' files are cached by version so the map opens without re-reading them."""
import json, os, shutil, socket, subprocess, sys, tarfile, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import osmium, valhalla, hu_valhalla_tar as V
from valhalla import get_config, Actor

T = Path(tempfile.mkdtemp())
# a road grid big enough for several tiles on two levels (primary roads: level 1, residential: level 2)
pbf = T / "g.osm.pbf"; w = osmium.SimpleWriter(str(pbf)); N = osmium.osm.mutable.Node; W = osmium.osm.mutable.Way
nid, grid = 1, {}
for i in range(7):
    for j in range(7):
        w.add_node(N(id=nid, location=(-95.6 + i * 0.09, 30.1 + j * 0.09))); grid[i, j] = nid; nid += 1
for k in range(7):
    w.add_way(W(id=2 * k + 1, nodes=[grid[k, j] for j in range(7)], tags={"highway": "residential", "name": f"Col {k}"}))
    w.add_way(W(id=2 * k + 2, nodes=[grid[j, k] for j in range(7)], tags={"highway": "primary" if k % 3 == 0 else "residential", "name": f"Row {k}"}))
w.close()
tiles = T / "tiles"; tiles.mkdir()
cfg = get_config(tile_extract="", tile_dir=str(tiles), verbose=False); cfg["mjolnir"]["timezone"] = ""; cfg["mjolnir"]["admin"] = ""
(T / "v.json").write_text(json.dumps(cfg))
subprocess.run([str(Path(valhalla.__file__).parent / "bin" / "valhalla_build_tiles"), "-c", str(T / "v.json"), str(pbf)],
               check=True, capture_output=True)
gph = sorted(tiles.rglob("*.gph")); assert len(gph) >= 3 and len({p.relative_to(tiles).parts[0] for p in gph}) >= 2, gph

def route(tar):
    a = Actor(get_config(tile_extract=str(tar), tile_dir="", verbose=False))
    r = json.loads(a.route(json.dumps({"locations": [{"lon": -95.6, "lat": 30.1}, {"lon": -95.06, "lat": 30.64}], "costing": "auto"})))
    return r["trip"]["summary"]["length"]

# 1) the builder: index.bin first, every entry pointing at its tile's bytes
new = T / "new.tar"; n = V.pack(tiles, new)
assert n == len(gph) and V.has_index(new)
with tarfile.open(new) as t: assert t.getnames()[0] == "index.bin"
raw = new.read_bytes()
for off, tid, size in V.check(new):
    name = next(p for p in gph if V.tile_id(p.relative_to(tiles).as_posix()) == tid)
    assert raw[off:off + size] == name.read_bytes(), name
assert V.tile_id("2/000/692/978.gph") == 2 | (692978 << 3)
length = route(new); assert length > 50, length
print(f"1) packed {n} tiles with index.bin; each entry points at its tile; Valhalla routes from it ({length:.0f} km)")

# 2) a tar from an older build (no index): gets one in place, same tiles, same route
old = T / "old.tar"
with tarfile.open(old, "w") as t:
    for f in gph: t.add(f, arcname=f.relative_to(tiles).as_posix())
assert not V.has_index(old) and abs(route(old) - length) < 0.01
seen = []
assert V.add_index(old, lambda d, k: seen.append((d, k))) and V.has_index(old) and seen[-1] == (len(gph), len(gph))
assert abs(route(old) - length) < 0.01 and not Path(str(old) + ".part").exists()
with tarfile.open(old) as t: assert sorted(m.name for m in t if m.name.endswith(".gph")) == sorted(p.relative_to(tiles).as_posix() for p in gph)
plain = T / "plain.tar"
with tarfile.open(plain, "w") as t: t.add(gph[0], arcname=gph[0].relative_to(tiles).as_posix())
assert V.add_index(plain, min_free=1 << 60) is False and not V.has_index(plain), "no room: left as it was"
print("2) an older tar gets index.bin in place (same tiles, same route); without room it's left alone")

# 3) the bridge: indexes an old tar in the background after start-up, warms up, routes; caching headers
data = T / "data"; (data / "routing").mkdir(parents=True)
with tarfile.open(data / "routing" / "valhalla_tiles.tar", "w") as t:
    for f in gph: t.add(f, arcname=f.relative_to(tiles).as_posix())
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=0\n")
(T / "user").mkdir(); (T / "usage").mkdir()
(T / "user" / "settings.json").write_text(json.dumps({"lastPos": [-95.6, 30.1]}))
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T)}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def get(p, headers=None):
    return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{p}", headers=headers or {}), timeout=20)
try:
    for _ in range(60):
        try: st = json.loads(get("/api/status").read()); break
        except OSError: time.sleep(0.25)
    for _ in range(80):
        if V.has_index(data / "routing" / "valhalla_tiles.tar") and "routing ready" in (T / "b.log").read_text(): break
        time.sleep(0.25)
    assert V.has_index(data / "routing" / "valhalla_tiles.tar"), (T / "b.log").read_text()[-800:]
    assert "routing ready" in (T / "b.log").read_text(), "warmed up after start-up"
    assert json.loads(get("/api/status").read())["maps"]["routingPrep"]["phase"] == "done"
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/route", data=json.dumps({"from": [-95.6, 30.1], "to": [-95.06, 30.64]}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read()); assert len(r["shape"]) > 2 and r["maneuvers"], r
    print("3) the bridge gives an older tar index.bin in the background, loads the router early, routes")

    ver = json.loads(get("/api/status").read())["version"]
    h = get(f"/apps/lib/pmtiles.mjs?v={ver}").headers
    assert "immutable" in h["Cache-Control"], h["Cache-Control"]
    h = get("/apps/lib/pmtiles.mjs").headers; assert h["Cache-Control"] == "no-cache", h["Cache-Control"]
    h = get("/apps/lib/pmtiles.mjs?v=0.0.1").headers; assert h["Cache-Control"] == "no-cache", "an old version's address isn't kept"
    lm = get("/apps/styles/neon-overdrive.json").headers["Last-Modified"]
    try: get("/apps/styles/neon-overdrive.json", {"If-Modified-Since": lm}); raise AssertionError("expected 304")
    except urllib.error.HTTPError as e: assert e.code == 304, e.code
    assert get("/apps/nav/index.html").headers["Cache-Control"] == "no-store"
    assert get("/api/status").headers["Cache-Control"] == "no-store"
    print("4) screen files cached by version (map code kept, not re-read), others checked (304), pages and data never cached")
finally:
    b.terminate(); b.wait(5)
print("routing speed: all tests passed")
