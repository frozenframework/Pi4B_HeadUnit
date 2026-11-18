"""Gas stations on the map: the builder keeps every station (named or not, brand, diesel); the head unit
finds them in new and older search data, keeps each station once, answers "in this view" (the map's
icons), "nearest" and "along the route ahead" (diesel first for a diesel truck), remembers them between
starts, and the bridge serves them (GET/POST /api/fuel; typing "gas" in Search)."""
import json, os, socket, sqlite3, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin")); sys.path.insert(0, str(ROOT / "maps"))
import hu_fuel
T = Path(tempfile.mkdtemp())

# Conroe-ish test area. A: Shell with pumps + building mapped (one station), sells diesel.
# B: unnamed Exxon. C: Buc-ee's 3 km north on the route. D: 4 km off the route. E: far away (Austin).
ST = [(-95.4600, 30.3100, "Shell", "Shell", 0, "100 Main St Conroe"),
      (-95.46025, 30.31010, "", "Shell", 1, ""),                          # same station, 25 m away (the building)
      (-95.4500, 30.3100, "", "Exxon", 0, ""),
      (-95.4600, 30.3400, "Buc-ee's", "Buc-ee's", 1, "I-45"),
      (-95.4180, 30.3250, "Valero", "Valero", -1, ""),
      (-97.7431, 30.2672, "Austin Gas", "", 0, "")]

def new_db(path, with_table=True):
    con = sqlite3.connect(path)
    con.execute("CREATE VIRTUAL TABLE places USING fts5(name, kind UNINDEXED, detail, lon UNINDEXED, lat UNINDEXED)")
    con.execute("INSERT INTO places VALUES ('Main St', 'street', '', -95.46, 30.31)")
    for lon, lat, name, brand, diesel, detail in ST:
        if name: con.execute("INSERT INTO places VALUES (?, 'fuel', ?, ?, ?)", (name, detail, lon, lat))
    if with_table:
        con.execute("CREATE TABLE fuel(lon REAL, lat REAL, name TEXT, brand TEXT, diesel INTEGER, detail TEXT)")
        con.executemany("INSERT INTO fuel VALUES (?,?,?,?,?,?)", ST)
    con.commit(); con.close()

# 1) reading: the fuel table (one Shell, merged; unnamed Exxon kept by its brand), and older search data
db = T / "search.sqlite"; new_db(db)
pts = hu_fuel.read_stations(db)
assert len(pts) == 5, pts
shell = [p for p in pts if p[3] == "Shell"]; assert len(shell) == 1 and shell[0][2] == "Shell" and shell[0][4] == 1 and shell[0][5], shell
assert any(p[3] == "Exxon" and p[2] == "" for p in pts)
old = T / "old.sqlite"; new_db(old, with_table=False)
assert {p[2] for p in hu_fuel.read_stations(old)} == {"Shell", "Buc-ee's", "Valero", "Austin Gas"}
print("1) stations read (new data: every one with brand / diesel; older data: the named ones); each kept once")

# 2) the map view, nearest, along the route ahead
logs = []
F = hu_fuel.Fuel(db, T / "cache.json", log=logs.append); F.ensure(wait=True)
assert F.ready() and F.count() == 5 and (T / "cache.json").exists() and logs
v = F.in_view(-95.5, 30.28, -95.40, 30.36); names = {f["properties"]["name"] for f in v["features"]}
assert names == {"Shell", "Exxon", "Buc-ee's", "Valero"}, names
assert F.in_view(-110, 20, -80, 40)["features"] == [], "zoomed far out: nothing (the icons only show from zoom 11)"
n = F.nearest(-95.4598, 30.3099, 4); assert [x["name"] for x in n][:2] == ["Shell", "Exxon"] and n[0]["dist"] < 50, n
nd = F.nearest(-95.4501, 30.3100, 4, diesel_first=True); assert nd[0]["diesel"] == 1, nd          # diesel truck: Shell before the closer Exxon
route = [[-95.4600, 30.3000], [-95.4600, 30.3200], [-95.4600, 30.3600]]                            # north on Main / I-45
a = F.along(route)
assert [x["name"] for x in a] == ["Shell", "Exxon", "Buc-ee's"], a                                 # in the order they come up; Valero 4 km off: no
assert a[0]["off"] < 50 and 900 < a[1]["off"] < 1000 and abs(a[2]["ahead"] - 4450) < 60, a
assert F.along(route[1:])[0]["name"] == "Buc-ee's", "what's behind the car isn't listed"
print("2) in the view, nearest (diesel first for a diesel truck), along the road ahead in order")

