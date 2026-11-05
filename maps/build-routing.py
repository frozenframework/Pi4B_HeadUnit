#!/usr/bin/env python3
"""
Build the offline ROUTING + SEARCH data for the head unit on a computer (Mac, Windows or Linux).
The Pi downloads the map *picture* data (basemap) itself; this script makes the heavier parts:

  valhalla_tiles.tar   turn-by-turn routing graph (Valhalla)
  search.sqlite        offline search: places, POIs, streets (and optionally addresses)

Output goes into a folder named HEADUNIT_MAPS. Copy that folder onto any USB stick and plug it
into the head unit: it imports itself. Or copy it over the network with scp (see README).

Setup once:   python3 -m venv hu-maps && source hu-maps/bin/activate   (Windows: hu-maps\\Scripts\\activate)
              pip install pyvalhalla==3.9.1 osmium
Run:          python3 build-routing.py --region texas-plus
              python3 build-routing.py --region us                    (needs ~16 GB RAM, a few hours)
  Street addresses ("1234 N Main St, Conroe") are included; --no-addresses leaves them out (smaller).
"""
import argparse, hashlib, json, os, shutil, sqlite3, subprocess, sys, tarfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGIONS = json.loads((HERE / "regions.json").read_text())
GEOFABRIK = "https://download.geofabrik.de/"

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def download(url, dest, tries=30):
    """Resumable download with a progress line, then MD5 check against Geofabrik's .md5 file.
    A dropped or broken connection (common on long downloads: resets, SSL "record layer failure",
    timeouts) is retried by itself, continuing from the last byte; running the script again later
    also continues. If Geofabrik publishes a newer file meanwhile (If-Range), it starts that one over."""
    dest = Path(dest)
    md5_url = url + ".md5"
    try:
        expected = urllib.request.urlopen(md5_url, timeout=60).read().decode().split()[0]
    except Exception:
        expected = None
    if dest.exists() and expected and md5(dest) == expected:
        log("up to date:", dest.name); return dest
    part = dest.with_suffix(dest.suffix + ".part")
    tag_file = part.with_suffix(part.suffix + ".etag")
    for attempt in range(1, tries + 1):
        have = part.stat().st_size if part.exists() else 0
        hdr = {}
        if have:
            hdr["Range"] = f"bytes={have}-"
            if tag_file.exists(): hdr["If-Range"] = tag_file.read_text().strip()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=120) as r:
                if have and r.status == 200:                       # file changed on the server (or no resume): start over
                    log(f"{dest.name} changed on the server; starting it over"); have = 0
                etag = r.headers.get("ETag") or r.headers.get("Last-Modified")
                if etag: tag_file.write_text(etag)
                total = have + int(r.headers.get("Content-Length", 0))
                got = have; last = 0
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk: break
                        f.write(chunk); got += len(chunk)
                        if time.time() - last > 2:
                            print(f"\r  {dest.name}: {got/2**20:,.0f} / {total/2**20:,.0f} MB", end="", flush=True); last = time.time()
            if total and got < total:
                raise IOError(f"connection closed at {got/2**20:,.0f} MB")
            break
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:                           # already have all of it
                break
            if e.code < 500 and e.code != 429: raise
            err = f"HTTP {e.code}"
        except Exception as e:                                   # reset, SSL error, timeout, short read...
            err = f"{type(e).__name__}: {e}"
        got_now = part.stat().st_size if part.exists() else 0
        wait = min(60, 5 * attempt)
        print(); log(f"{dest.name}: connection dropped at {got_now/2**20:,.0f} MB ({err}); continuing in {wait} s (try {attempt} of {tries})")
        time.sleep(wait)
    else:
        sys.exit(f"{dest.name} kept failing. Run the same command again: it continues where it stopped.")
    print()
    if expected and md5(part) != expected:
        part.unlink(); tag_file.unlink(missing_ok=True)
        sys.exit(f"Checksum mismatch for {dest.name}; run again to re-download.")
    tag_file.unlink(missing_ok=True)
    part.replace(dest); return dest

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""): h.update(b)
    return h.hexdigest()

def valhalla_bin(name):
    import valhalla
    exe = Path(valhalla.__file__).parent / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if not exe.exists(): sys.exit(f"Can't find {exe}. Install with: pip install pyvalhalla==3.9.1")
    return str(exe)

def valhalla_env():
    """On Windows the Valhalla programs need the DLLs pyvalhalla ships in site-packages/pyvalhalla.libs on
    PATH (otherwise they exit at once with 3221225781 = 0xC0000135, 'DLL not found'); pyvalhalla's own
    launcher does the same."""
    if os.name != "nt": return None
    import valhalla
    libs = Path(valhalla.__file__).resolve().parent.parent / "pyvalhalla.libs"
    return {**os.environ, "PATH": str(libs) + os.pathsep + os.environ.get("PATH", "")}

