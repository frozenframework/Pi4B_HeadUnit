"""Adding house numbers OpenStreetMap lacks (maps/add-addresses.py): Texas 911 address points are
downloaded page by page from the TxGIO service (a stand-in server here), continue after an interruption,
go into an existing search.sqlite without duplicates, and then a country-road house is found exactly
(not a same-named street in another city). Files you downloaded (GeoJSON / CSV) work too; push-maps
can send just the search data."""
import importlib.util, json, sys, tempfile, threading, urllib.parse, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import osmium, hu_addr

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
br = load("br", ROOT / "maps/build-routing.py"); aa = load("aa", ROOT / "maps/add-addresses.py")

T = Path(tempfile.mkdtemp()); pbf = T / "t.osm.pbf"
w = osmium.SimpleWriter(str(pbf)); N = osmium.osm.mutable.Node; W = osmium.osm.mutable.Way
nid = 1
def node(lon, lat, tags=None):
    global nid; w.add_node(N(id=nid, location=(lon, lat), tags=tags or {})); nid += 1; return nid - 1
node(-95.4560, 30.3119, {"place": "city", "name": "Conroe"})
node(-95.3900, 30.2400, {"addr:housenumber": "10", "addr:street": "Other Road", "addr:postcode": "77302"})
e = [node(-95.4100 + i * 0.003, 30.2450) for i in range(4)]
w.add_way(W(id=1, nodes=e, tags={"highway": "residential", "name": "Bramble Road"}))
for n, x in ((400, -97.8600), (700, -97.8560)):
    node(x, 30.3250, {"addr:housenumber": str(n), "addr:street": "Bramble Way", "addr:city": "Austin"})
node(-95.4100, 30.2449, {"addr:housenumber": "455", "addr:street": "Bramble Road"})       # one already in OSM
w.close()
out = T / "HEADUNIT_MAPS"; out.mkdir(); (T / "work").mkdir()
br.build_search([pbf], out, addresses=True, work=T / "work")
(out / "manifest.json").write_text(json.dumps({"region": "texas", "name": "Texas only", "format": 1}))
db = out / "search.sqlite"
con = sqlite3.connect(db)
r = hu_addr.lookup(con, "463 Bramble Rd, Conroe, TX 77302", [-95.46, 30.31])
assert r[0]["kind"] == "address_approx" or r[0]["kind"] == "street_only", r
con.close()

# a stand-in for the TxGIO address point service: 5 Montgomery County points, 2 per page
PTS = [{"objectid": 10 + i, "add_number": str(n), "addnum_suf": None, "st_premod": None, "st_predir": None, "st_pretyp": None,
        "st_presep": None, "st_name": "BRAMBLE", "st_postyp": "ROAD", "st_posdir": None, "st_posmod": None,
        "full_addr": f"{n} BRAMBLE ROAD", "post_comm": "CONROE", "post_code": "77302", "county": "Montgomery", "_xy": (-95.4110 + i * 0.001, 30.2449)}
       for i, n in enumerate((451, 455, 459, 463, 467))]
PTS.append({**PTS[0], "objectid": 30, "add_number": "309", "st_pretyp": "MO COUNTY ROAD", "st_name": "309", "st_postyp": None, "_xy": (-95.5, 30.4)})
calls = []; cut = {"once": True}
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query)); calls.append(q)
        mine = [p for p in PTS if "Montgomery" in q["where"]]
        if q.get("returnCountOnly"):
            body = {"count": len(mine)}
        else:
            last = int(q["where"].rsplit(">", 1)[1]); page = [p for p in mine if p["objectid"] > last][:2]
            if last >= 12 and cut["once"]:          # the connection drops part way: the next run continues
                cut["once"] = False; self.send_response(500); self.end_headers(); return
            body = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": p["objectid"], "geometry": {"type": "Point", "coordinates": list(p["_xy"])},
                    "properties": {k: v for k, v in p.items() if k != "_xy"}} for p in page], "exceededTransferLimit": len(page) == 2}
        b = json.dumps(body).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/query"
aa.time.sleep = lambda s: None
f = aa.fetch_county("Montgomery", T / "work" / "addresses", url)
lines = f.read_text().splitlines(); assert len(lines) == 6, lines
assert (T / "work/addresses/txgio-montgomery.last").read_text() == "30"
assert any(">" in c.get("where", "") and c["where"].endswith("> 11") for c in calls), "continued from the last objectid after the drop"
n0 = len(calls); aa.fetch_county("Montgomery", T / "work" / "addresses", url)
assert len(f.read_text().splitlines()) == 6 and len(calls) - n0 == 2, "a second run fetches only what's new"
try: aa.fetch_county("Montgomry", T / "work" / "addresses", url); raise AssertionError("misspelt county")
except SystemExit as e: assert "Check the spelling" in str(e)
print("1) 911 address points downloaded page by page; continues after a drop; nothing fetched twice")

assert aa.add_to_db(db, [f]) == 5, "455 was already in OpenStreetMap"
assert aa.add_to_db(db, [f]) == 0, "running it again adds nothing"
con = sqlite3.connect(db)
r = hu_addr.lookup(con, "463 Bramble Rd, Conroe, TX 77302", [-95.46, 30.31])
assert r[0]["kind"] == "address" and r[0]["name"] == "463 Bramble Rd" and abs(r[0]["lon"] + 95.4080) < 1e-6 and r[0]["detail"] == "Conroe 77302", r
r = hu_addr.lookup(con, "309 County Road 309, Conroe", [-95.46, 30.31]); assert r and r[0]["kind"] == "address", ("'MO County Road 309' = County Road 309", r)
assert con.execute("SELECT n FROM areas WHERE kind='zip' AND key='77302'").fetchone()[0] == 6
con.close()
print("2) added to the search data without duplicates; the country-road house is found exactly")

# files you downloaded: a GeoJSON FeatureCollection and a CSV (NAD-style columns)
(T / "a.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-95.4200, 30.2500]}, "properties": {"Add_Number": 12, "St_Name": "Quail", "St_PosTyp": "Court", "Post_City": "Conroe", "Zip_Code": "77302"}}]}, indent=1))
(T / "b.csv").write_text("FULL_ADDR,CITY,ZIP,LONGITUDE,LATITUDE\n14 Quail Ct,Conroe,77302,-95.4201,30.2501\n")
(T / "c.csv").write_text("ADDRESS,X,Y\n1 Main St,3150000,13900000\n")
assert aa.add_to_db(db, [T / "a.geojson", T / "b.csv"]) == 2
try: aa.add_to_db(db, [T / "c.csv"]); raise AssertionError("projected coordinates")
except SystemExit as e: assert "GeoJSON instead" in str(e)
con = sqlite3.connect(db); r = hu_addr.lookup(con, "14 Quail Ct 77302"); assert r[0]["kind"] == "address", r
print("3) downloaded GeoJSON / CSV files work; projected coordinates are refused with advice")

# push-maps --only search sends just the search data (+ the manifest)
pm = load("pm", ROOT / "tools/push-maps.py"); sent = []
class P:
    def __init__(self, *a): pass
    def run(self, cmd, **k):
        class R: returncode = 0; stdout = "1000000000000\n"; stderr = ""
        return R()
    def size(self, p): return -1
pm.Pi = P; pm.push_file = lambda pi, f, tries: sent.append(f.name)
(out / "valhalla_tiles.tar").write_bytes(b"x")
sys.argv = ["push-maps.py", str(out), "--only", "search"]; pm.main()
assert sent == ["search.sqlite", "manifest.json"], sent
print("4) push-maps --only search sends just search.sqlite and the manifest")
print("add addresses: all tests passed")
