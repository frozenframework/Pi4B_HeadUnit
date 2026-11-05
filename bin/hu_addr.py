"""
Street addresses for offline search ("1234 N Main St, Conroe TX 77301").

Shared by the map builder (maps/build-routing.py writes the tables) and the head unit (hu-bridge's
search reads them), so both spell streets the same way:

  addr(num, suffix, street, core, city, zip, lon, lat)   one row per OpenStreetMap address
  street_pts(street, core, city, lon, lat)               points along named roads (for a street with
                                                         no matching house number)
  street = the whole name normalized ("n main st"), core = without direction and type ("main"),
  so "1234 Main Street", "1234 n main st" and "1234 North Main" all find it.

  areas(kind, key, lon, lat, n)                          ZIP code centres (newer search data; older data
                                                         gets them from hu-bridge's background pass)

Lookup searches everything in the search data (the whole state and its neighbours), not just around
you: the exact house number on that street, preferring the ZIP and city you typed (searched there
first), then Texas unless you typed another state, then the one nearest to you; otherwise a position
estimated between the closest lower and higher numbers on the same street; otherwise the street.
Nothing at all: suggest() lists options (similarly spelled streets and places, the city / ZIP).
"""
import difflib, functools, math, re, sqlite3

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
STATE_CODES = {"tx": "TX", "texas": "TX", "la": "LA", "louisiana": "LA", "ok": "OK", "oklahoma": "OK",
               "nm": "NM", "new mexico": "NM", "ar": "AR", "arkansas": "AR"}
STATES = set(STATE_CODES)
DEFAULT_STATE = "TX"            # assumed when no state is typed: Texas results first

# Texas outline (coarse; out into Mexico and the Gulf, where there's nothing else) - enough to tell which
# state a result is in. Neighbours by their simple borders.
TEXAS = [(-106.62, 32.00), (-103.06, 32.00), (-103.04, 36.50), (-100.00, 36.50), (-100.00, 34.56),
         (-99.40, 34.38), (-99.00, 34.21), (-98.10, 34.13), (-97.55, 33.90), (-97.00, 33.73), (-96.60, 33.85),
         (-96.00, 33.84), (-95.30, 33.88), (-94.75, 33.65), (-94.48, 33.64), (-94.04, 33.55), (-94.04, 33.02),
         (-94.04, 31.99), (-93.80, 31.60), (-93.55, 31.20), (-93.53, 30.70), (-93.60, 30.40), (-93.70, 30.10),
         (-93.84, 29.69), (-93.85, 29.30), (-95.00, 28.40), (-96.60, 27.60), (-96.90, 26.50), (-96.90, 25.50),
         (-99.00, 26.10), (-99.90, 27.40), (-100.80, 28.50), (-101.40, 29.45), (-102.50, 29.40), (-103.00, 28.80),
         (-104.00, 28.90), (-104.60, 29.50), (-105.00, 30.30), (-106.20, 31.20), (-106.45, 31.60), (-106.53, 31.78)]

def _inside(x, y, poly):
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): c = not c
    return c

def state_of(lon, lat):
    return _state_cell(round(lon * 50), round(lat * 50))     # ~2 km cells: thousands of results stay fast

@functools.lru_cache(maxsize=65536)
def _state_cell(x, y):
    lon, lat = x / 50, y / 50
    if _inside(lon, lat, TEXAS): return "TX"
    if -109.06 <= lon < -103.00 and 31.33 <= lat <= 37.0: return "NM"
    if -103.00 <= lon < -94.43 and 33.60 <= lat <= 37.0: return "OK"
    if -94.62 <= lon <= -89.60 and 33.00 <= lat <= 36.5: return "AR"
    if -94.05 <= lon <= -88.80 and 28.80 <= lat < 33.02: return "LA"
    return ""

def strip_state(words):
    """['la', 'porte', 'tx'] -> (['la', 'porte'], 'TX'): a state only counts at the end"""
    if len(words) >= 2 and " ".join(words[-2:]) in STATE_CODES: return words[:-2], STATE_CODES[" ".join(words[-2:])]
    if words and words[-1] in STATE_CODES: return words[:-1], STATE_CODES[words[-1]]
    return words, ""

