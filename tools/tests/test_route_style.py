"""Route styles: Fastest, or Less stops (a stick shift: fewer traffic lights, fewer stops where a small road
meets a bigger one, fewer turns) even when it's longer - but never more than 50 % longer. The real
Valhalla on a small test town: Main St (short, a light every 300 m) or the loop road (longer, no lights)."""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import osmium, valhalla, hu_valhalla_tar as V, hu_route_style as S
from valhalla import get_config, Actor

T = Path(tempfile.mkdtemp())

# 1) counting, on hand-made edges: lights, a stop onto a bigger road, none on a ramp / roundabout, turns
E = lambda rc, sig=False, cross=(), use="road", rb=False: {"road_class": rc, "use": use, "roundabout": rb,
     "end_node": {"traffic_signal": sig, "intersecting_edges": [{"road_class": c} for c in cross]}}
edges = [E("residential", cross=["residential"]),            # residential x residential: no stop counted
         E("residential", cross=["primary"]),                # crosses a primary: stop
         E("residential"),                                   # then turns onto a secondary (next edge): stop
         E("secondary", sig=True),                           # a light
         E("secondary", use="ramp"),                         # ramp onto the freeway: no stop
         E("motorway"), E("tertiary", rb=True), E("primary"), E("primary")]
c = S.count(edges, [{"type": 15}, {"type": 10}, {"type": 9}, {"type": 13}])
assert c == {"signal": 1, "stop": 2, "left": 1, "right": 1, "uturn": 1}, c
assert S.score(100, c) == 100 + 25 + 40 + 14 + 8 + 30
print("1) lights, stops onto / across bigger roads (not from ramps or in roundabouts), turns counted")

# 2) a test town: Main St 3 km with 9 lights; the loop road around it is longer but has none
pbf = T / "m.osm.pbf"; w = osmium.SimpleWriter(str(pbf)); N = osmium.osm.mutable.Node; W = osmium.osm.mutable.Way
ids = iter(range(1, 10000)); wids = iter(range(1, 10000))
def node(lon, lat, tags=None):
    i = next(ids); w.add_node(N(id=i, location=(lon, lat), tags=tags or {})); return i
def way(nodes, tags): w.add_way(W(id=next(wids), nodes=nodes, tags=tags))
y0, x0, x1 = 30.30, -95.50, -95.47
main = [node(x0 + (x1 - x0) * k / 10, y0, {"highway": "traffic_signals"} if 0 < k < 10 else {}) for k in range(11)]
way(main, {"highway": "secondary", "name": "Main St"})
for k in range(1, 10):
    way([node(x0 + (x1 - x0) * k / 10, y0 - 0.004), main[k], node(x0 + (x1 - x0) * k / 10, y0 + 0.004)], {"highway": "residential", "name": f"Cross {k}"})
n1, n2 = node(x0, y0 + 0.014), node(x1, y0 + 0.014)
way([main[0], n1], {"highway": "primary", "name": "West Rd"}); way([n1, n2], {"highway": "trunk", "name": "Loop 336", "maxspeed": "65 mph"})
way([n2, main[-1]], {"highway": "primary", "name": "East Rd"})
s, e = node(x0 - 0.004, y0), node(x1 + 0.004, y0)
way([s, main[0]], {"highway": "secondary", "name": "Main St"}); way([main[-1], e], {"highway": "secondary", "name": "Main St"})
w.close()
tiles = T / "tiles"; tiles.mkdir()
cfg = get_config(tile_extract="", tile_dir=str(tiles), verbose=False); cfg["mjolnir"]["timezone"] = ""; cfg["mjolnir"]["admin"] = ""
(T / "v.json").write_text(json.dumps(cfg))
subprocess.run([str(Path(valhalla.__file__).parent / "bin" / "valhalla_build_tiles"), "-c", str(T / "v.json"), str(pbf)], check=True, capture_output=True)
data = T / "data"; (data / "routing").mkdir(parents=True); V.pack(tiles, data / "routing" / "valhalla_tiles.tar")
actor = Actor(get_config(tile_extract=str(data / "routing" / "valhalla_tiles.tar"), tile_dir="", verbose=False))
req = {"locations": [{"lon": -95.504, "lat": 30.30}, {"lon": -95.466, "lat": 30.30}], "costing": "auto"}
fast = json.loads(actor.route(json.dumps(req)))["trip"]
trip, info = S.plan(actor, req)
names = lambda t: [n for m in t["legs"][0]["maneuvers"] for n in (m.get("street_names") or [])]
assert "Loop 336" not in names(fast) and "Loop 336" in names(trip), (names(fast), names(trip))
assert info["fastest"]["stops"] >= 9 and info["stops"] < info["fastest"]["stops"] and info["signals"] == 0, info
assert trip["summary"]["time"] > fast["summary"]["time"], "longer, but fewer stops"
print(f"2) Less stops takes the loop road: {info['stops']} stops instead of {info['fastest']['stops']}, "
      f"{trip['summary']['time'] - fast['summary']['time']:.0f} s longer")

# 3) never more than 50 % longer: with the limit at 1 %, the fastest stays
S.MAX_EXTRA = 0.01
trip2, info2 = S.plan(actor, req); S.MAX_EXTRA = 0.5
assert "Loop 336" not in names(trip2), "too long a detour isn't taken"
_, q = S.plan(actor, req, quick=True); assert q["fastest"] is None
print("3) a detour longer than the limit isn't taken; a reroute (quick) asks Valhalla less")

# 4) through the bridge: style from the request, or the saved setting; the screen gets the comparison
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\nDIAG_AUTO=0\n")
for d in ("user", "usage", "run"): (T / d).mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T), "XDG_RUNTIME_DIR": str(T / "run")}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
try:
    for _ in range(80):
        try: call("/api/settings"); break
        except OSError: time.sleep(0.25)
    body = {"from": [-95.504, 30.30], "to": [-95.466, 30.30]}
    r = call("/api/route", body); assert r["style"] == "fastest" and r["styleInfo"] is None, r.get("style")
    r = call("/api/route", {**body, "style": "manual"})
    assert r["style"] == "manual" and r["styleInfo"]["stops"] < r["styleInfo"]["fastest"]["stops"], r["styleInfo"]
    assert any("Loop 336" in m["streets"] for m in r["maneuvers"])
    call("/api/settings", {"routeStyle": "manual"})
    r = call("/api/route", {**body, "quick": True}); assert r["style"] == "manual" and r["styleInfo"]["fastest"] is None
    assert call("/api/settings")["routeStyle"] == "manual"
finally:
    b.terminate(); b.wait(5)
print("4) bridge: per route or from the saved setting (Go sheet: Fastest / Less stops); reroutes keep the style")

nav = (ROOT / "apps/nav/nav.js").read_text()
assert "style, quick: reason === 'reroute'" in nav and "$('routeStyle').onclick = toggleStyle" in nav
en = json.loads((ROOT / "apps/common/i18n.json").read_text())["en"]
assert en["nav.style_fastest"] == "Fastest" and en["nav.style_manual"] == "Less stops"
print("5) the Go sheet offers Fastest / Less stops")
print("route style: all tests passed")
