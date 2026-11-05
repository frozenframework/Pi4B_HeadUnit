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
node(-95.5000, 30.3300, {"addr:housenumber": "4500", "addr:street": "State Highway 105 West", "addr:city": "Conroe"})
node(-95.4555, 30.3115, {"name": "A&W Restaurant", "amenity": "fast_food", "addr:housenumber": "125", "addr:street": "North Main Street"})
street = [node(-95.4561, 30.3090 + i * 0.002) for i in range(8)]
w.add_way(W(id=1, nodes=street, tags={"highway": "residential", "name": "North Main Street"}))
b = [node(-95.4570, 30.3150), node(-95.4569, 30.3150), node(-95.4569, 30.3151), node(-95.4570, 30.3151)]
w.add_way(W(id=2, nodes=b + [b[0]], tags={"building": "house", "addr:housenumber": "200", "addr:street": "N Main St", "addr:city": "Conroe"}))
# statewide: the same street and numbers in several towns (most OpenStreetMap addresses have no city)
node(-96.7970, 32.7767, {"place": "city", "name": "Dallas"}); node(-95.4560, 30.3119, {"place": "city", "name": "Conroe"})
node(-95.3011, 32.3513, {"place": "city", "name": "Tyler"}); node(-93.7502, 32.5252, {"place": "city", "name": "Shreveport"})
for n, lat in ((500, 32.7800), (520, 32.7810)):
    node(-96.8000, lat, {"addr:housenumber": str(n), "addr:street": "Elm Street", "addr:postcode": "75201"})
for n, lat in ((500, 30.3300), (520, 30.3310)):
    node(-95.4400, lat, {"addr:housenumber": str(n), "addr:street": "Elm Street"})                 # Conroe, no city
node(-95.3000, 32.3500, {"addr:housenumber": "500", "addr:street": "Elm Street", "addr:city": "Tyler"})
node(-93.7500, 32.5200, {"addr:housenumber": "500", "addr:street": "Elm Street", "addr:city": "Shreveport"})
for x, y in ((-96.8001, 32.7790), (-95.4401, 30.3290)):
    e = [node(x, y + i * 0.002) for i in range(3)]; nid += 1
    w.add_way(W(id=nid, nodes=e, tags={"highway": "residential", "name": "Elm Street"}))
node(-95.4300, 30.3000, {"name": "Walmart Supercenter", "shop": "supermarket"})
# a road in the country outside Conroe with no house numbers mapped; a Way of the same name in Austin with numbers
node(-95.3900, 30.2400, {"addr:housenumber": "10", "addr:street": "Other Road", "addr:postcode": "77302"})
e = [node(-95.4100 + i * 0.003, 30.2450) for i in range(4)]; nid += 1
w.add_way(W(id=nid, nodes=e, tags={"highway": "residential", "name": "Bramble Road"}))
for n, x in ((400, -97.8600), (700, -97.8560)):
    node(x, 30.3250, {"addr:housenumber": str(n), "addr:street": "Bramble Way", "addr:city": "Austin"})
w.close()

spec = importlib.util.spec_from_file_location("br", ROOT / "maps/build-routing.py"); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
out = T / "HEADUNIT_MAPS"; out.mkdir(); work = T / "work"; work.mkdir()
br.build_search([pbf], out, addresses=True, work=work)
con = sqlite3.connect(out / "search.sqlite")
assert con.execute("SELECT value FROM meta WHERE key='format'").fetchone() == ("3",)
assert con.execute("SELECT count(*) FROM areas WHERE kind='zip'").fetchone()[0] == 3
assert con.execute("SELECT count(*) FROM addr").fetchone()[0] == 17, "4 Conroe + Houston + SH 105 + A&W + the house (a building)"
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
r = L("140 N Main St"); assert r[0]["name"] == "140 N Main St", ("N after the number is North, not apartment 140N", r)
for q in ("4500 Hwy 105 W", "4500 Highway 105", "4500 TX-105 W, Conroe", "4500 State Highway 105 West"):
    r = L(q); assert r and r[0]["kind"] == "address" and abs(r[0]["lat"] - 30.33) < 1e-6, (q, r)
assert L("4500 Hwy 105 W")[0]["name"] == "4500 SH 105 W"
print("2) exact house numbers: Street/St, North/N, with or without city/ZIP/comma, nearest first")