def query_state(q):
    """The state typed at the end of a search ("..., Shreveport LA 71101") or ''."""
    w = norm_city(re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", q or "")).split()
    return strip_state(w)[1] if len(w) > 2 else ""

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
    if len(ts) > 2 and len(ts[0]) == 2 and ts[1] == "cr" and ts[0] not in DIRS.values(): ts = ts[1:]   # 911 data: "AN County Road 309"
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
CREATE TABLE IF NOT EXISTS areas(kind TEXT, key TEXT, lon REAL, lat REAL, n INTEGER, PRIMARY KEY(kind, key));
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
    place_t = strip_state(norm_city(place).split())[0]
    return num, suffix, street.strip(), place_t, zipc

def _dist(a, b):
    dx = (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2)); dy = a[1] - b[1]
    return math.hypot(dx, dy) * 111_320

PLACE_RANK = {"place:city": 0, "place:town": 1, "place:village": 2, "place:suburb": 3, "place:hamlet": 4, "place:neighbourhood": 5}

def zip_centre(con, zipc, zips=None):
    try:
        r = con.execute("SELECT lon, lat FROM areas WHERE kind='zip' AND key=?", (zipc,)).fetchone()
        if r: return tuple(r)
    except sqlite3.Error:                                # older search data: the bridge's background pass
        pass
    z = (zips or {}).get(zipc)
    return tuple(z[:2]) if z else None

def city_centre(con, city_words, want, near=None):
    """Where a typed city / town is: its OpenStreetMap place, in the wanted state first."""
    if not city_words: return None
    name = " ".join(city_words)
    try:
        rows = con.execute("SELECT name, kind, lon, lat FROM places WHERE places MATCH ? LIMIT 400",
                           ('name : "' + name.replace('"', "") + '"',)).fetchall()
    except sqlite3.Error:
        return None
    rows = [r for r in rows if r[1] in PLACE_RANK and norm_city(r[0]) == name]
    if not rows: return None
    rows.sort(key=lambda r: (state_of(r[2], r[3]) != want, PLACE_RANK[r[1]], _dist(near, (r[2], r[3])) if near else 0))
    return (rows[0][2], rows[0][3])

def find_area(con, city_words, zipc, want, near=None, zips=None):
    """The typed ZIP's centre, else the typed city's: searched there first."""
    return (zipc and zip_centre(con, zipc, zips)) or city_centre(con, city_words, want, near)

def street_type(street):
    """'n brandon rd' -> 'rd', 'state hwy 105 w' -> '' (a numbered road), 'brandon' -> ''"""
    ts = street.split()
    if len(ts) > 1 and ts[-1] in DIRS.values(): ts = ts[:-1]
    return ts[-1] if len(ts) > 1 and ts[-1] in TYPES.values() and ts[0] not in ("fm", "rm", "cr", "sh", "us", "i") else ""

def _scorer(st, city, zipc, want, strict_state, anchor, typed_area=False):
    typed_type = street_type(st)
    def score(r_street, r_city, r_zip, lon, lat):        # lower is better
        s = 0.0
        if r_street != st:
            rt = street_type(r_street)
            s += 6 if typed_type and rt and rt != typed_type else 0.5   # Brandon Way isn't Brandon Rd; "main st" ~ "n main st"
        if city and r_city: s += -2 if r_city.startswith(city) else 10      # the city you typed, confirmed
        if zipc and r_zip: s += -2 if r_zip == zipc else 10
        if state_of(lon, lat) != want: s += 20 if strict_state else 9   # Texas first unless you typed another state
        if anchor:                                       # in the ZIP / city you typed (that matters most), else near you
            d = _dist(anchor, (lon, lat))
            s += min(d / 5_000, 15) if typed_area else min(d / 20_000, 8)
        return s
    return score

def title_place(city, lon, lat, want):
    st = state_of(lon, lat)
    return city.title() + (f", {st}" if st and st != want else "") if city else (st if st and st != want else "")

