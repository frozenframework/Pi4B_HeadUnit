#!/usr/bin/env python3
"""
Add house numbers that OpenStreetMap doesn't have to the head unit's offline search.

OpenStreetMap has house numbers for many towns but few country roads. Texas publishes its 911 address
points (every house with a 911 address, from the county 911 districts) through the Texas Geographic
Information Office (TxGIO "StratMap Address Points"), free and public. This adds them to an existing
HEADUNIT_MAPS/search.sqlite; nothing else is rebuilt.

    python3 add-addresses.py HEADUNIT_MAPS --county Montgomery
    python3 add-addresses.py HEADUNIT_MAPS --county Montgomery --county Harris --county Walker
    python3 add-addresses.py HEADUNIT_MAPS --all-texas            (~14 million points, a few hours)
    python3 add-addresses.py HEADUNIT_MAPS my-addresses.geojson    (a file you downloaded: GeoJSON or CSV
                                                                    with NENA / NAD / OpenAddresses columns)
Then send just the search data to the head unit:
    python3 ../tools/push-maps.py HEADUNIT_MAPS --only search --host miata-headunit.local

Downloads are kept in hu-maps-work/addresses and continue where they stopped if the connection drops
or you stop it; running it again for the same county only fetches what's new. Houses already in the
search data (same number and street within 60 m) aren't added twice.
"""
import argparse, csv, gzip, io, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bin"))
import hu_addr

TXGIO = ("https://feature.geographic.texas.gov/arcgis/rest/services/Address_Points/"
         "stratmap_address_points_48_most_recent/MapServer/0/query")
FIELDS = "objectid,add_number,addnum_suf,st_premod,st_predir,st_pretyp,st_presep,st_name,st_postyp,st_posdir,st_posmod,full_addr,post_comm,post_code,county"
PAGE = 2000

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def get_json(url, params, tries=12):
    u = url + "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Pi4B-HeadUnit map builder"}), timeout=120) as r:
                d = json.loads(r.read())
            if isinstance(d, dict) and "error" in d: raise RuntimeError(d["error"])
            return d
        except Exception as e:                       # resets, SSL hiccups, a busy server: wait and retry
            if i == tries - 1: raise
            wait = min(5 * (i + 1), 60); log(f"   (server or connection problem: {e}; retrying in {wait} s)"); time.sleep(wait)

def county_where(county):
    return "1=1" if county == "*" else "county='" + county.replace("'", "''") + "'"

def fetch_county(county, work, url=TXGIO):
    """Download one county's address points (or '*' for all of Texas) into a GeoJSON-lines file, page by
    page in objectid order; the last objectid is kept so a stopped download continues."""
    work.mkdir(parents=True, exist_ok=True)
    slug = "all-texas" if county == "*" else re.sub(r"[^a-z0-9]+", "-", county.lower())
    out, state = work / f"txgio-{slug}.geojsonl", work / f"txgio-{slug}.last"
    where = county_where(county)
    total = get_json(url, {"where": where, "returnCountOnly": "true", "f": "json"}).get("count", 0)
    if not total:
        sys.exit(f"No Texas 911 address points for county '{county}'. Check the spelling (e.g. Montgomery, Fort Bend).")
    last = int(state.read_text()) if state.exists() and out.exists() else -1
    if last < 0: out.write_text("")
    have = sum(1 for _ in open(out, encoding="utf-8"))
    log(f"{'all of Texas' if county == '*' else county + ' County'}: {total:,} address points" + (f" ({have:,} already downloaded)" if have else ""))
    t0, got = time.time(), 0
    while True:
        q = {"where": f"({where}) AND objectid > {last}", "outFields": FIELDS, "orderByFields": "objectid ASC",
             "resultRecordCount": PAGE, "returnGeometry": "true", "outSR": "4326", "f": "geojson"}
        d = get_json(url, q)
        feats = d.get("features") or []
        if not feats: break
        with open(out, "a", encoding="utf-8") as f:
            for ft in feats: f.write(json.dumps(ft, separators=(",", ":")) + "\n")
        last = max(int(ft.get("id") or ft["properties"]["objectid"]) for ft in feats)
        state.write_text(str(last)); have += len(feats); got += len(feats)
        rate = got / max(time.time() - t0, 1)
        left = max(total - have, 0) / rate if rate else 0
        print(f"\r   {have:,} of {total:,} ({min(have / total, 1):.0%}), about {int(left // 60)} min left   ", end="", flush=True)
        if len(feats) < PAGE and not d.get("exceededTransferLimit"): break
    print()
    return out

