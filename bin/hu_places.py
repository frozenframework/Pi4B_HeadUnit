"""
Places: the offline map's address book (Home, Work, Gym, School and your own saved spots), plus what it
learns by itself: where you drive to and where the car starts from (where it was parked), so places you
go often are offered for saving, and recent destinations are one tap away.

Everything stays on the head unit (~/.config/headunit/places.json and place-history.json); Settings in
the Places panel can clear the history. Positions are [lon, lat].
"""
import json, math, threading, time, uuid
from pathlib import Path

LABELS = ("home", "work", "gym", "school")          # one each; anything else is a saved favorite
NEAR_M = 60                                         # same spot
CLUSTER_M = 150                                     # "the same place" for counting visits
OFTEN = 3                                           # visits before a place is suggested
HISTORY_DAYS = 120
HISTORY_MAX = 1000

def dist_m(a, b):
    R = 6371008.8; r = math.pi / 180
    dlat = (b[1] - a[1]) * r; dlon = (b[0] - a[0]) * r
    s = math.sin(dlat / 2) ** 2 + math.cos(a[1] * r) * math.cos(b[1] * r) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(s))

def _ok_lnglat(v):
    try:
        lon, lat = float(v[0]), float(v[1])
        return [round(lon, 6), round(lat, 6)] if -180 <= lon <= 180 and -90 <= lat <= 90 else None
    except (TypeError, ValueError, IndexError):
        return None

class Places:
    def __init__(self, folder: Path, clock=time.time):
        self.dir = Path(folder); self.clock = clock; self.lock = threading.Lock()
        self.pf = self.dir / "places.json"; self.hf = self.dir / "place-history.json"
        self._migrate()

    # ---------------------------------------------------------- storage
    def _read(self, f, default):
        try: return json.loads(f.read_text())
        except (OSError, ValueError): return default

    def _write(self, f, data):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=1)); tmp.replace(f)

    def _migrate(self):
        """Favorites from before Places become saved places (once)."""
        old = self.dir / "favorites.json"
        if self.pf.exists() or not old.exists(): return
        places = []
        for f in self._read(old, []):
            ll = _ok_lnglat(f.get("lnglat"))
            if ll and f.get("name"):
                places.append({"id": uuid.uuid4().hex[:10], "label": "favorite", "name": str(f["name"])[:80], "lnglat": ll,
                               "sub": "", "added": int(self.clock())})
        self._write(self.pf, places)

    def places(self):
        return self._read(self.pf, [])

    def history(self):
        cut = self.clock() - HISTORY_DAYS * 86400
        return [h for h in self._read(self.hf, []) if h.get("t", 0) >= cut]

    # ---------------------------------------------------------- address book
    def save(self, label, name, lnglat, sub=""):
        ll = _ok_lnglat(lnglat)
        if not ll: raise ValueError("bad position")
        label = label if label in LABELS else "favorite"
        name = (str(name or "").strip() or label.title())[:80]
        with self.lock:
            ps = self.places()
            if label in LABELS:                                     # only one Home, one Work...
                ps = [p for p in ps if p.get("label") != label]
            else:                                                   # the same spot isn't saved twice
                ps = [p for p in ps if not (p.get("label") == "favorite" and dist_m(p["lnglat"], ll) < NEAR_M)]
            p = {"id": uuid.uuid4().hex[:10], "label": label, "name": name, "lnglat": ll, "sub": str(sub or "")[:120],
                 "added": int(self.clock())}
            ps.insert(0, p); self._write(self.pf, ps[:200])
            return p

    def remove(self, pid):
        with self.lock:
            ps = self.places(); n = len(ps)
            ps = [p for p in ps if p.get("id") != pid]
            self._write(self.pf, ps); return len(ps) < n

    def rename(self, pid, name):
        with self.lock:
            ps = self.places()
            for p in ps:
                if p.get("id") == pid: p["name"] = str(name).strip()[:80] or p["name"]
            self._write(self.pf, ps)

    # ---------------------------------------------------------- learning
    def record(self, kind, lnglat, name=""):
        """kind: route (you picked a destination) | arrive (you got there) | parked (the car started here)."""
        ll = _ok_lnglat(lnglat)
        if not ll or kind not in ("route", "arrive", "parked"): return
        with self.lock:
            h = self.history()
            if kind == "parked" and h:                              # one per stay: skip a repeat of the last start spot
                last = next((x for x in reversed(h) if x["kind"] == "parked"), None)
                if last and dist_m(last["lnglat"], ll) < NEAR_M and self.clock() - last["t"] < 1800: return
            h.append({"t": int(self.clock()), "kind": kind, "lnglat": ll, "name": str(name or "")[:80]})
            self._write(self.hf, h[-HISTORY_MAX:])

    def clear_history(self):
        with self.lock: self._write(self.hf, [])

    def frequent(self, limit=5):
        """Places you end up at often (arrivals + where the car was parked), not already saved."""
        saved = [p["lnglat"] for p in self.places()]
        clusters = []
        for h in self.history():
            if h["kind"] not in ("arrive", "parked"): continue
            c = next((c for c in clusters if dist_m(c["center"], h["lnglat"]) < CLUSTER_M), None)
            if not c:
                c = {"center": h["lnglat"], "pts": [], "names": {}, "last": 0}; clusters.append(c)
            c["pts"].append(h["lnglat"]); c["last"] = max(c["last"], h["t"])
            if h.get("name"): c["names"][h["name"]] = c["names"].get(h["name"], 0) + 1
            n = len(c["pts"]); c["center"] = [sum(p[0] for p in c["pts"]) / n, sum(p[1] for p in c["pts"]) / n]
        # names from route destinations near the cluster help too
        for h in self.history():
            if h["kind"] == "route" and h.get("name"):
                for c in clusters:
                    if dist_m(c["center"], h["lnglat"]) < CLUSTER_M: c["names"][h["name"]] = c["names"].get(h["name"], 0) + 1
        out = []
        for c in clusters:
            if len(c["pts"]) < OFTEN or any(dist_m(c["center"], s) < CLUSTER_M for s in saved): continue
            name = max(c["names"], key=c["names"].get) if c["names"] else ""
            out.append({"lnglat": [round(c["center"][0], 6), round(c["center"][1], 6)], "visits": len(c["pts"]),
                        "name": name, "last": c["last"]})
        out.sort(key=lambda x: (-x["visits"], -x["last"]))
        return out[:limit]

    def recent(self, limit=8):
        out = []
        for h in reversed(self.history()):
            if h["kind"] != "route" or not h.get("name"): continue
            if any(dist_m(o["lnglat"], h["lnglat"]) < NEAR_M for o in out): continue
            out.append({"name": h["name"], "lnglat": h["lnglat"], "t": h["t"]})
            if len(out) >= limit: break
        return out

    def summary(self):
        return {"places": self.places(), "frequent": self.frequent(), "recent": self.recent()}