def lookup(con, q, near=None, limit=5, zips=None):
    """Address results for search: [{name, kind: address|address_approx|street_only, detail, lon, lat}]."""
    p = parse_query(q)
    if not p or not has_addresses(con): return []
    num, suffix, street, place_t, zipc = p
    typed_state = query_state(q); want = typed_state or DEFAULT_STATE
    words = tokens(street)
    # without a comma the city may be typed after the street: try the longest street that exists
    candidates = []
    for cut in range(len(words), 0, -1):
        st = " ".join(words[:cut]); core = core_street(st)
        if not core: continue
        if con.execute("SELECT 1 FROM addr WHERE core=? LIMIT 1", (core,)).fetchone() or \
           con.execute("SELECT 1 FROM street_pts WHERE core=? LIMIT 1", (core,)).fetchone():
            candidates = [(st, core, place_t or strip_state(words[cut:])[0])]; break
    if not candidates: return []
    st, core, city_t = candidates[0]
    city = " ".join(city_t)
    area = find_area(con, city_t, zipc, want, near, zips); anchor = area or near
    score = _scorer(st, city, zipc, want, bool(typed_state), anchor, bool(area))
    pretty = lambda n, sfx, name: f"{n}{sfx.upper()} {title_street(name)}"
    def where(r_city, lon, lat, r_zip=""):               # don't call a street 300 km away "Conroe"
        in_town = city and area and _dist(area, (lon, lat)) < 25_000
        return " ".join(x for x in (title_place(r_city or (city if in_town else ""), lon, lat, want), r_zip) if x)
    # Every candidate is scored the same way and the best come first: the exact house number, else a
    # position estimated between its neighbours (+1), else the street itself (+2). So a house number
    # 250 km away doesn't beat your own street in the town you typed when OpenStreetMap has no number
    # for your house, and a Brandon Way doesn't stand in for Brandon Rd.
    cands = []                                           # (score, result)
    rows = con.execute("SELECT num, suffix, street, city, zip, lon, lat FROM addr WHERE core=? AND num=? LIMIT 5000", (core, num)).fetchall()
    if suffix: rows = [r for r in rows if r[1] == suffix] or rows
    for r in rows:
        cands.append((score(r[2], r[3], r[4], r[5], r[6]), {"name": pretty(r[0], r[1], r[2]), "kind": "address",
                      "detail": where(r[3], r[5], r[6], r[4]), "lon": r[5], "lat": r[6]}))
    best_exact = min((c[0] for c in cands), default=99)
    if best_exact > 1.5:
        # estimate between the closest numbers below and above on the same street (in the city / ZIP you
        # typed first: every town has a Main St), one per street
        near_nums = con.execute("SELECT num, street, city, zip, lon, lat FROM addr WHERE core=? AND num BETWEEN ? AND ? LIMIT 50000",
                                (core, num - 2000, num + 2000)).fetchall()
        keyed = sorted(((score(r[1], r[2], r[3], r[4], r[5]), r) for r in near_nums), key=lambda x: x[0])
        lo = [x for x in keyed if x[1][0] < num][:300]; hi = [x for x in keyed if x[1][0] > num][:300]
        pairs = []
        for sa, a in lo:
            for sb, b in hi:
                if a[1] != b[1] or _dist((a[4], a[5]), (b[4], b[5])) > 2500: continue
                pairs.append((sa + (b[0] - a[0]) / 4000, a, b))
        pairs.sort(key=lambda x: x[0])
        for s_, a, b in pairs[:40]:
            f = (num - a[0]) / (b[0] - a[0]); lon, lat = a[4] + (b[4] - a[4]) * f, a[5] + (b[5] - a[5]) * f
            cands.append((s_ + 1, {"name": pretty(num, suffix, a[1]), "kind": "address_approx", "detail": where(a[2], lon, lat), "lon": lon, "lat": lat}))
        # the street, at least: the stretch of it in your ZIP / city (or nearest you), one per street name
        order, args = "", (core,)
        if anchor:
            k = math.cos(math.radians(anchor[1])) ** 2
            order, args = " ORDER BY (lon-?)*(lon-?)*? + (lat-?)*(lat-?)", (core, anchor[0], anchor[0], k, anchor[1], anchor[1])
        pts = con.execute("SELECT street, city, lon, lat FROM street_pts WHERE core=?" + order + " LIMIT 400", args).fetchall()
        for r in pts:
            cands.append((score(r[0], r[1], "", r[2], r[3]) + 2, {"name": title_street(r[0]), "kind": "street_only",
                          "detail": where(r[1], r[2], r[3]), "lon": r[2], "lat": r[3]}))
    cands.sort(key=lambda c: c[0])
    out = []
    for sc, r in cands:
        gap = 150 if r["kind"] == "address" else 2500     # one estimate / one street stretch per place
        if any(_dist((r["lon"], r["lat"]), (o["lon"], o["lat"])) < gap and (r["kind"] == o["kind"] == "address" or
               r["name"].split(" ", 1)[-1] == o["name"].split(" ", 1)[-1] or r["kind"] != "address") for o in out):
            continue
        if out and r["kind"] != "address" and (sc > cands[0][0] + 6 or sum(o["kind"] == r["kind"] for o in out) >= 2):
            continue                                     # a couple of estimates / street stretches, not a page of them
        out.append(r)
        if len(out) >= limit: break
    return out

