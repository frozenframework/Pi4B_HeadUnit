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

ERRORS = {"err_no_dbuslib": "a Python D-Bus library (python3-jeepney) is missing: connect to Wi-Fi and check for updates",
          "err_refused": "the phone said no: allow contact sync on the phone (iPhone: Settings > Bluetooth > (i) > Sync Contacts)",
          "err_obexd": "the Bluetooth contacts service (bluez-obexd) isn't installed or running",
          "err_no_answer": "the phone didn't answer: is it nearby, unlocked, and connected to the head unit?",
          "err_permission": "no permission to use Bluetooth yet (restart the head unit once after the update)",
          "err_dropped": "the phone closed the contacts connection each time (3 tries): unlock it, check Sync Contacts is on, and try again"}
def friendly(e):
    """busctl / obexd errors as a code Settings shows in words a driver can act on (else the message)."""
    m = str(e)
    if m == "err_no_dbuslib": return m
    if m.startswith("err_dropped"): return "err_dropped"
    for pat, code in (("ServiceUnknown|org.bluez.obex was not provided|not activatable", "err_obexd"),
                      ("Failed to connect to bus|AccessDenied|Permission denied", "err_permission"),
                      ("Not Allowed|Forbidden|Rejected|denied|stopped the transfer", "err_refused"),
                      ("Unable to find service record|Host is down|Connection refused|br-connection|Timeout|timed out|didn't send|Page Timeout", "err_no_answer")):
        if re.search(pat, m, re.I): return code
    return m[:200]

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
                        "connected": bool(v("Connected", False)), "paired": bool(v("Paired", False)),
                        "pbap": PBAP_UUID in [u.lower() for u in v("UUIDs", [])]})
        return out
    def obexd_available(self):
        """Is BlueZ's OBEX service (package bluez-obexd) on the session bus or startable there?"""
        names = []
        for m in ("ListNames", "ListActivatableNames"):
            try: names += self._run(True, "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", m, timeout=10)[0]
            except Exception: pass
        return "org.bluez.obex" in names

    RETRY_WAIT = 2
    def pull_phonebook(self, address, timeout=300, progress=lambda phase, done=0, total=0: None, tries=3):
        """The phone's contacts as vCard text (PBAP over obexd). progress(phase, done, total) along the way:
        connecting (the phone may ask to allow it) -> downloading (contacts received so far).

        Everything goes over ONE session-bus connection (ObexLink): obexd ends a session the moment the
        program that created it disconnects, so separate busctl calls can't do this.
        Only CreateSession -> Select -> PullAll: no GetSize first - an iPhone may close the contacts
        connection after it (the session then vanishes: "UnknownObject ... PullAll"). If the phone closes
        the connection anyway (it also does when it has just asked "Allow ... to sync contacts?"), it's
        tried again with a new session, up to `tries` times."""
        last = None
        for attempt in range(1, tries + 1):
            progress("connecting", attempt - 1, 0)
            try:
                return self._pull_once(address, timeout, progress)
            except SessionGone as e:
                last = e
                if attempt < tries: time.sleep(self.RETRY_WAIT * attempt)
        raise RuntimeError(f"err_dropped ({last})")

    def _pull_once(self, address, timeout, progress):
        link = ObexLink()
        sess = fname = None; step = "CreateSession"
        PB = "org.bluez.obex.PhonebookAccess1"
        def call(path, iface, method, *a, **k):
            nonlocal step; step = method
            try: return link.call(path, iface, method, *a, **k)
            except RuntimeError as e:                         # the session (or the transfer) is gone: the phone hung up
                if re.search(r"UnknownObject|UnknownMethod|UnknownInterface|Disconnected|not connected|Connection reset", str(e), re.I):
                    raise SessionGone(f"after {step}: {e}") from None
                raise
        try:
            sess = call("/org/bluez/obex", "org.bluez.obex.Client1", "CreateSession", "sa{sv}",
                        (address, {"Target": ("s", "pbap")}), timeout=90)[0]
            call(sess, PB, "Select", "ss", ("int", "pb"))
            progress("downloading", 0, 0)
            transfer, props = call(sess, PB, "PullAll", "sa{sv}",
                                   ("", {"Format": ("s", "vcard30"), "Fields": ("as", ["VERSION", "FN", "N", "ADR"])}))
            fname = link.plain(props.get("Filename"))
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    st = link.plain(link.call(transfer, "org.freedesktop.DBus.Properties", "Get", "ss",
                                              ("org.bluez.obex.Transfer1", "Status"))[0])
                except RuntimeError:                         # obexd drops the transfer once it's finished
                    st = "complete" if fname and os.path.exists(fname) and os.path.getsize(fname) > 0 else "gone"
                if st == "complete": break
                if st == "gone": raise SessionGone("during the transfer")
                if st == "error": raise RuntimeError("the phone stopped the transfer (allow contact sync on the phone)")
                try:
                    with open(fname, "rb") as fh: got = fh.read().upper().count(b"BEGIN:VCARD")
                    progress("downloading", got, 0)
                except OSError: pass
                time.sleep(0.5)
            else:
                raise RuntimeError("the phone didn't send its contacts in time")
            time.sleep(0.3)
            return Path(fname).read_text(encoding="utf-8", errors="replace")
        finally:
            if fname:
                try: os.remove(fname)                          # the full phonebook isn't left lying around
                except OSError: pass
            if sess:
                try: link.call("/org/bluez/obex", "org.bluez.obex.Client1", "RemoveSession", "o", (sess,), timeout=10)
                except Exception: pass
            link.close()

