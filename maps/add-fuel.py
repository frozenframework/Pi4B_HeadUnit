#!/usr/bin/env python3
"""
Add every gas station - and every license-plate camera (Flock and others, for the "avoid Flock cameras"
route option) - to existing offline search data (HEADUNIT_MAPS/search.sqlite), without rebuilding
search: the map's gas icons and Places > Gas then have all stations, named or not, with their brand
and whether they sell diesel. Nothing else in the search data changes (addresses, 911 house numbers
you added, ...), and running it again just replaces them. (The head unit can also download the
cameras itself over Wi-Fi: Settings > Maps > Plate cameras.)

    source hu-maps/bin/activate          (Windows: hu-maps\\Scripts\\activate)
    pip install osmium
    python3 maps/add-fuel.py HEADUNIT_MAPS
    python3 tools/push-maps.py HEADUNIT_MAPS --only search --host miata-headunit.local

It reads the OpenStreetMap file the search data was built from (hu-maps-work/<name>.osm.pbf, from
HEADUNIT_MAPS/manifest.json; downloaded again if it's gone). Two quick passes over it (about 5-15
minutes each for the whole US): the stations, then the corners of the ones mapped as an area.
"""
import argparse, importlib.util, json, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bin"))
import hu_cameras

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def need_osmium():
    try:
        import osmium  # noqa: F401
        return osmium
    except ImportError:
        sys.exit("This needs the osmium Python package. Run it inside the map-building environment:\n"
                 "    source hu-maps/bin/activate          (Windows: hu-maps\\Scripts\\activate)\n"
                 "    pip install osmium\n"
                 "then run this again.")

def station(tags):
    """(name, brand, diesel, detail) of an amenity=fuel object."""
    brand = tags.get("brand") or tags.get("operator") or ""
    dz = tags.get("fuel:diesel", "")
    diesel = 1 if dz in ("yes", "only") else -1 if dz == "no" else 0
    detail = " ".join(x for x in (tags.get("addr:housenumber", ""), tags.get("addr:street", ""), tags.get("addr:city", "")) if x)
    return tags.get("name", ""), brand, diesel, detail

def read_fuel(pbfs, cameras=None):
    """[(lon, lat, name, brand, diesel, detail)] for every amenity=fuel node and way; with a list as
    `cameras`, every license-plate camera goes into it too: (lon, lat, flock)."""
    osmium = need_osmium()
    has_filters = hasattr(osmium, "filter") and hasattr(osmium.filter, "KeyFilter")
    out, ways = [], []                                   # ways: (info, [node ids])
    class Pass1(osmium.SimpleHandler):
        def node(self, n):
            if n.tags.get("amenity") == "fuel" and n.location.valid():
                out.append((n.location.lon, n.location.lat) + station(n.tags))
            if cameras is not None and n.tags.get("surveillance:type") == "ALPR" and n.location.valid():
                cameras.append((n.location.lon, n.location.lat, 1 if hu_cameras.is_flock(n.tags) else 0))
        def way(self, w):
            if w.tags.get("amenity") == "fuel":
                refs = list(dict.fromkeys(nd.ref for nd in w.nodes))          # a closed area repeats its first corner
                if len(refs) > 40: refs = refs[:: len(refs) // 40 + 1]
                ways.append((station(w.tags), refs))
    for p in pbfs:
        log("finding gas stations in", Path(p).name)
        h = Pass1()
        if has_filters: h.apply_file(str(p), filters=[osmium.filter.KeyFilter("amenity", "surveillance:type")])
        else: h.apply_file(str(p))
    if ways:
        want = {r for _, refs in ways for r in refs}
        loc = {}
        class Pass2(osmium.SimpleHandler):
            def node(self, n):
                if n.id in want and n.location.valid(): loc[n.id] = (n.location.lon, n.location.lat)
        for p in pbfs:
            log(f"placing the {len(ways):,} stations mapped as an area")
            h = Pass2()
            if has_filters and hasattr(osmium.filter, "IdFilter"): h.apply_file(str(p), filters=[osmium.filter.IdFilter(want)])
            else: h.apply_file(str(p))
        for info, refs in ways:
            pts = [loc[r] for r in refs if r in loc]
            if pts:
                out.append((sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)) + info)
    return out