# ------------------------------------------------------------------ search helpers (hu-bridge)
def _fts_any(words):
    """FTS query matching any of the words (and words starting like them: 'lonestar' finds 'Lone Star')."""
    ws = [re.sub(r"[^0-9a-z]", "", w.lower()) for w in words]
    ws = sorted({w[:3] for w in ws if len(w) >= 3 and w not in TYPES and w not in DIRS})
    return " OR ".join(f'"{w}"*' for w in ws)

def _sort_places(rows, want, anchor):
    rows.sort(key=lambda r: (state_of(r[3], r[4]) != want, _dist(anchor, (r[3], r[4])) if anchor else 0))
    return rows

def place_anchor(con, q, near=None, zips=None):
    """For a name search ("walmart conroe", "starbucks 77301"): (where to search first - the city / ZIP
    typed at the end, else near you -, the state wanted, the words left for the name or None)."""
    want = query_state(q) or DEFAULT_STATE
    zm = re.search(r"\b(\d{5})\b", q)
    w = norm_city(re.sub(r"\b\d{5}\b", " ", q)).split()
    w2, st = strip_state(w)
    if st: w = w2
    for n in (2, 1):                                     # "college station" before "station"
        if len(w) > n:
            c = city_centre(con, w[-n:], want, near)
            if c: return c, want, w[:-n]
    z = zm and zip_centre(con, zm.group(1), zips)
    if z: return z, want, w
    return near, want, (w if st else None)

def streets_by_name(con, q, near=None, zips=None):
    """'1234 Smith Rd, Conroe TX' with no house-number match (or search data without addresses):
    the street by name, in the city / ZIP typed first, then Texas, then nearest."""
    p = parse_query(q)
    if not p: return []
    num, suffix, street, place_t, zipc = p
    want = query_state(q) or DEFAULT_STATE
    rest = re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", re.sub(r"^\s*\d+\w?\s+", "", q)).replace(",", " ")
    words = [re.sub(r"[^0-9A-Za-zÀ-ɏ]", "", w) for w in rest.split()]
    words = strip_state([w for w in words if w])[0] if words else []
    anchor = find_area(con, place_t, zipc, want, near, zips)
    for cut in range(len(words), 0, -1):
        if not anchor and cut < len(words):              # "smith rd conroe": the leftover words may be the city
            anchor = city_centre(con, [w.lower() for w in words[cut:]], want, near)
        try:
            st = con.execute("SELECT name, kind, detail, lon, lat FROM places WHERE places MATCH ? AND kind='street' LIMIT 3000",
                             (" ".join(f'"{w}"*' for w in words[:cut]),)).fetchall()
        except sqlite3.Error:
            st = []
        if st:
            _sort_places(st, want, anchor or near)
            return [{"name": r[0], "kind": "street_only", "detail": r[2] or title_place("", r[3], r[4], want), "lon": r[3], "lat": r[4]} for r in st[:3]]
    return []