# ------------------------------------------------------------------ reading address records
def pick(p, *names):
    for n in names:
        v = p.get(n)
        if v not in (None, ""): return str(v).strip()
    return ""

def to_tags(props):
    """A 911 / NENA / NAD / OpenAddresses record -> OpenStreetMap-style address tags (hu_addr.addr_row)."""
    p = {str(k).lower(): v for k, v in props.items()}
    num = pick(p, "add_number", "addnum", "number", "house_num", "housenumber", "addr_num", "st_num")
    street = " ".join(pick(p, k) for k in ("st_premod", "st_predir", "st_pretyp", "st_presep", "st_name", "st_postyp", "st_posdir", "st_posmod") if pick(p, k))
    if not p.get("st_name"):
        street = pick(p, "street", "streetname", "stnam_full", "fullname", "street_name", "st_full")
    full = pick(p, "full_addr", "fulladdr", "full_address", "address", "situs", "site_addr")
    if (not num or not street) and full:             # "563 Main St" in one column
        m = re.match(r"^\s*(\d+\s*[A-Za-z]?)\s+(.+)$", full)
        if m: num, street = num or m.group(1), street or m.group(2)
    if not num or not street: return None
    return {"addr:housenumber": num + pick(p, "addnum_suf"), "addr:street": street,
            "addr:city": pick(p, "post_comm", "post_city", "city", "municipality", "postal_city", "msag_comm"),
            "addr:postcode": pick(p, "post_code", "zip_code", "zip", "postcode", "zipcode", "zip5")}

def read_records(path):
    """(tags, lon, lat) from GeoJSON (one feature per line, or a FeatureCollection), CSV, .gz or .zip."""
    path = Path(path)
    def opener():
        if path.suffix == ".zip":
            z = zipfile.ZipFile(path)
            for n in z.namelist():
                if n.lower().endswith((".csv", ".geojson", ".geojsonl", ".json")):
                    yield n, io.TextIOWrapper(z.open(n), encoding="utf-8-sig", errors="replace")
        elif path.suffix == ".gz":
            yield path.stem, gzip.open(path, "rt", encoding="utf-8-sig", errors="replace")
        else:
            yield path.name, open(path, encoding="utf-8-sig", errors="replace")
    for name, f in opener():
        with f:
            if name.lower().endswith(".csv"):
                for row in csv.DictReader(f):
                    p = {k.lower(): v for k, v in row.items() if k}
                    try: lon, lat = float(pick(p, "longitude", "lon", "x", "long")), float(pick(p, "latitude", "lat", "y"))
                    except ValueError: continue
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        sys.exit(f"{name}: the coordinates aren't longitude / latitude (a state-plane projection?). Download it as GeoJSON instead.")
                    t = to_tags(p)
                    if t: yield t, lon, lat
                continue
            first = f.readline()
            while first and not first.strip(): first = f.readline()
            if re.match(r'\s*\{\s*"type"\s*:\s*"Feature"\s*,', first):     # one feature per line (our downloads)
                feats = (json.loads(ln.strip().rstrip(",")) for ln in ([first] + list(f)) if ln.strip().startswith("{"))
            else:                                    # a whole FeatureCollection (e.g. from an ArcGIS Hub download)
                feats = json.loads(first + f.read()).get("features", [])
            for ft in feats:
                g = ft.get("geometry") or {}
                if g.get("type") != "Point": continue
                t = to_tags(ft.get("properties") or {})
                if t: yield t, g["coordinates"][0], g["coordinates"][1]

