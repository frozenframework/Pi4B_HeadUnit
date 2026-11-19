"""
License-plate cameras (ALPR: Flock Safety and others) for the privacy route option: routes that go
around the known cameras when the driver asks for it.

Where they come from: OpenStreetMap, where volunteers (largely the DeFlock project) map them as
man_made=surveillance + surveillance:type=ALPR, with manufacturer=Flock Safety for Flock's. Two ways
onto the head unit:
  - the head unit downloads them itself over Wi-Fi (Overpass API, the map region's box): Settings >
    Maps > Plate cameras > Update, and by itself once a week while the option is on; kept in
    ~/.config/headunit/alpr-cameras.json;
  - or they come with the search data built on a computer (cameras table: maps/build-routing.py,
    maps/add-fuel.py).
The newer of the two is used. Coverage is only as good as the volunteers' mapping: a route "with no
known cameras" can still pass one that nobody has mapped yet.

Avoiding them (bin/hu-bridge.py do_route): route, find the cameras the route passes, ask Valhalla
again with those spots excluded (exclude_locations, 25 m around each), and repeat a few times; the
route passing the fewest wins, as long as it isn't more than MAX_EXTRA longer. A camera near the start
or the destination can't be avoided and isn't excluded (it would leave no route at all).
"""
import json, math, os, sqlite3, threading, time, urllib.parse, urllib.request
from pathlib import Path

R = 6371008.8
CELL = 20                         # grid of 0.05 degree
ON_ROUTE_M = 30                   # a camera this close to the route line watches it
EXCLUDE_RADIUS_M = 25             # road pieces this close to a camera are left out
END_M = 200                       # cameras this close to the start / destination can't be avoided
MAX_EXTRA = 1.0                   # at most twice as long as the plain route
ROUNDS = 5
FLOCK_WIKIDATA = "Q108485435"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

def is_flock(tags):
    t = " ".join(str(tags.get(k, "")) for k in ("manufacturer", "brand", "operator", "model")).lower()
    return "flock" in t or tags.get("manufacturer:wikidata") == FLOCK_WIKIDATA or tags.get("brand:wikidata") == FLOCK_WIKIDATA

def _m(lon1, lat1, lon2, lat2):
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return R * math.radians(math.hypot((lon2 - lon1) * k, lat2 - lat1))

