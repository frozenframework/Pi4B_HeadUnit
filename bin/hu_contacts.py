"""
Contacts' addresses in the offline map search ("Sean" -> his house, with Go), from the phone's contact
list over Bluetooth.

CarPlay and Android Auto don't share contacts with the car (CarPlay sends only a video picture, calls
and now playing). Phones share them over Bluetooth with the Phone Book Access Profile (PBAP), the
same way car stereos show who's calling. So: the phone is paired with the head unit's Bluetooth (not
only the CarPlay adapter), and on the iPhone Settings > Bluetooth > (the head unit) > Sync Contacts is
on (iOS asks to allow it the first time).

How it works:
  - every 20 s, the phones connected to the head unit's Bluetooth that offer PBAP (BlueZ, system bus)
  - a phone that just connected (or whose contacts are over 6 hours old) is asked for its phonebook
    through BlueZ's obexd (session bus; busctl, so no extra Python packages), as vCards
  - only contacts with a street address are kept: name, label (Home / Work), the address and where it
    is on the map (found with the offline address search, bin/hu_addr.py). No phone numbers, notes or
    pictures. Saved per phone in USER_DIR/contacts/<phone>.json, readable only by the head-unit user,
    so search works before the phone connects; Settings > Maps > Forget removes them
  - search uses the contacts of the phones connected now, or of the phone connected last
"""
import json, os, quopri, re, shutil, sqlite3, subprocess, time, threading
from pathlib import Path

PBAP_UUID = "0000112f-0000-1000-8000-00805f9b34fb"     # Phonebook Access server (the phone's side)
REFRESH = 6 * 3600
RETRY = (60, 180, 600)

# ------------------------------------------------------------------ vCards (2.1 from older phones, 3.0)
def _unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"=\n", "", text) if "QUOTED-PRINTABLE" in text.upper() else text   # 2.1 soft line breaks
    return re.sub(r"\n[ \t]", "", text)

def _split(v, sep):
    out, cur, esc = [], "", False
    for ch in v:
        if esc: cur += {"n": "\n", "N": "\n"}.get(ch, ch); esc = False
        elif ch == "\\": esc = True
        elif ch == sep: out.append(cur); cur = ""
        else: cur += ch
    return out + [cur]

def parse_vcards(text):
    """[{name, addresses: [{label, street, city, state, zip, country}]}] from a vCard file."""
    cards, cur = [], None
    for line in _unfold(text).split("\n"):
        if ":" not in line: continue
        key, val = line.split(":", 1)
        parts = key.split(";"); prop = parts[0].split(".")[-1].upper()      # "item1.ADR" -> ADR
        params = [p.upper() for p in parts[1:]]
        if prop == "BEGIN" and val.strip().upper() == "VCARD": cur = {"fn": "", "n": "", "adr": []}; continue
        if cur is None: continue
        if prop == "END":
            name = cur["fn"] or " ".join(x for x in reversed(cur["n"]) if x).strip()
            if name and cur["adr"]: cards.append({"name": name, "addresses": cur["adr"]})
            cur = None; continue
        if any("QUOTED-PRINTABLE" in p for p in params):
            cs = next((p.split("=", 1)[1] for p in params if p.startswith("CHARSET=")), "UTF-8")
            try: val = quopri.decodestring(val.encode("latin-1", "replace")).decode(cs.lower(), "replace")
            except LookupError: val = quopri.decodestring(val.encode("latin-1", "replace")).decode("utf-8", "replace")
        if prop == "FN": cur["fn"] = _split(val, ";")[0].strip()
        elif prop == "N": cur["n"] = [x.strip() for x in _split(val, ";")[:2]]
        elif prop == "ADR":
            f = (_split(val, ";") + [""] * 7)[:7]       # PO box; extended; street; city; region; postal code; country
            street = (f[2] or "").strip().split("\n")[0].strip()
            if not street: continue
            types = {t for p in params for t in re.split(r"[=,]", p)} - {"TYPE", "PREF", "POSTAL", "PARCEL", "INTL", "DOM"}
            label = "home" if "HOME" in types else "work" if "WORK" in types else "other"
            cur["adr"].append({"label": label, "street": street, "city": f[3].strip(), "state": f[4].strip(),
                               "zip": f[5].strip(), "country": f[6].strip()})
    return cards

def address_text(a):
    return ", ".join(x for x in (a["street"], " ".join(y for y in (a["city"], a["state"], a["zip"]) if y)) if x)

