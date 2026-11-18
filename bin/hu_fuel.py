"""
Gas stations for the map: every station in the offline search data, kept in memory with a coarse grid
so the map can ask for "the ones in this view" (icons) and the driver for "the nearest ones" or "the
ones along my route" (Places > Gas, or typing gas / fuel / diesel in Search).

Where they come from (search.sqlite, built by maps/build-routing.py):
  - the `fuel` table (every amenity=fuel in OpenStreetMap, named or not, with brand and diesel), or
  - for search data built before that table existed: the named ones among the places (kind "fuel").
Worked out once per search data in the background (a minute or so on the Pi for the whole US) and kept
in a small file next to the user settings, so later starts load it in a moment.
"""
import json, math, os, sqlite3, threading, time
from pathlib import Path

CELL = 10                       # grid cells of 0.1 degree (~11 km)
R = 6371008.8
FIELDS = ("lon", "lat", "name", "brand", "diesel", "detail")
DEDUPE_M = 60                   # the same station mapped twice (pumps + building): kept once

def _m(lon1, lat1, lon2, lat2):
    """Metres between two points (equirectangular: plenty for a few km)."""
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return R * math.radians(math.hypot((lon2 - lon1) * k, lat2 - lat1))

def read_stations(db):
    """[(lon, lat, name, brand, diesel, detail)] from the search data. diesel: 1 sells it, 0 unknown, -1 doesn't."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='fuel'").fetchone():
            rows = con.execute("SELECT lon, lat, name, brand, diesel, detail FROM fuel").fetchall()
        else:                                          # older search data: named stations among the places
            rows = [(lon, lat, name, "", 0, detail) for name, detail, lon, lat in
                    con.execute("SELECT name, detail, lon, lat FROM places WHERE kind = 'fuel'")]
    finally:
        con.close()
    rows.sort(key=lambda r: (round(r[1], 3), round(r[0], 3)))
    out, grid = [], {}
    for lon, lat, name, brand, diesel, detail in rows:
        if lon is None or lat is None: continue
        key = (int(math.floor(lon * 1000)), int(math.floor(lat * 1000)))
        dup = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((key[0] + dx, key[1] + dy), ()):
                    o = out[i]
                    if _m(lon, lat, o[0], o[1]) < DEDUPE_M and (not brand or not o[3] or brand.lower() == o[3].lower()): dup = i
        if dup is not None:                             # keep the better-described one
            o = out[dup]
            d = 1 if 1 in (o[4], diesel) else (o[4] or diesel or 0)      # "sells diesel" wins over unknown / no
            out[dup] = (o[0], o[1], o[2] or name or "", o[3] or brand or "", int(d), o[5] or detail or "")
            continue
        grid.setdefault(key, []).append(len(out))
        out.append((round(lon, 6), round(lat, 6), name or "", brand or "", int(diesel or 0), detail or ""))
    return out

class Fuel:
    def __init__(self, search_db, cache, log=None):
        self.db, self.cache, self.log = Path(search_db), Path(cache), log
        self.pts, self.grid, self.stamp, self.busy, self.lock = [], {}, None, False, threading.Lock()

    def _stamp(self):
        try: st = self.db.stat(); return [int(st.st_mtime), st.st_size]
        except OSError: return None

    def ensure(self, wait=False):
        """Starts loading (or working out) the stations for the current search data; returns at once
        unless wait. Cheap to call often."""
        st = self._stamp()
        if st is None:
            self.pts, self.grid, self.stamp = [], {}, None; return
        if st == self.stamp or self.busy: return
        self.busy = True
        th = threading.Thread(target=self._load, args=(st,), daemon=True); th.start()
        if wait: th.join()

    def _load(self, st):
        try:
            try:
                c = json.loads(self.cache.read_text())
                pts = [tuple(p) for p in c["points"]] if c.get("stamp") == st and c.get("v") == 1 else None
            except (OSError, ValueError, KeyError):
                pts = None
            if pts is None:
                t = time.time()
                try: os.nice(10)
                except OSError: pass
                pts = read_stations(self.db)
                try:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.cache.with_suffix(".tmp"); tmp.write_text(json.dumps({"v": 1, "stamp": st, "points": pts}, separators=(",", ":")))
                    os.replace(tmp, self.cache)
                except OSError:
                    pass
                if self.log: self.log(f"gas stations: {len(pts)} found in the search data in {time.time() - t:.0f} s")
            grid = {}
            for i, p in enumerate(pts):
                grid.setdefault((int(math.floor(p[0] * CELL)), int(math.floor(p[1] * CELL))), []).append(i)
            with self.lock:
                self.pts, self.grid, self.stamp = pts, grid, st
        except Exception as e:
            if self.log: self.log(f"gas stations: {e}")
            self.stamp = st
        finally:
            self.busy = False

    def ready(self): return self.stamp is not None and not self.busy

    def _cells(self, w, s, e, n):
        with self.lock: pts, grid = self.pts, self.grid
        for cx in range(int(math.floor(w * CELL)), int(math.floor(e * CELL)) + 1):
            for cy in range(int(math.floor(s * CELL)), int(math.floor(n * CELL)) + 1):
                for i in grid.get((cx, cy), ()):
                    yield pts[i]

    def in_view(self, w, s, e, n, limit=600):
        """GeoJSON of the stations in a box (the map's view). At most `limit`, spread over the box."""
        if (e - w) * (n - s) > 25: return {"type": "FeatureCollection", "features": [], "tooMany": True}   # zoomed far out
        hits = [p for p in self._cells(w, s, e, n) if w <= p[0] <= e and s <= p[1] <= n]
        if len(hits) > limit: hits = hits[::math.ceil(len(hits) / limit)]
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p[0], p[1]]},
             "properties": {"name": p[2] or p[3], "brand": p[3], "diesel": p[4], "detail": p[5]}} for p in hits]}

    def _item(self, p, **kw):
        return {"name": p[2] or p[3], "brand": p[3] if p[3] and p[3] != p[2] else "", "diesel": p[4], "detail": p[5],
                "lon": p[0], "lat": p[1], "kind": "fuel", **kw}

    def nearest(self, lon, lat, n=10, radius_km=40, diesel_first=False):
        """The closest stations (straight-line), widening the search until enough are found."""
        out = []
        for r in (5, 15, radius_km):
            d = r / 111.0; k = d / max(0.2, math.cos(math.radians(lat)))
            out = [(_m(lon, lat, p[0], p[1]), p) for p in self._cells(lon - k, lat - d, lon + k, lat + d)]
            out = [x for x in out if x[0] <= r * 1000]
            if len(out) >= n: break
        out.sort(key=lambda x: x[0])
        if diesel_first:                                # a known diesel station within 8 km goes before the others
            out.sort(key=lambda x: (0 if (x[1][4] > 0 and x[0] < 8000) else 1, x[0]))
        return [self._item(p, dist=round(m)) for m, p in out[:n]]

    def along(self, shape, n=10, max_off=1600, diesel_first=False):
        """Stations near the road ahead: shape = the rest of the route [[lon, lat], ...] (from the car on).
        Each with `ahead` (metres along the route to the turn-off) and `off` (metres off the route),
        in the order they come up."""
        if len(shape) < 2: return []
        # cumulative distance along the route
        cum = [0.0]
        for a, b in zip(shape, shape[1:]): cum.append(cum[-1] + _m(a[0], a[1], b[0], b[1]))
        best = {}
        pad = max_off / 111000.0 * 1.6
        for i, (a, b) in enumerate(zip(shape, shape[1:])):
            w, e = min(a[0], b[0]) - pad, max(a[0], b[0]) + pad
            s, nn = min(a[1], b[1]) - pad, max(a[1], b[1]) + pad
            for p in self._cells(w, s, e, nn):
                # distance from p to segment a-b (local flat projection, metres)
                k = math.cos(math.radians(a[1]))
                ax, ay = 0.0, 0.0; bx, by = (b[0] - a[0]) * k, b[1] - a[1]; px, py = (p[0] - a[0]) * k, p[1] - a[1]
                L = bx * bx + by * by
                raw = 0.0 if L == 0 else (px * bx + py * by) / L
                if i == 0 and raw < 0 and R * math.radians(math.hypot(px, py)) > 150: continue   # behind the car
                t = max(0.0, min(1.0, raw))
                off = R * math.radians(math.hypot(px - t * bx, py - t * by))
                if off > max_off: continue
                ahead = cum[i] + t * (cum[i + 1] - cum[i])
                key = (p[0], p[1])
                if key not in best or off < best[key][0]: best[key] = (off, ahead, p)
        items = sorted(best.values(), key=lambda x: x[1])
        if diesel_first:
            known = [x for x in items if x[2][4] > 0]
            if known: items = known + [x for x in items if x[2][4] <= 0]
        return [self._item(p, ahead=round(ahead), off=round(off)) for off, ahead, p in items[:n]]

    def count(self):
        with self.lock: return len(self.pts)