class SessionGone(Exception):
    """The phone closed the contacts connection part way (obexd removed the session)."""

class ObexLink:
    """One connection to the session bus for a whole phonebook transfer (jeepney, pure Python; or
    dbus-python). Arguments are given jeepney-style: a variant is (signature, value)."""
    SERVICE = "org.bluez.obex"
    def __init__(self, address=None):
        address = address or os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path=/run/user/{os.getuid()}/bus"
        try:
            from jeepney.io.blocking import open_dbus_connection
            self.kind, self.conn = "jeepney", open_dbus_connection(bus=address)
        except ImportError:
            try:
                import dbus
            except ImportError:
                raise RuntimeError("err_no_dbuslib")
            self.kind, self.conn = "dbus", dbus.bus.BusConnection(address)

    def call(self, path, iface, method, sig="", args=(), timeout=60):
        if self.kind == "jeepney":
            from jeepney import new_method_call, DBusAddress, MessageType, HeaderFields
            msg = new_method_call(DBusAddress(path, bus_name=self.SERVICE, interface=iface), method, sig or None, tuple(args))
            rep = self.conn.send_and_get_reply(msg, timeout=timeout)
            if rep.header.message_type == MessageType.error:
                name = rep.header.fields.get(HeaderFields.error_name, "error")
                raise RuntimeError(f"{name}: {rep.body[0] if rep.body else ''}")
            return list(rep.body)
        import dbus
        def conv(a):
            if isinstance(a, dict):
                return dbus.Dictionary({k: (dbus.Array(v[1], signature="s") if v[0] == "as" else v[1]) for k, v in a.items()}, signature="sv")
            return a
        obj = self.conn.get_object(self.SERVICE, path, introspect=False)
        try:
            r = getattr(dbus.Interface(obj, iface), method)(*[conv(a) for a in args], timeout=timeout)
        except dbus.exceptions.DBusException as e:
            raise RuntimeError(f"{e.get_dbus_name()}: {e.get_dbus_message()}")
        return list(r) if isinstance(r, tuple) else [r]

    @staticmethod
    def plain(v):
        """A variant's value: jeepney gives (signature, value), dbus-python the value itself."""
        return v[1] if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) else (str(v) if v is not None else None)

    def close(self):
        try: self.conn.close()
        except Exception: pass

# ------------------------------------------------------------------ contacts store + search
def _slug(addr): return re.sub(r"[^0-9a-f]", "", addr.lower())