def suggest(con, q, near=None, zips=None, limit=6):
    """Nothing found: options to pick from - similarly spelled streets (with your house number when it's
    there), similarly spelled places, and the city / ZIP you typed."""
    want = query_state(q) or DEFAULT_STATE
    out, seen = [], set()
    def add(r):
        key = (r["name"].lower(), round(r["lon"], 1), round(r["lat"], 1))
        if key in seen or len(out) >= limit: return
        seen.add(key); out.append({**r, "option": True})
    p = parse_query(q)
    if p:
        num, suffix, street, place_t, zipc = p
        anchor = find_area(con, place_t, zipc, want, near, zips)
        typed = core_street(street)
        fq = _fts_any(street.split())
        cands = []
        if fq:
            try: cands = con.execute("SELECT name, kind, detail, lon, lat FROM places WHERE places MATCH ? AND kind='street' LIMIT 5000", (fq,)).fetchall()
            except sqlite3.Error: cands = []
        sims = {}
        for r in cands:
            c = core_street(r[0])
            if c not in sims: sims[c] = difflib.SequenceMatcher(None, typed, c).ratio()
        cands = [r for r in cands if sims[core_street(r[0])] >= 0.6]
        cands.sort(key=lambda r: (-round(sims[core_street(r[0])], 1), state_of(r[3], r[4]) != want,
                                  _dist(anchor or near, (r[3], r[4])) if (anchor or near) else 0))
        tried = set()
        for r in cands:
            if r[0].lower() in tried: continue
            tried.add(r[0].lower())
            where = ", ".join(x for x in (" ".join(place_t), zipc) if x)
            hit = lookup(con, f"{num}{suffix} {r[0]}" + (f", {where}" if where else ""), near, 1, zips) if has_addresses(con) else []
            add(hit[0] if hit else {"name": r[0], "kind": "street_only", "detail": r[2] or title_place("", r[3], r[4], want), "lon": r[3], "lat": r[4]})
            if len(tried) >= 4: break
        if len(tried) < 4 and typed and has_addresses(con):     # typos in the first letters ("Mian St"): same first letter
            cores = [r[0] for r in con.execute("SELECT DISTINCT core FROM street_pts WHERE core >= ? AND core < ?",
                                               (typed[0], chr(ord(typed[0]) + 1)))]
            where = ", ".join(x for x in (" ".join(place_t), zipc) if x)
            for c in difflib.get_close_matches(typed, [c for c in cores if abs(len(c) - len(typed)) <= 2], n=4, cutoff=0.6):
                hit = lookup(con, f"{num}{suffix} {c}" + (f", {where}" if where else ""), near, 1, zips)
                if hit and hit[0]["name"].lower() not in tried: tried.add(hit[0]["name"].lower()); add(hit[0])
        if anchor:
            add({"name": " ".join(x for x in (" ".join(place_t).title(), zipc) if x), "kind": "area", "detail": "", "lon": anchor[0], "lat": anchor[1]})
    else:
        fq = _fts_any(q.replace(",", " ").split())
        rows = []
        if fq:
            try: rows = con.execute("SELECT name, kind, detail, lon, lat FROM places WHERE places MATCH ? LIMIT 5000", (fq,)).fetchall()
            except sqlite3.Error: rows = []
        ql = norm_city(q)
        sim = lambda n: difflib.SequenceMatcher(None, ql, norm_city(n)).ratio()
        rows = [(round(sim(r[0]), 1),) + tuple(r) for r in rows]
        rows = [r for r in rows if r[0] >= 0.5]
        rows.sort(key=lambda r: (-r[0], state_of(r[4], r[5]) != want, _dist(near, (r[4], r[5])) if near else 0))
        for r in rows:
            add({"name": r[1], "kind": r[2], "detail": r[3], "lon": r[4], "lat": r[5]})
    return out
