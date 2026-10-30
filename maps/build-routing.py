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
              python3 build-routing.py --region us --no-addresses     (needs ~16 GB RAM, a few hours)
"""
import argparse, hashlib, json, os, shutil, sqlite3, subprocess, sys, tarfile, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGIONS = json.loads((HERE / "regions.json").read_text())
GEOFABRIK = "https://download.geofabrik.de/"

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def download(url, dest):
    """Resumable download with a progress line, then MD5 check against Geofabrik's .md5 file."""
    dest = Path(dest)
    md5_url = url + ".md5"
    try:
        expected = urllib.request.urlopen(md5_url, timeout=60).read().decode().split()[0]
    except Exception:
        expected = None
    if dest.exists() and expected and md5(dest) == expected:
        log("up to date:", dest.name); return dest
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab" if have else "wb") as f:
        total = have + int(r.headers.get("Content-Length", 0))
        got = have; last = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk: break
            f.write(chunk); got += len(chunk)
            if time.time() - last > 2:
                print(f"\r  {dest.name}: {got/2**20:,.0f} / {total/2**20:,.0f} MB", end="", flush=True); last = time.time()
    print()
    if expected and md5(part) != expected:
        part.unlink(); sys.exit(f"Checksum mismatch for {dest.name}; run again to re-download.")
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
    subprocess.run([valhalla_bin("valhalla_build_tiles"), "-c", str(cfg_path), *map(str, pbfs)], check=True)
    tar_path = out / "valhalla_tiles.tar"
    log("packing", tar_path.name)
    with tarfile.open(tar_path, "w") as t:
        for f in sorted(tiles.rglob("*.gph")):
            t.add(f, arcname=str(f.relative_to(tiles)).replace(os.sep, "/"))
    return tar_path

KIND_KEYS = ("amenity", "shop", "tourism", "leisure", "aeroway", "railway", "office", "healthcare", "craft", "historic")
def build_search(pbfs, out, addresses):
    import osmium
    db_path = out / "search.sqlite"
    if db_path.exists(): db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute("CREATE VIRTUAL TABLE places USING fts5(name, kind UNINDEXED, detail, lon UNINDEXED, lat UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    rows = []; seen_streets = set()
    def flush():
        con.executemany("INSERT INTO places VALUES (?,?,?,?,?)", rows); rows.clear()
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
            if addresses and "addr:housenumber" in tags and "addr:street" in tags and not name:
                rows.append((f'{tags["addr:housenumber"]} {tags["addr:street"]}', "address", city, lon, lat))
            if len(rows) > 50000: flush()
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
            else:
                self.place(t, lon, lat, True)
    for p in pbfs:
        log("indexing", Path(p).name)
        H().apply_file(str(p), locations=True, idx="flex_mem")
    flush(); con.execute("INSERT INTO places(places) VALUES('optimize')"); con.commit()
    n = con.execute("SELECT count(*) FROM places").fetchone()[0]; con.close()
    log(f"search index: {n:,} entries")
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
        files["routing"] = build_routing(pbfs, work, out).name
    if not a.skip_search:
        files["search"] = build_search(pbfs, out, addresses=not a.no_addresses).name
    manifest = {"region": a.region, "name": reg["name"], "bbox": reg["bbox"], "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sources": [p.name for p in pbfs], "files": files, "format": 1}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    size = sum(f.stat().st_size for f in out.iterdir()) / 2**30
    log(f"done: {out.resolve()}  ({size:.2f} GB). Copy this folder to a USB stick and plug it into the head unit.")

if __name__ == "__main__":
    main()