def add_to_db(db, rows, cameras=None):
    con = sqlite3.connect(db)
    try:
        if cameras is not None:
            con.execute("DROP TABLE IF EXISTS cameras")
            con.execute("CREATE TABLE cameras(lon REAL, lat REAL, flock INTEGER)")
            con.executemany("INSERT INTO cameras VALUES (?,?,?)", cameras)
        con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("DROP TABLE IF EXISTS fuel")
        con.execute("CREATE TABLE fuel(lon REAL, lat REAL, name TEXT, brand TEXT, diesel INTEGER, detail TEXT)")
        con.executemany("INSERT INTO fuel VALUES (?,?,?,?,?,?)", rows)
        # unnamed stations findable by their brand in Search ("Shell"): rows this script added before are replaced
        old = con.execute("SELECT value FROM meta WHERE key = 'fuel_places'").fetchone()
        if old:
            a, b = (int(x) for x in old[0].split(","))
            con.execute("DELETE FROM places WHERE rowid BETWEEN ? AND ?", (a, b))
        first = (con.execute("SELECT max(rowid) FROM places").fetchone()[0] or 0) + 1
        brand_only = [(brand, "fuel", detail, lon, lat) for lon, lat, name, brand, diesel, detail in rows if not name and brand]
        con.executemany("INSERT INTO places(name, kind, detail, lon, lat) VALUES (?,?,?,?,?)", brand_only)
        last = con.execute("SELECT max(rowid) FROM places").fetchone()[0] or 0
        if brand_only: con.execute("INSERT OR REPLACE INTO meta VALUES ('fuel_places', ?)", (f"{first},{last}",))
        else: con.execute("DELETE FROM meta WHERE key = 'fuel_places'")
        con.commit()
        return len(brand_only)
    finally:
        con.close()

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps", nargs="?", default="HEADUNIT_MAPS", help="the HEADUNIT_MAPS folder")
    ap.add_argument("--pbf", nargs="*", help="OpenStreetMap files to read (default: the ones the search data was built from)")
    ap.add_argument("--work", default="hu-maps-work")
    a = ap.parse_args()
    need_osmium()                                        # before any long download
    out = Path(a.maps); db = out / "search.sqlite"
    if not db.exists(): sys.exit(f"No search data in {out} (build it with maps/build-routing.py first).")
    man = json.loads((out / "manifest.json").read_text()) if (out / "manifest.json").exists() else {}
    if a.pbf:
        pbfs = [Path(p) for p in a.pbf]
    else:
        spec = importlib.util.spec_from_file_location("build_routing", HERE / "build-routing.py")
        br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
        region = br.REGIONS.get(man.get("region", ""), {})
        if not region.get("geofabrik"): sys.exit("Couldn't tell which region the search data is for: pass --pbf <file.osm.pbf>.")
        work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
        pbfs = [br.download(f"{br.GEOFABRIK}{g}-latest.osm.pbf", work / (g.split("/")[-1] + "-latest.osm.pbf")) for g in region["geofabrik"]]
    cams = []
    rows = read_fuel(pbfs, cams)
    named = sum(1 for r in rows if r[2]); diesel = sum(1 for r in rows if r[4] == 1)
    extra = add_to_db(db, rows, cams)
    log(f"{len(cams):,} license-plate cameras added ({sum(c[2] for c in cams):,} Flock)")
    log(f"{len(rows):,} gas stations added ({named:,} named, {diesel:,} known to sell diesel; "
        f"{extra:,} unnamed ones now found by their brand in Search)")
    log(f"send it to the head unit: python3 tools/push-maps.py {out} --only search --host miata-headunit.local")

if __name__ == "__main__":
    main()
