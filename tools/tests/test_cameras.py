"""Privacy routing: routes around the known license-plate cameras (Flock's, or all of them) when the driver
asks for it. Cameras come from OpenStreetMap - downloaded by the head unit (Overpass, here a stand-in
server) or with the search data; Flock's are told apart; a route passing a camera is asked for again with
that spot left out; cameras at the start / destination can't be avoided and are said so; never more
than twice as long. The real Valhalla on a test town: Main St, or the loop road around it."""
import http.server, json, os, socket, sqlite3, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import osmium, valhalla, hu_valhalla_tar as V, hu_cameras as C
from valhalla import get_config, Actor
T = Path(tempfile.mkdtemp())

# 1) which cameras are Flock's
assert C.is_flock({"manufacturer": "Flock Safety"}) and C.is_flock({"manufacturer:wikidata": "Q108485435"}) and C.is_flock({"brand": "flock"})
assert not C.is_flock({"manufacturer": "Motorola Solutions"}) and not C.is_flock({})
print("1) Flock's cameras told apart from other plate readers")

# 2) the test town (Main St 3 km; the loop road around it) and its cameras
pbf = T / "m.osm.pbf"; w = osmium.SimpleWriter(str(pbf)); N = osmium.osm.mutable.Node; W = osmium.osm.mutable.Way
ids = iter(range(1, 10000)); wids = iter(range(1, 10000))
def node(lon, lat, tags=None):
    i = next(ids); w.add_node(N(id=i, location=(lon, lat), tags=tags or {})); return i
def way(nodes, tags): w.add_way(W(id=next(wids), nodes=nodes, tags=tags))
y0, x0, x1 = 30.30, -95.50, -95.47
main = [node(x0 + (x1 - x0) * k / 10, y0) for k in range(11)]
way(main, {"highway": "secondary", "name": "Main St"})
n1, n2 = node(x0, y0 + 0.014), node(x1, y0 + 0.014)
way([main[0], n1], {"highway": "primary", "name": "West Rd"}); way([n1, n2], {"highway": "trunk", "name": "Loop 336"})
way([n2, main[-1]], {"highway": "primary", "name": "East Rd"})
s, e = node(x0 - 0.004, y0), node(x1 + 0.004, y0)
way([s, main[0]], {"highway": "secondary", "name": "Main St"}); way([main[-1], e], {"highway": "secondary", "name": "Main St"})
# cameras as volunteers map them (beside the road): a Flock on Main St, a Motorola one on the loop road
node(-95.4865, 30.30012, {"man_made": "surveillance", "surveillance:type": "ALPR", "manufacturer": "Flock Safety"})
node(-95.4850, 30.31412, {"man_made": "surveillance", "surveillance:type": "ALPR", "manufacturer": "Motorola Solutions"})
w.close()
tiles = T / "tiles"; tiles.mkdir()
cfg = get_config(tile_extract="", tile_dir=str(tiles), verbose=False); cfg["mjolnir"]["timezone"] = ""; cfg["mjolnir"]["admin"] = ""
(T / "v.json").write_text(json.dumps(cfg))
subprocess.run([str(Path(valhalla.__file__).parent / "bin" / "valhalla_build_tiles"), "-c", str(T / "v.json"), str(pbf)], check=True, capture_output=True)
data = T / "data"; (data / "routing").mkdir(parents=True); (data / "search").mkdir(); (data / "maps").mkdir()
V.pack(tiles, data / "routing" / "valhalla_tiles.tar")
(data / "maps" / "manifest.json").write_text(json.dumps({"region": "test", "bbox": [-95.6, 30.2, -95.4, 30.4]}))

# the cameras into search data, the way the map builder / add-fuel.py do it
import importlib.util
spec = importlib.util.spec_from_file_location("af", ROOT / "maps/add-fuel.py"); af = importlib.util.module_from_spec(spec); spec.loader.exec_module(af)
db = data / "search" / "search.sqlite"
con = sqlite3.connect(db); con.execute("CREATE VIRTUAL TABLE places USING fts5(name, kind UNINDEXED, detail, lon UNINDEXED, lat UNINDEXED)"); con.commit(); con.close()
cams = []; af.add_to_db(db, af.read_fuel([pbf], cams), cams)
assert sorted(c[2] for c in cams) == [0, 1], cams
print("2) the map tools put the plate cameras (and which are Flock's) into the search data")

# 3) on the route, at the ends
K = C.Cameras(db, T / "user" / "alpr-cameras.json")
st = K.status(); assert st["count"] == 2 and st["flock"] == 1 and st["source"] == "search", st
mainline = [[-95.504, 30.30], [-95.466, 30.30]]
assert len(K.on_route(mainline, "flock")[0]) == 1 and len(K.on_route(mainline, "all")[0]) == 1
loop = [[-95.504, 30.30], [-95.50, 30.30], [-95.50, 30.314], [-95.47, 30.314], [-95.47, 30.30], [-95.466, 30.30]]
assert K.on_route(loop, "flock") == ([], []) and len(K.on_route(loop, "all")[0]) == 1
avoidable, stuck = K.on_route(mainline, "flock", ends=[(-95.487, 30.30)]); assert not avoidable and len(stuck) == 1
print("3) cameras on a route found (Flock's or all); one at the start / destination counted as unavoidable")

# 4) avoiding them with the real Valhalla
cfg = get_config(tile_extract=str(data / "routing" / "valhalla_tiles.tar"), tile_dir="", verbose=False); cfg["service_limits"]["max_exclude_locations"] = 5000
actor = Actor(cfg)
from_to = {"locations": [{"lon": -95.504, "lat": 30.30}, {"lon": -95.466, "lat": 30.30}], "costing": "auto"}
def route_fn(r):
    t = json.loads(actor.route(json.dumps(r)))["trip"]
    return t, decode(t["legs"][0]["shape"]), None