class Contacts:
    def __init__(self, store_dir, search_db, bus=None, log=None, lookup=None):
        self.dir = Path(store_dir); self.db = Path(search_db); self.bus = bus or Bus(); self.log = log
        self.lookup = lookup                                  # hu_addr.lookup (offline address search)
        self.state = {}                                       # address -> {state, error, next_try, tries, seen_connected}
        self.connected = []; self.lock = threading.Lock()
        self.progress = {}                                    # address -> {phase, done, total, phone, ...} (Settings shows a bar)
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

    def _progress(self, dev, phase, done=0, total=0, **kw):
        p = self.progress.get(dev["address"], {})
        if phase == "connecting": p = {"started": time.time()}
        p.update(phone=dev["name"], address=dev["address"], phase=phase, done=done, total=total, at=time.time(), **kw)
        self.progress[dev["address"]] = p

    def refresh(self, dev):
        """Pull one phone's contacts, keep the ones with an address, place them on the map, save."""
        try:
            vcf = self.bus.pull_phonebook(dev["address"], progress=lambda ph, d=0, t=0: self._progress(dev, ph, d, t))
        except TypeError:                                    # a bus without progress reports
            self._progress(dev, "connecting"); vcf = self.bus.pull_phonebook(dev["address"])
        except Exception as e:
            self._progress(dev, "error", error=friendly(e)); raise
        cards = parse_vcards(vcf)
        n_addr = sum(len(c["addresses"]) for c in cards); done = 0
        self._progress(dev, "placing", 0, n_addr, received=vcf.upper().count("BEGIN:VCARD"))
        old = next((d for d in self.saved() if d["address"] == dev["address"]), {})
        dbm = self.db.stat().st_mtime if self.db.exists() else 0
        cache = dict(old.get("places", {})) if old.get("search_db") == dbm else {}
        out = []
        for c in cards:
            addrs = []
            for a in c["addresses"]:
                hit = self.locate(a, cache); done += 1
                if done % 5 == 0: self._progress(dev, "placing", done, n_addr)
                addrs.append({**a, "text": address_text(a), **({"lon": hit["lon"], "lat": hit["lat"], "found": hit["kind"]} if hit else {})})
            out.append({"name": c["name"], "addresses": addrs})
        data = {"address": dev["address"], "phone": dev["name"], "updated": time.time(), "count": len(out),
                "contacts": out, "search_db": dbm, "places": {k: v for k, v in cache.items()}}
        self._save(data)
        self._progress(dev, "done", len(out), len(out), on_map=sum(1 for c in out for a in c["addresses"] if "lon" in a),
                       received=self.progress.get(dev["address"], {}).get("received", 0))
        self._info("contacts: %d with an address from %s", len(out), dev["name"])
        return data

    # ---------------------------------------------------------------- Settings > Developer: check + import now
    def diagnose(self):
        """What stands between the phone and the address book, step by step: [{ok, key, vars}]."""
        import grp, pwd, shutil as sh
        out = []
        add = lambda ok, key, **v: out.append({"ok": ok, "key": key, "vars": v})
        if not sh.which(self.bus.busctl):
            add(False, "busctl_missing"); return out
        try:
            devs = self.bus.devices(); add(True, "bt_ok")
        except Exception as e:
            add(False, "bt_off", error=friendly(e)); return out
        try:                                                  # BlueZ only talks to the "bluetooth" group
            g = grp.getgrnam("bluetooth")
            if g.gr_gid not in os.getgroups() and g.gr_gid != os.getgid():
                add(False, "bt_group_reboot" if pwd.getpwuid(os.getuid()).pw_name in g.gr_mem else "bt_group")
        except (KeyError, OSError):
            pass
        add(self.bus.obexd_available(), "obexd")
        try: import jeepney                                   # noqa: F401 - the transfer's D-Bus connection
        except ImportError:
            try: import dbus                                  # noqa: F401
            except ImportError: add(False, "no_dbuslib")
        phones = [d for d in devs if d["paired"] or d["connected"]]
        if not phones: add(False, "no_phones")
        for d in phones:
            key = "phone_ready" if d["connected"] and d["pbap"] else "phone_no_pbap" if d["connected"] else "phone_not_connected"
            add(key == "phone_ready", key, phone=d["name"])
        return out

    def force_import(self):
        with self.lock: return self._force_import()

    def _force_import(self):
        """Import now from every paired phone that offers contacts (connected first), whatever was
        fetched before. Returns the check list with each phone's result."""
        report = self.diagnose()
        if not all(r["ok"] for r in report if r["key"] in ("busctl_missing", "bt_off")):
            return report
        try: devs = [d for d in self.bus.devices() if (d["paired"] or d["connected"]) and (d["pbap"] or d["connected"])]
        except Exception: devs = []
        devs.sort(key=lambda d: not d["connected"])
        for d in devs:
            st = self.state.setdefault(d["address"], {"state": "new", "tries": 0, "next_try": 0, "fresh": False})
            st.update(state="syncing", phone=d["name"])
            try:
                data = self.refresh(d); st.update(state="ok", tries=0, fresh=True, error="", next_try=0)
                p = self.progress.get(d["address"], {})
                report.append({"ok": True, "key": "imported", "vars": {"phone": d["name"], "n": data["count"], "m": p.get("on_map", 0),
                                                                     "received": p.get("received", 0)}})
            except Exception as e:
                st.update(state="error", error=friendly(e))
                report.append({"ok": False, "key": "import_failed", "vars": {"phone": d["name"], "error": friendly(e), "detail": str(e)[:300]}})
        return report

    def tick(self):
        """One check (run every ~20 s, off the event loop): fetch for phones that just connected."""
        if not self.lock.acquire(blocking=False): return     # an import from Settings is running
        try: self._tick()
        finally: self.lock.release()

    def _tick(self):
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
                st.update(state="error", error=friendly(e))
                if self.log: self.log.warning("contacts from %s: %s", d["name"], e)

    def active(self):
        """Whose contacts search uses: the phones connected now, else the one connected last."""
        saved = self.saved()
        now = [d for d in saved if d["address"] in self.connected]
        return now or sorted(saved, key=lambda d: d.get("updated", 0))[-1:]

    def book(self):
        """The address book screen: everyone with an address from the phones search uses, A to Z
        (one entry per name; addresses not found on the map are listed but can't be picked)."""
        phones = self.active(); people = {}
        for ph in phones:
            for c in ph["contacts"]:
                p = people.setdefault(c["name"].lower(), {"name": c["name"], "addresses": [], "src": ph["address"], "phone": ph.get("phone", "")})
                for a in c["addresses"]:
                    if any(x["text"] == a["text"] for x in p["addresses"]): continue
                    p["addresses"].append({k: a[k] for k in ("label", "text", "lon", "lat", "found") if k in a})
        for p in people.values():
            p["addresses"].sort(key=lambda a: ("lon" not in a, {"home": 0, "work": 1}.get(a["label"], 2)))
        out = sorted(people.values(), key=lambda p: (not p["name"][:1].isalpha(), p["name"].lower()))
        return {"phones": [ph.get("phone", "") for ph in phones], "contacts": out}

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


