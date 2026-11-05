"""
Street addresses for offline search ("1234 N Main St, Conroe TX 77301").

Shared by the map builder (maps/build-routing.py writes the tables) and the head unit (hu-bridge's
search reads them), so both spell streets the same way:

  addr(num, suffix, street, core, city, zip, lon, lat)   one row per OpenStreetMap address
  street_pts(street, core, city, lon, lat)               points along named roads (for a street with
                                                         no matching house number)
  street = the whole name normalized ("n main st"), core = without direction and type ("main"),
  so "1234 Main Street", "1234 n main st" and "1234 North Main" all find it.

Lookup: the exact house number on that street (preferring the city / ZIP you typed, then the one
nearest to you); otherwise a position estimated between the nearest lower and higher numbers on the
same street; otherwise the street itself.
"""
import math, re, sqlite3

TYPES = {"street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave", "road": "rd", "rd": "rd", "drive": "dr", "dr": "dr",
         "boulevard": "blvd", "blvd": "blvd", "highway": "hwy", "hwy": "hwy", "parkway": "pkwy", "pkwy": "pkwy", "lane": "ln", "ln": "ln",
         "court": "ct", "ct": "ct", "place": "pl", "pl": "pl", "circle": "cir", "cir": "cir", "trail": "trl", "trl": "trl",
         "freeway": "fwy", "fwy": "fwy", "expressway": "expy", "expy": "expy", "terrace": "ter", "ter": "ter", "way": "way",
         "loop": "loop", "square": "sq", "sq": "sq", "crossing": "xing", "xing": "xing", "bend": "bnd", "bnd": "bnd",
         "turnpike": "tpke", "tpke": "tpke", "row": "row", "run": "run", "path": "path", "pass": "pass", "spur": "spur"}
DIRS = {"north": "n", "n": "n", "south": "s", "s": "s", "east": "e", "e": "e", "west": "w", "w": "w",
        "northeast": "ne", "ne": "ne", "northwest": "nw", "nw": "nw", "southeast": "se", "se": "se", "southwest": "sw", "sw": "sw"}
PHRASES = [("farm to market road", "fm"), ("farm to market", "fm"), ("farm-to-market road", "fm"), ("ranch road", "rm"),
           ("county road", "cr"), ("state highway", "sh"), ("us highway", "us"), ("u s highway", "us"), ("us hwy", "us"), ("interstate", "i"),
           ("saint ", "st ")]
STATES = {"tx", "texas", "la", "louisiana", "ok", "oklahoma", "nm", "new mexico", "ar", "arkansas"}

def tokens(s):
    s = " " + re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower().replace(".", "").replace("'", "")) + " "
    for a, b in PHRASES: s = s.replace(" " + a + " ", " " + b + " ")
    ts = s.split()
    for i, t in enumerate(ts[:-1]):                # "Highway 105" / "TX 105" / "SH 105" are the same state highway
        if t in ("highway", "hwy", "tx", "sh") and ts[i + 1].isdigit() and (i == 0 or ts[i - 1] not in ("us", "i")):
            ts[i] = "sh"
    return [DIRS.get(t, TYPES.get(t, t)) for t in ts]

def norm_street(s):
    return " ".join(tokens(s))

def core_street(s):
    ts = tokens(s)
    if len(ts) > 1 and ts[0] in DIRS.values(): ts = ts[1:]
    if len(ts) > 1 and ts[-1] in DIRS.values(): ts = ts[:-1]
    if len(ts) > 1 and ts[-1] in TYPES.values() and ts[0] not in ("fm", "rm", "cr", "sh", "us", "i"): ts = ts[:-1]
    return " ".join(ts)

UPPER = {"sh", "us", "fm", "rm", "cr", "i", "ne", "nw", "se", "sw"}
def title_street(s):
    """'sh 105 w' -> 'SH 105 W', 'n main st' -> 'N Main St'"""
    return " ".join(w.upper() if w in UPPER else w.title() for w in str(s).split())

def norm_city(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).split())

def parse_number(h):
    """'1234' -> (1234, ''), '12A' -> (12, 'a'), '1234-1236' -> (1234, ''), 'Lot 5' -> None"""
    m = re.match(r"^\s*(\d{1,6})\s*([a-zA-Z]?)\b", str(h or ""))
    return (int(m.group(1)), m.group(2).lower()) if m else None

# ------------------------------------------------------------------ building (maps/build-routing.py)
SCHEMA = """
CREATE TABLE IF NOT EXISTS addr(num INTEGER, suffix TEXT, street TEXT, core TEXT, city TEXT, zip TEXT, lon REAL, lat REAL);
CREATE TABLE IF NOT EXISTS street_pts(street TEXT, core TEXT, city TEXT, lon REAL, lat REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS addr_core_num ON addr(core, num);
CREATE INDEX IF NOT EXISTS street_pts_core ON street_pts(core);
"""

def addr_row(tags, lon, lat):
    pn = parse_number(tags.get("addr:housenumber"))
    st = tags.get("addr:street") or tags.get("addr:place")
    if not pn or not st: return None
    z = re.match(r"\d{5}", str(tags.get("addr:postcode", "")))
    return (pn[0], pn[1], norm_street(st), core_street(st), norm_city(tags.get("addr:city", "")), z.group(0) if z else "", lon, lat)