r = L("130 N Main St", CONROE); assert r[0]["kind"] == "address_approx" and 30.3110 < r[0]["lat"] < 30.3120, r
r = L("5000 N Main St", CONROE); assert r[0]["kind"] == "street_only", r
assert L("120 Nowhere Blvd") == [] and L("Joe's Tacos") == []
print("3) unmapped number estimated between its neighbours; otherwise the street; nonsense finds nothing")

# 4) the whole state, not just around you: the ZIP / city typed first, then Texas, then nearest
SHREVEPORT = [-93.75, 32.52]
r = L("500 Elm St, Dallas", CONROE); assert r[0]["lat"] == 32.78 and r[0]["detail"] == "Dallas 75201", ("Dallas typed, near Conroe", r)
r = L("500 Elm St 75201", CONROE); assert r[0]["lat"] == 32.78, ("ZIP typed", r)
r = L("500 Elm St Dallas TX 75201", CONROE); assert r[0]["lat"] == 32.78, r
r = L("500 Elm St", CONROE); assert r[0]["lat"] == 30.33 and len(r) == 4, ("no city: nearest first, all listed", r)
r = L("500 Elm St", SHREVEPORT); assert r[0]["detail"] == "Tyler" and r[-1]["detail"] == "Shreveport, LA", ("Texas first", r)
r = L("500 Elm St, Shreveport LA", CONROE); assert r[0]["detail"] == "Shreveport", ("another state typed", r)
r = L("510 Elm St, Dallas", CONROE); assert r[0]["kind"] == "address_approx" and 32.78 < r[0]["lat"] < 32.781, ("estimated in Dallas", r)
r = L("900 Elm St, Dallas", CONROE); assert r[0]["kind"] == "street_only" and r[0]["lat"] > 32, ("the street in Dallas", r)
r = L("463 Bramble Rd, Conroe, TX 77302", CONROE)
assert r[0]["kind"] == "street_only" and r[0]["name"] == "Bramble Rd" and abs(r[0]["lat"] - 30.245) < 1e-3, ("your road, not a Way in Austin", r)
r = L("463 Bramble Way, Austin", CONROE); assert r[0]["kind"] == "address_approx" and r[0]["name"] == "463 Bramble Way", r
assert hu_addr.state_of(-95.46, 30.31) == "TX" and hu_addr.state_of(-93.75, 32.52) == "LA" and hu_addr.state_of(-96.39, 33.99) == "OK"
o = hu_addr.suggest(con, "500 Elmm St, Dallas", CONROE)
assert o[0]["option"] and o[0]["name"] == "500 Elm St" and o[0]["lat"] == 32.78 and any(x["kind"] == "area" for x in o), o
o = hu_addr.suggest(con, "wallmart", CONROE); assert o and o[0]["name"] == "Walmart Supercenter", o
print("4) whole state searched: ZIP / city typed first, then Texas, then nearest; options for misspellings")

# 5) through the real head-unit search (bridge), incl. a business name with '&'
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
    r = search("walmart conroe"); assert r and r[0]["name"] == "Walmart Supercenter", r
    r = search("500 Elmm St, Dallas"); assert r and all(x.get("option") for x in r) and r[0]["name"] == "500 Elm St", r
    r = search("zzqx"); assert r == [], r
    # search data from before addresses (no addr table): the street still comes up for a typed address
    old = sqlite3.connect(data / "search" / "search.sqlite"); old.execute("DROP TABLE areas"); old.commit(); old.close()
    for _ in range(40):                                   # ZIP centres worked out in the background
        r = search("500 Elm St 75201")
        if r[0]["lat"] == 32.78: break
        time.sleep(0.25)
    assert r[0]["lat"] == 32.78 and (T / "user" / "search-zips.json").exists(), r
    old = sqlite3.connect(data / "search" / "search.sqlite"); old.execute("DROP TABLE addr"); old.commit(); old.close()
    r = search("120 N Main St, Conroe TX 77301"); assert r and r[0]["kind"] == "street_only" and r[0]["name"] == "North Main Street", r
finally:
    b.terminate(); b.wait(5)
print("5) head-unit search: addresses first, business names with '&', streets by name; old data still finds the street")
print("address search: all tests passed")