if __name__ == "__main__":                 # over SSH:  python3 /opt/headunit/bin/hu_contacts.py [--import]
    import sys, logging
    sys.path.insert(0, str(Path(__file__).resolve().parent)); import hu_addr
    TEXT = {"busctl_missing": "busctl (systemd) not found", "bt_ok": "Bluetooth service reachable",
            "bt_off": "Bluetooth service not reachable: {error}", "bt_group": "this user isn't in the bluetooth group (run the update setup again)",
            "bt_group_reboot": "added to the bluetooth group: restart the head unit once", "obexd": "Bluetooth contacts service (bluez-obexd)",
            "no_phones": "no paired phone: pair the phone with the head unit's Bluetooth (not only the CarPlay adapter)",
            "phone_ready": "{phone}: connected, offers contacts", "phone_no_pbap": "{phone}: connected but doesn't offer contacts (turn on Sync Contacts on the phone)",
            "phone_not_connected": "{phone}: paired, not connected now", "imported": "{phone}: {received} contacts received, {n} with an address, {m} on the map",
            "import_failed": "{phone}: {error}", "no_dbuslib": ERRORS["err_no_dbuslib"]}
    def words(r):
        v = dict(r["vars"]); v["error"] = ERRORS.get(v.get("error"), v.get("error", ""))
        return TEXT.get(r["key"] if r["key"] != "obexd" or r["ok"] else "obexd", r["key"]).format(**v)
    home = Path.home() / ".config" / "headunit"
    c = Contacts(home / "contacts", Path("/var/lib/headunit/search/search.sqlite"), lookup=hu_addr.lookup)
    def show(r): print(("  ok   " if r["ok"] else "  FAIL ") + words(r))
    if "--import" in sys.argv:
        stop = threading.Event()
        def bar():
            while not stop.wait(1):
                for p in c.progress.values():
                    print(f"\r  {p['phone']}: {p['phase']} {p.get('done', 0)}/{p.get('total') or '?'}      ", end="", flush=True)
        threading.Thread(target=bar, daemon=True).start()
        rep = c.force_import(); stop.set(); print()
    else:
        rep = c.diagnose()
    for r in rep: show(r)