def street_row(name, city, lon, lat):
    return (norm_street(name), core_street(name), norm_city(city), lon, lat)

# ------------------------------------------------------------------ lookup (hu-bridge)
def has_addresses(con):
    try: return con.execute("SELECT 1 FROM sqlite_master WHERE name='addr'").fetchone() is not None
    except sqlite3.Error: return False

def parse_query(q):
    """'1234 n main st, conroe tx 77301' -> (1234, '', 'n main st', ['conroe'], '77301') or None"""
    m = re.match(r"^\s*(\d{1,6})([a-zA-Z])?\s+(.+)$", q or "")     # 12A Main St; "130 N Main" = 130, North Main
    if not m: return None
    num, suffix, rest = int(m.group(1)), (m.group(2) or "").lower(), m.group(3)
    zm = re.search(r"\b(\d{5})(?:-\d{4})?\b", rest); zipc = zm.group(1) if zm else ""
    if zm: rest = (rest[:zm.start()] + rest[zm.end():]).strip(" ,")
    if "," in rest:
        street, place = rest.split(",", 1)
    else:
        street, place = rest, ""
    place_t = [t for t in norm_city(place).split() if t not in STATES]
    return num, suffix, street.strip(), place_t, zipc

def _dist(a, b):
    dx = (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2)); dy = a[1] - b[1]
    return math.hypot(dx, dy) * 111_320

def lookup(con, q, near=None, limit=5):
    """Address results for search: [{name, kind: address|address_approx|street, detail, lon, lat}]."""
    p = parse_query(q)
    if not p or not has_addresses(con): return []
    num, suffix, street, place_t, zipc = p
    words = tokens(street)
    # without a comma the city may be typed after the street: try the longest street that exists
    candidates = []
    for cut in range(len(words), 0, -1):
        st = " ".join(words[:cut]); core = core_street(st)
        if not core: continue
        if con.execute("SELECT 1 FROM addr WHERE core=? LIMIT 1", (core,)).fetchone() or \
           con.execute("SELECT 1 FROM street_pts WHERE core=? LIMIT 1", (core,)).fetchone():
            candidates = [(st, core, place_t or words[cut:])]; break
    if not candidates: return []
    st, core, city_t = candidates[0]
    city = " ".join(city_t)
    def score(row):                                    # lower is better
        r_street, r_city, r_zip, lon, lat = row
        s = 0.0
        if r_street != st: s += 0.5                     # typed "main st", this is "n main st": nearly as good
        if city and r_city and not r_city.startswith(city): s += 10
        if zipc and r_zip and r_zip != zipc: s += 10
        if near: s += min(_dist(near, (lon, lat)) / 20_000, 8)   # the one near you before one 60 miles away
        return s
    pretty = lambda n, sfx, name, c: f"{n}{sfx.upper()} {title_street(name)}" + (f", {c.title()}" if c else "")
    rows = con.execute("SELECT num, suffix, street, city, zip, lon, lat FROM addr WHERE core=? AND num=? LIMIT 200", (core, num)).fetchall()
    if suffix: rows = [r for r in rows if r[1] == suffix] or rows
    if rows:
        rows.sort(key=lambda r: score((r[2], r[3], r[4], r[5], r[6])))
        out, seen = [], []
        for r in rows:
            if any(_dist((r[5], r[6]), s) < 150 for s in seen): continue
            seen.append((r[5], r[6]))
            out.append({"name": pretty(r[0], r[1], r[2], ""), "kind": "address", "detail": " ".join(x for x in (r[3].title(), r[4]) if x),
                        "lon": r[5], "lat": r[6]})
            if len(out) >= limit: break
        return out
    # no such number: estimate between the closest numbers below and above on the same street
    lo = con.execute("SELECT num, street, city, zip, lon, lat FROM addr WHERE core=? AND num<? ORDER BY num DESC LIMIT 60", (core, num)).fetchall()
    hi = con.execute("SELECT num, street, city, zip, lon, lat FROM addr WHERE core=? AND num>? ORDER BY num ASC LIMIT 60", (core, num)).fetchall()
    best = None
    for a in lo:
        for b in hi:
            if a[1] != b[1] or _dist((a[4], a[5]), (b[4], b[5])) > 2500 or b[0] - a[0] > 2000: continue
            s = score((a[1], a[2], a[3], a[4], a[5])) + (b[0] - a[0]) / 4000
            if best is None or s < best[0]: best = (s, a, b)
    if best:
        _, a, b = best; f = (num - a[0]) / (b[0] - a[0])
        lon, lat = a[4] + (b[4] - a[4]) * f, a[5] + (b[5] - a[5]) * f
        return [{"name": pretty(num, suffix, a[1], ""), "kind": "address_approx", "detail": a[2].title(), "lon": lon, "lat": lat}]
    # the street, at least
    pts = con.execute("SELECT street, city, lon, lat FROM street_pts WHERE core=? LIMIT 400", (core,)).fetchall()
    if not pts: return []
    pts.sort(key=lambda r: score((r[0], r[1], "", r[2], r[3])))
    r = pts[0]
    return [{"name": title_street(r[0]), "kind": "street_only", "detail": r[1].title(), "lon": r[2], "lat": r[3]}]