class Cameras:
    def __init__(self, search_db, user_file, log=None):
        self.db, self.file, self.log = Path(search_db), Path(user_file), log
        self.pts, self.grid, self.stamp, self.source, self.updated = [], {}, None, "", 0
        self.busy = False; self.error = ""; self.lock = threading.Lock()

    # ------------------------------------------------------------ loading
    def _stamps(self):
        out = []
        for p in (self.file, self.db):
            try: st = p.stat(); out.append([str(p), int(st.st_mtime), st.st_size])
            except OSError: out.append(None)
        return out

    def ensure(self):
        st = self._stamps()
        if st == self.stamp: return
        pts, source, updated = [], "", 0
        try:                                              # downloaded on the head unit
            d = json.loads(self.file.read_text())
            pts = [tuple(p) for p in d.get("cameras", [])]; source = "download"; updated = d.get("updated", 0)
        except (OSError, ValueError):
            pass
        try:                                              # or from the search data, when that's newer
            dbt = int(self.db.stat().st_mtime)
            if not pts or dbt > updated:
                con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
                try:
                    if con.execute("SELECT 1 FROM sqlite_master WHERE name='cameras'").fetchone():
                        rows = [(lon, lat, int(flock)) for lon, lat, flock in con.execute("SELECT lon, lat, flock FROM cameras")]
                        if rows: pts, source, updated = rows, "search", int(dbt)
                finally:
                    con.close()
        except (OSError, sqlite3.Error):
            pass
        grid = {}
        for i, p in enumerate(pts):
            grid.setdefault((int(math.floor(p[0] * CELL)), int(math.floor(p[1] * CELL))), []).append(i)
        with self.lock:
            self.pts, self.grid, self.stamp, self.source, self.updated = pts, grid, st, source, updated

    def status(self):
        self.ensure()
        with self.lock: pts = self.pts
        return {"count": len(pts), "flock": sum(1 for p in pts if p[2]), "source": self.source, "updated": self.updated,
                "busy": self.busy, "error": self.error}

    # ------------------------------------------------------------ queries
    def _cells(self, w, s, e, n):
        with self.lock: pts, grid = self.pts, self.grid
        for cx in range(int(math.floor(w * CELL)), int(math.floor(e * CELL)) + 1):
            for cy in range(int(math.floor(s * CELL)), int(math.floor(n * CELL)) + 1):
                for i in grid.get((cx, cy), ()):
                    yield pts[i]

    def in_view(self, w, s, e, n, which="flock", limit=800):
        self.ensure()
        if (e - w) * (n - s) > 25: return {"type": "FeatureCollection", "features": []}
        hits = [p for p in self._cells(w, s, e, n) if w <= p[0] <= e and s <= p[1] <= n and (which == "all" or p[2])]
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p[0], p[1]]}, "properties": {"flock": p[2]}} for p in hits[:limit]]}

    def on_route(self, shape, which="flock", ends=()):
        """(avoidable, unavoidable): the cameras within ON_ROUTE_M of the route line; unavoidable ones are
        near the start / destination. Each a (lon, lat)."""
        self.ensure()
        found = {}
        pad = ON_ROUTE_M / 111000.0 * 1.5
        for a, b in zip(shape, shape[1:]):
            for p in self._cells(min(a[0], b[0]) - pad, min(a[1], b[1]) - pad, max(a[0], b[0]) + pad, max(a[1], b[1]) + pad):
                if which != "all" and not p[2]: continue
                key = (p[0], p[1])
                if key in found: continue
                k = math.cos(math.radians(a[1]))
                bx, by = (b[0] - a[0]) * k, b[1] - a[1]; px, py = (p[0] - a[0]) * k, p[1] - a[1]
                L = bx * bx + by * by
                t = 0.0 if L == 0 else max(0.0, min(1.0, (px * bx + py * by) / L))
                if R * math.radians(math.hypot(px - t * bx, py - t * by)) <= ON_ROUTE_M: found[key] = True
        near_end = lambda c: any(_m(c[0], c[1], e[0], e[1]) <= END_M for e in ends)
        cams = list(found)
        return [c for c in cams if not near_end(c)], [c for c in cams if near_end(c)]

    # ------------------------------------------------------------ download (Overpass)
    def update(self, bbox, urls=None, timeout=600):
        """Download every ALPR camera in bbox [w, s, e, n] from OpenStreetMap (Overpass API). Returns the count."""
        if self.busy: return None
        self.busy = True; self.error = ""
        try:
            w, s, e, n = bbox
            q = f'[out:json][timeout:{timeout}];nwr["surveillance:type"="ALPR"]({s},{w},{n},{e});out center tags;'
            last = None
            for url in (urls or [u for u in os.environ.get("HU_OVERPASS", "").split(",") if u] or OVERPASS):
                try:
                    req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                                 headers={"User-Agent": "Pi4B-HeadUnit (privacy routing)"})
                    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
                        d = json.loads(r.read())
                    break
                except Exception as ex:
                    last = ex; d = None
            if d is None: raise last or RuntimeError("no Overpass server answered")
            cams = []
            for el in d.get("elements", []):
                lat, lon = el.get("lat"), el.get("lon")
                if lat is None and el.get("center"): lat, lon = el["center"].get("lat"), el["center"].get("lon")
                if lat is None or lon is None: continue
                cams.append([round(lon, 6), round(lat, 6), 1 if is_flock(el.get("tags", {})) else 0])
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"updated": int(time.time()), "bbox": bbox, "cameras": cams}, separators=(",", ":")))
            os.replace(tmp, self.file)
            if self.log: self.log(f"plate cameras: {len(cams)} downloaded ({sum(c[2] for c in cams)} Flock)")
            self.ensure()
            return len(cams)
        except Exception as ex:
            self.error = str(ex)[:200]
            if self.log: self.log(f"plate cameras: download failed: {ex}")
            return None
        finally:
            self.busy = False

def avoid(route_fn, req, cams, which="flock", max_extra=MAX_EXTRA, rounds=ROUNDS):
    """The route passing the fewest known cameras. route_fn(req) -> (trip, shape, extra) asks Valhalla
    (with the route style); req = a Valhalla request with two locations. Returns (trip, shape, extra, info)."""
    locs = req["locations"]; ends = [(locs[0]["lon"], locs[0]["lat"]), (locs[-1]["lon"], locs[-1]["lat"])]
    trip, shape, extra = route_fn(req)
    base_time = trip["summary"]["time"]
    first_hits, stuck = cams.on_route(shape, which, ends)
    best = (len(first_hits), base_time, trip, shape, extra, stuck)
    excluded, hits = [], first_hits
    for _ in range(rounds):
        new = [c for c in hits if c not in excluded]
        if not new: break
        excluded += new
        try:
            t2, s2, x2 = route_fn({**req, "exclude_locations": [{"lon": c[0], "lat": c[1], "radius": EXCLUDE_RADIUS_M} for c in excluded]})
        except Exception:                                 # no way around these (an island, a single road in): keep the best so far
            break
        hits, stuck2 = cams.on_route(s2, which, ends)
        if t2["summary"]["time"] <= base_time * (1 + max_extra) and (len(hits), t2["summary"]["time"]) < best[:2]:
            best = (len(hits), t2["summary"]["time"], t2, s2, x2, stuck2)
        if not hits: break
    n, _, trip, shape, extra, stuck = best
    return trip, shape, extra, {"which": which, "passed": n + len(stuck), "unavoidable": len(stuck),
                                "avoided": max(0, len(first_hits) - n), "known": cams.status()["count"]}