# 3) the next start: from the remembered list, no reading of the search data
logs.clear(); F2 = hu_fuel.Fuel(db, T / "cache.json", log=logs.append); F2.ensure(wait=True)
assert F2.count() == 5 and not logs, logs
time.sleep(1.1); con = sqlite3.connect(db); con.execute("INSERT INTO fuel VALUES (-95.47, 30.30, 'New', '', 0, '')"); con.commit(); con.close()
F2.ensure(wait=True); assert F2.count() == 6, "new search data: worked out again"
print("3) remembered between starts; worked out again for new search data")

# 4) the map builder writes the fuel table (osmium)
try:
    import osmium  # noqa
    osm = T / "t.osm"
    osm.write_text("""<?xml version='1.0' encoding='UTF-8'?><osm version="0.6">
<node id="1" lat="30.31" lon="-95.46" version="1"><tag k="amenity" v="fuel"/><tag k="brand" v="Shell"/><tag k="fuel:diesel" v="yes"/></node>
<node id="2" lat="30.32" lon="-95.46" version="1"><tag k="amenity" v="fuel"/><tag k="name" v="Joe's Gas"/><tag k="fuel:diesel" v="no"/></node>
<node id="3" lat="30.33" lon="-95.46" version="1"><tag k="amenity" v="cafe"/><tag k="name" v="Cafe"/></node>
</osm>""")
    import importlib.util
    spec = importlib.util.spec_from_file_location("br", ROOT / "maps/build-routing.py"); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
    out = T / "built"; out.mkdir()
    br.build_search([osm], out, addresses=False, work=T)
    con = sqlite3.connect(out / "search.sqlite")
    rows = sorted(con.execute("SELECT name, brand, diesel FROM fuel").fetchall())
    shell_found = con.execute("SELECT kind FROM places WHERE places MATCH 'shell'").fetchall()
    con.close()
    assert rows == [("", "Shell", 1), ("Joe's Gas", "", -1)], rows
    assert shell_found == [("fuel",)], "an unnamed Shell is found by typing Shell"
    print("4) the map builder keeps every gas station, named or not, with brand and diesel")
except ImportError:
    print("4) (osmium not installed here: builder check skipped)")

# 5) the bridge: GET (map icons), POST (Places > Gas), "gas" in Search
data = T / "data"; (data / "search").mkdir(parents=True); new_db(data / "search" / "search.sqlite")
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\nDIAG_AUTO=0\nFUEL=diesel\n")
for d in ("user", "usage", "run"): (T / d).mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T), "XDG_RUNTIME_DIR": str(T / "run")}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())
try:
    for _ in range(80):
        try: r = call("/api/fuel?bbox=-95.5,30.28,-95.40,30.36");
        except OSError: time.sleep(0.25); continue
        if r.get("ready") and r["features"]: break
        time.sleep(0.25)
    assert len(r["features"]) == 4 and all(f["geometry"]["type"] == "Point" for f in r["features"]), r
    r = call("/api/fuel", {"near": [-95.4501, 30.31], "route": route})
    assert r["fuel"] == "diesel" and r["near"][0]["diesel"] == 1 and [x["name"] for x in r["along"]] == ["Shell", "Buc-ee's", "Exxon"], r   # diesel ones first
    res = call("/api/search?q=gas&near=-95.4598,30.3099")
    assert res and all(x["kind"] == "fuel" for x in res) and res[0]["name"] in ("Shell", "Buc-ee's"), res
    assert call("/api/status")["fuel"] == "diesel"
    assert (T / "user" / "fuel-stations.json").exists()
finally:
    b.terminate(); b.wait(5)
print("5) bridge: icons for the map's view, the Gas list (along the route / nearest), \"gas\" in Search")

# 6) every skin has its own gas-icon colours, and the map adds the icons with each skin
themes = json.loads((ROOT / "apps/styles/themes.json").read_text())
src = {t["id"]: t for t in json.loads((ROOT / "tools/themes.json").read_text())["themes"]}
for t in themes:
    f = t.get("fuel"); assert f and all(k in f for k in ("bg", "fg", "ring", "badge", "badgeText")), t["id"]
    assert f == src[t["id"]]["fuel"], "apps/styles/themes.json matches tools/themes.json"
    assert f["bg"].lower() != f["fg"].lower() and f["fg"].lower() != t["route"]["color"].lower(), t["id"]   # stands out from the route line
nav = (ROOT / "apps/nav/nav.js").read_text()
assert "addFuelLayer(); addRouteLayers(); refreshRoute();" in nav, "the icons come back after a skin change"
assert "map.on('click', 'hu-fuel'" in nav and "fuelColors(state.theme)" in nav
print("6) each skin colours its own gas icons; they're redrawn with every skin change")
print("fuel: all tests passed")