# ------------------------------------------------------------------ BlueZ through busctl
class Bus:
    """busctl --json=short calls: the system bus for BlueZ (which phones are connected), the session bus
    for obexd (the phonebook transfer)."""
    def __init__(self, busctl="busctl"):
        self.busctl = busctl
    def _run(self, user, *args, timeout=60):
        cmd = [self.busctl] + (["--user"] if user else []) + ["--json=short"] + [str(a) for a in args]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0: raise RuntimeError((r.stderr or r.stdout).strip() or f"busctl failed ({r.returncode})")
        return json.loads(r.stdout)["data"] if r.stdout.strip() else None
    def devices(self):
        objs = self._run(False, "call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")[0]
        out = []
        for path, ifaces in objs.items():
            d = ifaces.get("org.bluez.Device1")
            if not d: continue
            v = lambda k, dflt=None: (d.get(k) or {}).get("data", dflt)
            out.append({"address": v("Address", ""), "name": v("Alias") or v("Name") or v("Address", ""),
                        "connected": bool(v("Connected", False)), "pbap": PBAP_UUID in [u.lower() for u in v("UUIDs", [])]})
        return out
    def pull_phonebook(self, address, timeout=120):
        """The phone's contacts as vCard text (PBAP over obexd)."""
        sess = self._run(True, "call", "org.bluez.obex", "/org/bluez/obex", "org.bluez.obex.Client1", "CreateSession",
                         "sa{sv}", address, 1, "Target", "s", "pbap", timeout=60)[0]
        fname = None
        try:
            self._run(True, "call", "org.bluez.obex", sess, "org.bluez.obex.PhonebookAccess1", "Select", "ss", "int", "pb")
            res = self._run(True, "call", "org.bluez.obex", sess, "org.bluez.obex.PhonebookAccess1", "PullAll", "sa{sv}", "",
                            2, "Format", "s", "vcard30", "Fields", "as", 4, "VERSION", "FN", "N", "ADR")
            transfer, props = res[0], res[1]
            fname = (props.get("Filename") or {}).get("data")
            t0, size = time.time(), -1
            while time.time() - t0 < timeout:
                try:
                    st = self._run(True, "get-property", "org.bluez.obex", transfer, "org.bluez.obex.Transfer1", "Status")
                except RuntimeError:                         # obexd drops the transfer once it's finished
                    st = "complete" if fname and os.path.exists(fname) else "error"
                if st == "complete": break
                if st == "error": raise RuntimeError("the phone stopped the transfer (allow contact sync on the phone)")
                time.sleep(0.5)
            else:
                raise RuntimeError("the phone didn't send its contacts in time")
            time.sleep(0.3)
            return Path(fname).read_text(encoding="utf-8", errors="replace")
        finally:
            if fname:
                try: os.remove(fname)                          # the full phonebook isn't left lying around
                except OSError: pass
            try: self._run(True, "call", "org.bluez.obex", "/org/bluez/obex", "org.bluez.obex.Client1", "RemoveSession", "o", sess)
            except Exception: pass

# ------------------------------------------------------------------ contacts store + search
def _slug(addr): return re.sub(r"[^0-9a-f]", "", addr.lower())