def build_routing(pbfs, work, out):
    from valhalla import get_config
    tiles = work / "valhalla_tiles"
    if tiles.exists(): shutil.rmtree(tiles)
    tiles.mkdir(parents=True)
    cfg = get_config(tile_extract="", tile_dir=str(tiles), verbose=True)
    cfg["mjolnir"]["concurrency"] = os.cpu_count() or 4
    cfg["mjolnir"]["timezone"] = ""; cfg["mjolnir"]["admin"] = ""   # skip optional admin/timezone DBs
    cfg_path = work / "valhalla.json"; cfg_path.write_text(json.dumps(cfg, indent=1))
    log("building routing graph (this is the slow part)")
    subprocess.run([valhalla_bin("valhalla_build_tiles"), "-c", str(cfg_path), *map(str, pbfs)], check=True, env=valhalla_env())
    tar_path = out / "valhalla_tiles.tar"
    log("packing", tar_path.name)
    part = tar_path.with_suffix(".tar.part")
    with tarfile.open(part, "w") as t:
        for f in sorted(tiles.rglob("*.gph")):
            t.add(f, arcname=str(f.relative_to(tiles)).replace(os.sep, "/"))
    os.replace(part, tar_path)                    # only a completely packed file gets the real name
    return tar_path

def routing_already_built(pbfs, work, out):
    """The routing graph from a run that stopped later (e.g. in the search step) is reused instead of
    spending hours building it again: valhalla_tiles.tar is newer than the map data and holds every
    tile that was built."""
    tar_path, tiles = out / "valhalla_tiles.tar", work / "valhalla_tiles"
    if not tar_path.exists() or not tiles.exists(): return False
    if tar_path.stat().st_mtime < max(Path(p).stat().st_mtime for p in pbfs): return False
    try:
        with tarfile.open(tar_path) as t: n = sum(1 for m in t if m.isfile())
    except (tarfile.TarError, OSError):
        return False
    return n > 0 and n == sum(1 for _ in tiles.rglob("*.gph"))

