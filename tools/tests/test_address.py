"""Address search: the map builder indexes OpenStreetMap house numbers and streets; the head unit finds
"1234 N Main St, Conroe" however it's typed (Street/St, North/N, with or without the city or ZIP,
nearest first), estimates numbers that aren't mapped from their neighbours, falls back to the street,
and still finds businesses with symbols in their names (A&W)."""
import importlib.util, json, os, socket, sqlite3, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import osmium, hu_addr

T = Path(tempfile.mkdtemp()); pbf = T / "t.osm.pbf"
w = osmium.SimpleWriter(str(pbf)); N = osmium.osm.mutable.Node; W = osmium.osm.mutable.Way
nid = 1
def node(lon, lat, tags=None):
    global nid; w.add_node(N(id=nid, location=(lon, lat), tags=tags or {})); nid += 1; return nid - 1
conroe = lambda n, lat: {"addr:housenumber": str(n), "addr:street": "North Main Street", "addr:city": "Conroe", "addr:postcode": "77301"}
for n, lat in ((100, 30.3100), (120, 30.3110), (140, 30.3120), (300, 30.3200)):
    node(-95.4560, lat, conroe(n, lat))
node(-95.3700, 29.7600, {"addr:housenumber": "120", "addr:street": "Main St", "addr:city": "Houston"})
node(-95.4555, 30.3115, {"name": "A&W Restaurant", "amenity": "fast_food", "addr:housenumber": "125", "addr:street": "North Main Street"})
street = [node(-95.4561, 30.3090 + i * 0.002) for i in range(8)]
w.add_way(W(id=1, nodes=street, tags={"highway": "residential", "name": "North Main Street"}))
b = [node(-95.4570, 30.3150), node(-95.4569, 30.3150), node(-95.4569, 30.3151), node(-95.4570, 30.3151)]
w.add_way(W(id=2, nodes=b + [b[0]], tags={"building": "house", "addr:housenumber": "200", "addr:street": "N Main St", "addr:city": "Conroe"}))
w.close()

spec = importlib.util.spec_from_file_location("br", ROOT / "maps/build-routing.py"); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
out = T / "HEADUNIT_MAPS"; out.mkdir(); work = T / "work"; work.mkdir()
br.build_search([pbf], out, addresses=True, work=work)
con = sqlite3.connect(out / "search.sqlite")
assert con.execute("SELECT value FROM meta WHERE key='format'").fetchone() == ("2",)
assert con.execute("SELECT count(*) FROM addr").fetchone()[0] == 7, "4 Conroe + Houston + A&W + the house (a building)"
assert con.execute("SELECT count(*) FROM street_pts").fetchone()[0] >= 2
print("1) builder: street addresses (nodes and buildings) and street points indexed")

CONROE, HOUSTON = [-95.456, 30.311], [-95.37, 29.76]
L = lambda q, near=None: hu_addr.lookup(con, q, near)
r = L("120 n main st, conroe"); assert r[0]["kind"] == "address" and abs(r[0]["lat"] - 30.3110) < 1e-6, r
r = L("120 North Main Street Conroe TX 77301"); assert abs(r[0]["lat"] - 30.3110) < 1e-6, r
r = L("120 main st houston"); assert abs(r[0]["lat"] - 29.76) < 1e-6, ("city typed without a comma", r)
r = L("120 Main St", HOUSTON); assert abs(r[0]["lat"] - 29.76) < 1e-6 and len(r) == 2, ("nearest first, both listed", r)
r = L("120 Main St", CONROE); assert abs(r[0]["lat"] - 30.3110) < 1e-6, r
r = L("200 N. Main St."); assert r[0]["kind"] == "address" and abs(r[0]["lon"] + 95.45695) < 1e-3, r
print("2) exact house numbers: Street/St, North/N, with or without city/ZIP/comma, nearest first")

r = L("130 N Main St", CONROE); assert r[0]["kind"] == "address_approx" and 30.3110 < r[0]["lat"] < 30.3120, r
r = L("5000 N Main St", CONROE); assert r[0]["kind"] == "street_only", r
assert L("120 Nowhere Blvd") == [] and L("Joe's Tacos") == []
print("3) unmapped number estimated between its neighbours; otherwise the street; nonsense finds nothing")

# 4) through the real head-unit search (bridge), incl. a business name with '&'
data = T / "data"; (data / "search").mkdir(parents=True); (out / "search.sqlite").replace(data / "search" / "search.sqlite")
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\n"); (T / "user").mkdir(); (T / "usage").mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T)}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
try:
    def search(q, near=CONROE):
        u = f"http://127.0.0.1:{port}/api/search?q={urllib.parse.quote(q)}&near={near[0]},{near[1]}"
        return json.loads(urllib.request.urlopen(u, timeout=10).read())
    for _ in range(60):
        try: search("x"); break
        except OSError: time.sleep(0.25)
    r = search("140 n main st, conroe"); assert r[0]["kind"] == "address" and abs(r[0]["lat"] - 30.3120) < 1e-6, r
    r = search("a&w"); assert any(x["name"] == "A&W Restaurant" for x in r), r
    r = search("north main"); assert any(x["kind"] == "street" for x in r), r
finally:
    b.terminate(); b.wait(5)
print("4) head-unit search: addresses first, business names with '&', streets by name")
print("address search: all tests passed")