def decode(sh):
    coords, idx, lat, lon = [], 0, 0, 0
    while idx < len(sh):
        for which in (0, 1):
            shift = res = 0
            while True:
                b = ord(sh[idx]) - 63; idx += 1; res |= (b & 0x1F) << shift; shift += 5
                if b < 0x20: break
            d = ~(res >> 1) if res & 1 else res >> 1
            if which == 0: lat += d
            else: lon += d
        coords.append([lon / 1e6, lat / 1e6])
    return coords
names = lambda t: [n for m in t["legs"][0]["maneuvers"] for n in (m.get("street_names") or [])]
trip, shape, _, info = C.avoid(route_fn, from_to, K, "flock")
assert "Loop 336" in names(trip) and info == {"which": "flock", "passed": 0, "unavoidable": 0, "avoided": 1, "known": 2}, (names(trip), info)
trip, shape, _, info = C.avoid(route_fn, from_to, K, "all")            # the loop has a (Motorola) camera too: Main St, 1 either way
assert info["passed"] == 1, info
trip, _, _, info = C.avoid(route_fn, from_to, K, "flock", max_extra=0.01)
assert "Loop 336" not in names(trip) and info["passed"] == 1, "a detour longer than allowed isn't taken"
near_dest = {"locations": [{"lon": -95.504, "lat": 30.30}, {"lon": -95.4855, "lat": 30.30}], "costing": "auto"}
trip, _, _, info = C.avoid(route_fn, near_dest, K, "flock")
assert info["unavoidable"] == 1 and info["passed"] == 1 and trip, info
print("4) the route goes around Main St's Flock camera (loop road); all cameras: the fewer; a camera at the destination is said so")

# 5) the head unit downloads the region's cameras (stand-in Overpass server)
asked = []
class Overpass(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        asked.append(urllib.parse.unquote_plus(self.rfile.read(int(self.headers["Content-Length"])).decode()))
        body = json.dumps({"elements": [
            {"type": "node", "id": 1, "lat": 30.30012, "lon": -95.4865, "tags": {"surveillance:type": "ALPR", "manufacturer": "Flock Safety"}},
            {"type": "node", "id": 2, "lat": 30.31412, "lon": -95.4850, "tags": {"surveillance:type": "ALPR", "manufacturer": "Motorola Solutions"}},
            {"type": "way", "id": 3, "center": {"lat": 30.25, "lon": -95.45}, "tags": {"surveillance:type": "ALPR", "brand": "Flock Safety"}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
import urllib.parse
srv = http.server.HTTPServer(("127.0.0.1", 0), Overpass); threading.Thread(target=srv.serve_forever, daemon=True).start()
ovp = f"http://127.0.0.1:{srv.server_port}/api/interpreter"
assert K.update([-95.6, 30.2, -95.4, 30.4], urls=["http://127.0.0.1:9/nothing-here", ovp]) == 3       # first server down: the next one
assert 'surveillance:type"="ALPR"' in asked[0] and "(30.2,-95.6,30.4,-95.4)" in asked[0], asked
st = K.status(); assert st["count"] == 3 and st["flock"] == 2 and st["source"] == "download" and st["updated"], st
print("5) downloaded from OpenStreetMap (Overpass; a second server if the first is down); newer than the search data: used")

# 6) through the bridge: the option per route or as set; the map's camera icons; Update in Settings
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\nDIAG_AUTO=0\n")
for d in ("u2", "usage", "run"): (T / d).mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "u2"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T),
       "XDG_RUNTIME_DIR": str(T / "run"), "HU_OVERPASS": ovp}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
try:
    for _ in range(80):
        try: s0 = call("/api/settings"); break
        except OSError: time.sleep(0.25)
    assert s0["avoidCameras"] == "off" and s0["avoidCamerasWhich"] == "flock"
    body = {"from": [-95.504, 30.30], "to": [-95.466, 30.30]}
    r = call("/api/route", body); assert r["cameras"] is None and not any("Loop 336" in m["streets"] for m in r["maneuvers"])
    r = call("/api/route", {**body, "avoid": "flock"})
    assert r["cameras"]["avoided"] == 1 and r["cameras"]["passed"] == 0 and any("Loop 336" in m["streets"] for m in r["maneuvers"]), r["cameras"]
    call("/api/settings", {"avoidCameras": "flock"})
    r = call("/api/route", {**body, "style": "manual"}); assert r["cameras"]["passed"] == 0 and r["style"] == "manual", "with Less stops too"
    v = call("/api/cameras?bbox=-95.6,30.2,-95.4,30.4"); assert len(v["features"]) == 1, "Flock only, as set"
    assert len(call("/api/cameras?bbox=-95.6,30.2,-95.4,30.4&which=all")["features"]) == 2
    r = call("/api/cameras", {"op": "update"}); assert r["ok"]
    for _ in range(40):
        st = call("/api/cameras")
        if st["count"] == 3 and not st["busy"]: break
        time.sleep(0.25)
    assert st["count"] == 3 and st["source"] == "download", st
finally:
    b.terminate(); b.wait(5)
print("6) bridge: per route or as set (with Less stops too); camera icons for the map's view; Update from Settings")

nav = (ROOT / "apps/nav/nav.js").read_text()
assert "avoid: camsOn() ? state.settings.avoidCameras : 'off'" in nav and "$('privacyBtn').onclick = togglePrivacy" in nav
assert "addFuelLayer(); addCamLayer(); addRouteLayers();" in nav
print("7) the Go sheet has Avoid Flock next to Fastest / Less stops; the cameras show on the map while it's on")
del actor; import gc; gc.collect()
print("cameras: all tests passed")