BIG_PBF = 2 * 2**30      # above this much OpenStreetMap data, node positions go to a file on disk
KIND_KEYS = ("amenity", "shop", "tourism", "leisure", "aeroway", "railway", "office", "healthcare", "craft", "historic")
def build_search(pbfs, out, addresses, work=Path(".")):
    import osmium
    db_path = out / "search.sqlite"
    for f in (db_path, out / "search.sqlite-journal", out / "search.sqlite-wal", out / "search.sqlite-shm"):
        f.unlink(missing_ok=True)                    # incl. the leftovers of a run that stopped part way
    con = sqlite3.connect(db_path)
    con.execute("CREATE VIRTUAL TABLE places USING fts5(name, kind UNINDEXED, detail, lon UNINDEXED, lat UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    # street addresses (house number lookups) in plain indexed tables: see bin/hu_addr.py
    sys.path.insert(0, str(HERE.parent / "bin"))
    import hu_addr
    con.executescript(hu_addr.SCHEMA)
    rows = []; addr_rows = []; street_rows = []; seen_streets = set(); seen_pts = set()
    def flush():
        con.executemany("INSERT INTO places VALUES (?,?,?,?,?)", rows); rows.clear()
        con.executemany("INSERT INTO addr VALUES (?,?,?,?,?,?,?,?)", addr_rows); addr_rows.clear()
        con.executemany("INSERT INTO street_pts VALUES (?,?,?,?,?)", street_rows); street_rows.clear()
    class H(osmium.SimpleHandler):
        def place(self, tags, lon, lat, is_way):
            name = tags.get("name")
            city = tags.get("addr:city", "")
            if name:
                kind = tags.get("place") and "place:" + tags["place"]
                if not kind:
                    for k in KIND_KEYS:
                        if k in tags: kind = tags[k].replace("_", " "); break
                if kind:
                    rows.append((name, kind, " ".join(x for x in (tags.get("addr:housenumber", ""), tags.get("addr:street", ""), city) if x), lon, lat))
            if addresses and "addr:housenumber" in tags:
                a = hu_addr.addr_row(tags, lon, lat)
                if a: addr_rows.append(a)
            if len(rows) + len(addr_rows) > 50000: flush()
        def node(self, n):
            if n.tags and n.location.valid():
                self.place(n.tags, n.location.lon, n.location.lat, False)
        def way(self, w):
            t = w.tags
            try:
                nodes = w.nodes; mid = nodes[len(nodes) // 2]
                if not mid.location.valid(): return
                lon, lat = mid.location.lon, mid.location.lat
            except Exception:
                return
            if "highway" in t and "name" in t and t["highway"] not in ("footway", "path", "service", "cycleway", "steps"):
                key = (t["name"], round(lon, 2), round(lat, 2))       # one hit per street per ~1 km cell
                if key not in seen_streets:
                    seen_streets.add(key); rows.append((t["name"], "street", t.get("ref", ""), lon, lat))
                if addresses:                                         # points along the street, ~300 m apart
                    for i in range(0, len(nodes), max(1, len(nodes) // 4)):
                        nd = nodes[i]
                        if not nd.location.valid(): continue
                        pk = (t["name"], round(nd.location.lon * 300), round(nd.location.lat * 300))
                        if pk in seen_pts: continue
                        seen_pts.add(pk); street_rows.append(hu_addr.street_row(t["name"], "", nd.location.lon, nd.location.lat))
                    if len(street_rows) > 50000: flush()
            else:
                self.place(t, lon, lat, True)
    # Node positions: kept in memory for a state or two; for big regions (the whole US is ~10 GB of
    # OpenStreetMap data) in a file on disk instead, so it runs on a 16 GB computer.
    big = sum(Path(p).stat().st_size for p in pbfs) > BIG_PBF
    def remove(f):                                   # Windows may still hold the file for a moment: not fatal
        try: f.unlink(missing_ok=True)
        except OSError: log(f"(couldn't delete {f.name} yet; it's only a work file, delete hu-maps-work later)")
    for old in work.glob("node-locations*.idx"): remove(old)
    for i, p in enumerate(pbfs):
        idx_file = work / f"node-locations-{i}.idx"
        idx = f"sparse_file_array,{idx_file}" if big else "flex_mem"
        log("indexing", Path(p).name, "(node positions on disk)" if big else "")
        h = H(); h.apply_file(str(p), locations=True, idx=idx); del h
        flush(); con.commit()                        # what's indexed is saved before anything else can fail
        if big:
            import gc; gc.collect(); remove(idx_file)
    flush(); con.execute("INSERT INTO places(places) VALUES('optimize')"); con.commit()
    log("indexing street addresses")
    con.executescript(hu_addr.INDEXES)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('format', '2')"); con.commit()
    n = con.execute("SELECT count(*) FROM places").fetchone()[0]
    na = con.execute("SELECT count(*) FROM addr").fetchone()[0]; con.execute("ANALYZE"); con.close()
    log(f"search index: {n:,} places and streets, {na:,} street addresses")
    return db_path

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="texas-plus", choices=sorted(k for k in REGIONS if not k.startswith("_")))
    ap.add_argument("--pbf", nargs="*", help="use local .osm.pbf files instead of downloading")
    ap.add_argument("--out", default="HEADUNIT_MAPS")
    ap.add_argument("--work", default="hu-maps-work")
    ap.add_argument("--no-addresses", action="store_true", help="skip street addresses in search (much smaller)")
    ap.add_argument("--skip-routing", action="store_true"); ap.add_argument("--skip-search", action="store_true")
    a = ap.parse_args()
    reg = REGIONS[a.region]
    out = Path(a.out); work = Path(a.work); out.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    if a.pbf:
        pbfs = [Path(p) for p in a.pbf]
    else:
        pbfs = [download(f"{GEOFABRIK}{g}-latest.osm.pbf", work / (g.split("/")[-1] + "-latest.osm.pbf")) for g in reg["geofabrik"]]
    files = {}
    if not a.skip_routing:
        if routing_already_built(pbfs, work, out):
            log("routing graph already built from this map data; reusing it (delete HEADUNIT_MAPS/valhalla_tiles.tar to rebuild)")
            files["routing"] = "valhalla_tiles.tar"
        else:
            files["routing"] = build_routing(pbfs, work, out).name
    elif (out / "valhalla_tiles.tar").exists():
        files["routing"] = "valhalla_tiles.tar"
    if not a.skip_search:
        files["search"] = build_search(pbfs, out, addresses=not a.no_addresses, work=work).name
    manifest = {"region": a.region, "name": reg["name"], "bbox": reg["bbox"], "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sources": [p.name for p in pbfs], "files": files, "format": 1}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    size = sum(f.stat().st_size for f in out.iterdir()) / 2**30
    log(f"done: {out.resolve()}  ({size:.2f} GB). Send it to the head unit with tools/push-maps.py (or a USB stick).")

if __name__ == "__main__":
    main()