class Contacts:
    def __init__(self, store_dir, search_db, bus=None, log=None, lookup=None):
        self.dir = Path(store_dir); self.db = Path(search_db); self.bus = bus or Bus(); self.log = log
        self.lookup = lookup                                  # hu_addr.lookup (offline address search)
        self.state = {}                                       # address -> {state, error, next_try, tries, seen_connected}
        self.connected = []; self.lock = threading.Lock()
    def _info(self, *a):
        if self.log: self.log.info(*a)

    # saved per phone
    def _file(self, address): return self.dir / f"{_slug(address)}.json"
    def saved(self):
        out = []
        for f in sorted(self.dir.glob("*.json")) if self.dir.exists() else []:
            try: out.append(json.loads(f.read_text()))
            except (OSError, ValueError): pass
        return out
    def _save(self, data):
        self.dir.mkdir(parents=True, exist_ok=True); os.chmod(self.dir, 0o700)
        f = self._file(data["address"]); tmp = f.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh: json.dump(data, fh)
        os.replace(tmp, f)
    def forget(self, address=None):
        n = 0
        for d in self.saved():
            if address in (None, d["address"]):
                self._file(d["address"]).unlink(missing_ok=True); n += 1
                st = self.state.get(d["address"])             # not fetched again until the phone reconnects
                if st: st.update(state="forgotten", fresh=True)
        return n

    # where an address is, with the offline search
    def locate(self, a, cache):
        key = address_text(a)
        if key in cache: return cache[key]
        hit = None
        if self.lookup and self.db.exists() and re.match(r"^\s*\d", a["street"]):
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
            try:
                r = self.lookup(con, key, None, 1)
                if r and r[0]["kind"] != "address" and a["city"]:
                    # only an estimate or the street: it must be in the contact's town, not a same-named
                    # street somewhere else (the map data may not cover their town at all)
                    import hu_addr
                    want = hu_addr.STATE_CODES.get(a["state"].lower(), hu_addr.DEFAULT_STATE)
                    town = hu_addr.city_centre(con, hu_addr.norm_city(a["city"]).split(), want)
                    if not town or hu_addr._dist(town, (r[0]["lon"], r[0]["lat"])) > 30_000: r = []
                if r: hit = {"lon": r[0]["lon"], "lat": r[0]["lat"], "kind": r[0]["kind"]}
            except Exception as e:
                if self.log: self.log.warning("contacts: finding an address: %s", e)
            finally:
                con.close()
        cache[key] = hit
        return hit

    def refresh(self, dev):
        """Pull one phone's contacts, keep the ones with an address, place them on the map, save."""
        vcf = self.bus.pull_phonebook(dev["address"])
        cards = parse_vcards(vcf)
        old = next((d for d in self.saved() if d["address"] == dev["address"]), {})
        dbm = self.db.stat().st_mtime if self.db.exists() else 0
        cache = dict(old.get("places", {})) if old.get("search_db") == dbm else {}
        out = []
        for c in cards:
            addrs = []
            for a in c["addresses"]:
                hit = self.locate(a, cache)
                addrs.append({**a, "text": address_text(a), **({"lon": hit["lon"], "lat": hit["lat"], "found": hit["kind"]} if hit else {})})
            out.append({"name": c["name"], "addresses": addrs})
        data = {"address": dev["address"], "phone": dev["name"], "updated": time.time(), "count": len(out),
                "contacts": out, "search_db": dbm, "places": {k: v for k, v in cache.items()}}
        self._save(data)
        self._info("contacts: %d with an address from %s", len(out), dev["name"])
        return data

    def tick(self):
        """One check (run every ~20 s, off the event loop): fetch for phones that just connected."""
        try:
            devs = [d for d in self.bus.devices() if d["connected"] and d["pbap"]]
        except Exception as e:
            devs = []
            if self.log: self.log.debug("contacts: bluetooth: %s", e)
        self.connected = [d["address"] for d in devs]
        now = time.time()
        for addr in list(self.state):                         # disconnected: fetch again next time it connects
            if addr not in self.connected: self.state[addr]["fresh"] = False
        for d in devs:
            st = self.state.setdefault(d["address"], {"state": "new", "tries": 0, "next_try": 0, "fresh": False})
            saved = next((x for x in self.saved() if x["address"] == d["address"]), None)
            due = not st["fresh"] or (saved and now - saved["updated"] > REFRESH)
            if not due or now < st["next_try"]: continue
            st.update(state="syncing", phone=d["name"])
            try:
                self.refresh(d); st.update(state="ok", tries=0, fresh=True, error="")
            except Exception as e:
                st["tries"] += 1; st["next_try"] = now + RETRY[min(st["tries"] - 1, len(RETRY) - 1)]
                st.update(state="error", error=str(e)[:200])
                if self.log: self.log.warning("contacts from %s: %s", d["name"], e)

    def active(self):
        """Whose contacts search uses: the phones connected now, else the one connected last."""
        saved = self.saved()
        now = [d for d in saved if d["address"] in self.connected]
        return now or sorted(saved, key=lambda d: d.get("updated", 0))[-1:]

    def search(self, q, near=None, limit=6):
        words = [w for w in re.sub(r"[^\w ]+", " ", q.lower()).split() if w]
        if not words or words[0][0].isdigit(): return []
        out = []
        for ph in self.active():
            for c in ph["contacts"]:
                nw = re.sub(r"[^\w ]+", " ", c["name"].lower()).split()
                for a in c["addresses"]:
                    if "lon" not in a: continue
                    lw = nw + [a["label"]]
                    if all(any(x.startswith(w) for x in lw) for w in words):
                        out.append({"name": c["name"], "kind": "contact", "label": a["label"], "found": a.get("found"),
                                    "detail": a["text"], "lon": a["lon"], "lat": a["lat"]})
        def rank(r):
            first = r["name"].lower().startswith(words[0]); d = 0
            if near: d = (r["lon"] - near[0]) ** 2 + (r["lat"] - near[1]) ** 2
            return (not first, {"home": 0, "work": 1}.get(r["label"], 2), d)
        out.sort(key=rank)
        return out[:limit]

    def status(self):
        res = []
        gone = {a for a, st in self.state.items() if st.get("state") == "forgotten"}
        for d in self.saved():
            n_addr = sum(len(c["addresses"]) for c in d["contacts"])
            n_map = sum(1 for c in d["contacts"] for a in c["addresses"] if "lon" in a)
            st = self.state.get(d["address"], {})
            res.append({"address": d["address"], "phone": d["phone"], "contacts": d["count"], "addresses": n_addr, "on_map": n_map,
                        "updated": d["updated"], "connected": d["address"] in self.connected, "state": st.get("state", "saved"),
                        "error": st.get("error", "")})
        for addr, st in self.state.items():
            if addr not in gone and not any(r["address"] == addr for r in res):
                res.append({"address": addr, "phone": st.get("phone", addr), "contacts": 0, "addresses": 0, "on_map": 0, "updated": 0,
                            "connected": addr in self.connected, "state": st["state"], "error": st.get("error", "")})
        return res