# ------------------------------------------------------------------ adding to search.sqlite
def add_to_db(db, files):
    con = sqlite3.connect(db)
    con.executescript(hu_addr.SCHEMA); con.executescript(hu_addr.INDEXES)
    before = con.execute("SELECT count(*) FROM addr").fetchone()[0]
    added = dup = bad = 0; batch = []; mine = {}           # (core, num) -> positions added this run
    for path in files:
        log("adding", Path(path).name)
        for tags, lon, lat in read_records(path):
            r = hu_addr.addr_row(tags, lon, lat)
            if not r: bad += 1; continue
            same = con.execute("SELECT lon, lat FROM addr WHERE core=? AND num=? LIMIT 50", (r[3], r[0])).fetchall()
            same += mine.get((r[3], r[0]), [])
            if any(hu_addr._dist((lon, lat), s) < 60 for s in same):
                dup += 1; continue
            batch.append(r); added += 1; mine.setdefault((r[3], r[0]), []).append((lon, lat))
            if len(batch) >= 5000:
                con.executemany("INSERT INTO addr VALUES (?,?,?,?,?,?,?,?)", batch); batch.clear()
                print(f"\r   {added:,} added, {dup:,} already there", end="", flush=True)
    con.executemany("INSERT INTO addr VALUES (?,?,?,?,?,?,?,?)", batch); print()
    log("updating ZIP code centres")
    con.execute("DELETE FROM areas WHERE kind='zip'")
    con.execute("INSERT INTO areas SELECT 'zip', zip, avg(lon), avg(lat), count(*) FROM addr WHERE zip != '' GROUP BY zip")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('format', '3')")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('extra_addresses', ?)", (time.strftime("%Y-%m-%d"),))
    con.commit(); con.execute("ANALYZE"); con.commit(); con.close()
    log(f"done: {added:,} house numbers added ({before:,} -> {before + added:,}); {dup:,} were already there, {bad:,} unusable")
    return added

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the HEADUNIT_MAPS folder from build-routing.py")
    ap.add_argument("files", nargs="*", help="address files you downloaded (GeoJSON / CSV / .gz / .zip)")
    ap.add_argument("--county", action="append", default=[], help="a Texas county to add (repeat for more)")
    ap.add_argument("--all-texas", action="store_true", help="every Texas county (~14 million points)")
    ap.add_argument("--work", default="hu-maps-work", help="where downloads are kept (default: hu-maps-work)")
    a = ap.parse_args()
    db = Path(a.folder) / "search.sqlite"
    if not db.exists(): sys.exit(f"No {db}: run build-routing.py first (or give the path to HEADUNIT_MAPS).")
    if not (a.files or a.county or a.all_texas): sys.exit("Say which addresses to add: --county Montgomery, --all-texas, or a file.")
    work = Path(a.work) / "addresses"
    files = [fetch_county(c.strip().title(), work) for c in (["*"] if a.all_texas else a.county)] + [Path(f) for f in a.files]
    add_to_db(db, files)
    man = Path(a.folder) / "manifest.json"
    if man.exists():                                   # the head unit shows when the search data was made
        m = json.loads(man.read_text()); m["addresses_added"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        src = set(m.get("address_sources", [])) | ({"TxGIO 911 address points"} if a.county or a.all_texas else set()) | {Path(f).name for f in a.files}
        m["address_sources"] = sorted(src)
        man.write_text(json.dumps(m, indent=1))
    print("Next: python3 ../tools/push-maps.py", a.folder, "--only search --host miata-headunit.local")

if __name__ == "__main__":
    main()
