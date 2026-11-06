"""Contacts' addresses in map search: a phone on the head unit's Bluetooth shares its contacts (PBAP
through BlueZ's obexd, a stand-in busctl here); only contacts with an address are kept (no phone
numbers), placed on the map with the offline address search, saved readable only by the head-unit
user, and found by name in the map search ("sean" -> Sean's house) - also after the phone has gone,
until Forget. A refused sync is retried later; vCards from iPhones (3.0) and older phones (2.1) read."""
import importlib.util, json, os, socket, sqlite3, stat, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import hu_contacts, hu_addr
from hu_contacts import parse_vcards

# 1) vCards
IPHONE = """BEGIN:VCARD\r\nVERSION:3.0\r\nN:Example;Sean;;;\r\nFN:Sean Example\r\nTEL;type=CELL:+1 936 555 0100\r
item1.ADR;type=HOME;type=pref:;;140 N Main St\\nApt 4;Conroe;TX;77301;United States\r\nitem1.X-ABADR:us\r
ADR;type=WORK:;;4500 Hwy 105 W;Conroe;TX;77304;\r\nEND:VCARD\r
BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No Address Person\r\nTEL:555\r\nEND:VCARD\r
BEGIN:VCARD\r\nVERSION:3.0\r\nN:Shop;Joe's\\, Tacos;;;\r\nADR;TYPE=WORK:;;120 Main St;Houston;TX;;\r\nEND:VCARD\r\n"""
OLD = "BEGIN:VCARD\nVERSION:2.1\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Mu=C3=B1oz;Jos=C3=A9\nADR;HOME:;;300 N Main St;Con\n roe;TX;77301;\nEND:VCARD\n"
c = parse_vcards(IPHONE)
assert [x["name"] for x in c] == ["Sean Example", "Joe's, Tacos Shop"], c
assert c[0]["addresses"][0] == {"label": "home", "street": "140 N Main St", "city": "Conroe", "state": "TX", "zip": "77301", "country": "United States"}, c[0]
assert c[0]["addresses"][1]["label"] == "work"
o = parse_vcards(OLD); assert o[0]["name"] == "José Muñoz" and o[0]["addresses"][0]["city"] == "Conroe", o
print("1) vCards: iPhone 3.0 (item1.ADR, escapes, Home/Work) and 2.1 (quoted-printable, folded lines); no address = skipped")

# a small search data set to place the addresses
import osmium
T = Path(tempfile.mkdtemp()); pbf = T / "t.osm.pbf"
w = osmium.SimpleWriter(str(pbf)); Nd = osmium.osm.mutable.Node; nid = 1
for n, lat in ((100, 30.3100), (140, 30.3120), (300, 30.3200)):
    w.add_node(Nd(id=nid, location=(-95.4560, lat), tags={"addr:housenumber": str(n), "addr:street": "North Main Street", "addr:city": "Conroe", "addr:postcode": "77301"})); nid += 1
w.add_node(Nd(id=nid, location=(-95.4560, 30.3119), tags={"place": "city", "name": "Conroe"})); nid += 1
w.close()
spec = importlib.util.spec_from_file_location("br", ROOT / "maps/build-routing.py"); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
out = T / "maps"; out.mkdir(); (T / "w").mkdir()
br.build_search([pbf], out, addresses=True, work=T / "w")
data = T / "data"; (data / "search").mkdir(parents=True); (out / "search.sqlite").replace(data / "search" / "search.sqlite")
DB = data / "search" / "search.sqlite"

# 2) a stand-in busctl: BlueZ (system bus) and obexd (session bus)
FAKE = T / "busctl"; LOG = T / "busctl.log"; VCF = T / "phonebook.vcf"; VCF.write_text(IPHONE)
FAKE.write_text(f"""#!{sys.executable}
import json, sys, os, shutil
a = sys.argv[1:]; open({str(LOG)!r}, "a").write(json.dumps(a) + "\\n")
user = "--user" in a; a = [x for x in a if x not in ("--user", "--json=short")]
st = {str(T / "fake.json")!r}; s = json.load(open(st)) if os.path.exists(st) else {{}}
out = lambda t, d: print(json.dumps({{"type": t, "data": d}}))
if not user and a[:2] == ["call", "org.bluez"]:
    dev = {{"Address": {{"type": "s", "data": "AA:BB:CC:DD:EE:FF"}}, "Alias": {{"type": "s", "data": "Robert's iPhone"}},
           "Connected": {{"type": "b", "data": s.get("connected", True)}}, "Paired": {{"type": "b", "data": True}},
           "UUIDs": {{"type": "as", "data": ["0000112F-0000-1000-8000-00805F9B34FB", "0000111e-0000-1000-8000-00805f9b34fb"]}}}}
    out("a{{oa{{sa{{sv}}}}}}", [{{"/org/bluez/hci0/dev_AA": {{"org.bluez.Device1": dev}}, "/org/bluez/hci0": {{"org.bluez.Adapter1": {{}}}}}}])
elif a[3:5] == ["org.bluez.obex.Client1", "CreateSession"]:
    if s.get("refuse"): print("Call failed: Not Allowed", file=sys.stderr); sys.exit(1)
    out("o", ["/org/bluez/obex/client/session7"])
elif a[4:5] == ["GetSize"]:
    out("q", [3])
elif a[3:5] == ["org.freedesktop.DBus", "ListNames"]:
    out("as", [[":1.1"] + (["org.bluez.obex"] if not s.get("no_obexd") else [])])
elif a[4:5] == ["PullAll"]:
    f = {str(T / "obexd-tmp.vcf")!r}; shutil.copy({str(VCF)!r}, f)
    out("oa{{sv}}", ["/org/bluez/obex/client/session7/transfer0", {{"Filename": {{"type": "s", "data": f}}, "Size": {{"type": "t", "data": 0}}}}])
elif a[0] == "get-property":
    print("Unknown object", file=sys.stderr); sys.exit(1)       # obexd already dropped the finished transfer
""")
FAKE.chmod(0o755)
fake = lambda **k: (T / "fake.json").write_text(json.dumps(k))
fake()
# obexd itself on a private session bus (tools/tests/fake_obexd.py): ends a session when its creator disconnects
BUS = f"unix:path={T}/bus"
daemon = subprocess.Popen(["dbus-daemon", "--config-file=/usr/share/dbus-1/session.conf", f"--address={BUS}", "--nofork", "--print-address=1"],
                          stdout=subprocess.PIPE, text=True)
daemon.stdout.readline(); os.environ["DBUS_SESSION_BUS_ADDRESS"] = BUS
obexd = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "fake_obexd.py"), BUS, str(LOG), str(VCF),
                          str(T / "fake.json"), str(T / "obexd-tmp.vcf")], stdout=subprocess.PIPE, text=True)
assert obexd.stdout.readline().strip() == "ready"
import atexit; atexit.register(lambda: (obexd.terminate(), daemon.terminate()))
# the bug this guards against: each busctl call is its own connection, so the session was gone by Select
a = hu_contacts.ObexLink(); sp = a.call("/org/bluez/obex", "org.bluez.obex.Client1", "CreateSession", "sa{sv}", ("AA", {"Target": ("s", "pbap")}))[0]
a.close(); time.sleep(0.3); b2 = hu_contacts.ObexLink()
try: b2.call(sp, "org.bluez.obex.PhonebookAccess1", "Select", "ss", ("int", "pb")); raise AssertionError("session should be gone")
except RuntimeError as e: assert "doesn't exist" in str(e), e
b2.close(); LOG.write_text("")
store = T / "user" / "contacts"
ct = hu_contacts.Contacts(store, DB, bus=hu_contacts.Bus(str(FAKE)), lookup=hu_addr.lookup)
ct.tick()
calls = [json.loads(l) for l in LOG.read_text().splitlines()]
pull = [c for c in calls if "PullAll" in c][0]
assert pull[-7:] == ["Format", "s", "vcard30", "Fields", "as", "4", "VERSION"][:7] or "ADR" in pull, pull
assert any("RemoveSession" in c for c in calls) and not (T / "obexd-tmp.vcf").exists(), "session closed, phonebook file removed"
f = store / "aabbccddeeff.json"
assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(store.stat().st_mode) == 0o700
saved = f.read_text(); assert "555" not in saved and "No Address" not in saved, "only names and addresses are kept"
d = json.loads(saved); assert d["count"] == 2 and d["phone"] == "Robert's iPhone" and "lon" not in d["contacts"][1]["addresses"][0]
sean = d["contacts"][0]["addresses"]
assert sean[0]["found"] == "address" and abs(sean[0]["lat"] - 30.3120) < 1e-6, sean
assert [c[1] for c in calls if c[0] == "obex"][:4] == ["CreateSession", "Select", "PullAll", "Get"], ("no GetSize: an iPhone hangs up after it", calls)
print("2) phonebook pulled through obexd on one connection, session closed and the file removed; saved 0600 with names + addresses only")

r = ct.search("sean"); assert r[0]["name"] == "Sean Example" and r[0]["label"] == "home" and r[0]["kind"] == "contact", r
assert ct.search("sea ex")[0]["name"] == "Sean Example" and ct.search("sean home")[0]["label"] == "home"
assert ct.search("joe") == [], "Houston isn't in this map data: not placed on a Main St in Conroe"
assert ct.search("140 n main") == [] and ct.search("zed") == []
bk = ct.book(); assert bk["phones"] == ["Robert's iPhone"] and [c["name"] for c in bk["contacts"]] == ["Joe's, Tacos Shop", "Sean Example"], bk
sa = bk["contacts"][1]["addresses"]; assert sa[0]["label"] == "home" and "lon" in sa[0] and "lon" not in sa[1], ("on the map first", sa)
assert "lon" not in bk["contacts"][0]["addresses"][0], "listed, but not on the map"
n = len(LOG.read_text().splitlines()); ct.tick()
assert not any("PullAll" in l for l in LOG.read_text().splitlines()[n:]), "not fetched again while it stays connected"
fake(connected=False); ct.tick(); assert ct.search("sean"), "saved: still found after the phone disconnects"
VCF.write_text(IPHONE.replace("Sean Example", "Sean Q Example")); fake(); ct.tick()
assert ct.search("sean")[0]["name"] == "Sean Q Example", "fetched again when it reconnects"
print("3) found by name / part of a name / 'home'; address book A-Z; fetched once per connection; still there after disconnecting")

# a refused sync (iPhone: Sync Contacts off, or not allowed yet) is retried later, not every 20 s
ct2 = hu_contacts.Contacts(T / "user2", DB, bus=hu_contacts.Bus(str(FAKE)), lookup=hu_addr.lookup)
fake(refuse=True); ct2.tick(); s0 = ct2.status()[0]
assert s0["state"] == "error" and s0["error"] == "err_refused" and ct2.search("sean") == []
n = len(LOG.read_text().splitlines()); ct2.tick()
assert not any("CreateSession" in l for l in LOG.read_text().splitlines()[n:]), "waits before asking again"
fake(); ct2.state["AA:BB:CC:DD:EE:FF"]["next_try"] = 0; ct2.tick(); assert ct2.status()[0]["state"] == "ok"
assert ct2.forget() == 1 and ct2.search("sean") == [] and not list((T / "user2").glob("*.json"))
print("4) refused sync retried later; Forget removes the saved contacts")

# 6) progress, the step-by-step check and Import now (Settings > Developer)
p = ct.progress["AA:BB:CC:DD:EE:FF"]
assert p["phase"] == "done" and p["received"] == 3 and p["done"] == 2 and p["on_map"] >= 1 and p["phone"] == "Robert's iPhone", p
phases = []
class Spy(hu_contacts.Bus):
    def pull_phonebook(self, address, **k):
        cb = k["progress"]; return super().pull_phonebook(address, progress=lambda ph, d=0, t=0: (phases.append((ph, d, t)), cb(ph, d, t)))
ct3 = hu_contacts.Contacts(T / "user3", DB, bus=Spy(str(FAKE)), lookup=hu_addr.lookup)
rep3 = ct3.force_import()
assert phases[0][0] == "connecting" and ("downloading", 0, 0) in phases, phases
keys = [(r["ok"], r["key"]) for r in rep3]
assert keys == [(True, "bt_ok"), (True, "obexd"), (True, "phone_ready"), (True, "imported")] or \
       [k for k in keys if k[1] != "bt_group" and k[1] != "bt_group_reboot"] == [(True, "bt_ok"), (True, "obexd"), (True, "phone_ready"), (True, "imported")], keys
assert rep3[-1]["vars"]["received"] == 3 and rep3[-1]["vars"]["n"] == 2
n = len(LOG.read_text().splitlines()); ct3.force_import()
assert any("PullAll" in l for l in LOG.read_text().splitlines()[n:]), "Import now fetches again even though it's fresh"
# the phone hangs up part way (as it does right after asking "Allow ... to sync contacts?"): a new session
hu_contacts.Bus.RETRY_WAIT = 0
fake(drop_after_select=2, drop_id=1); n = len(LOG.read_text().splitlines()); rep5 = ct3.force_import()
assert rep5[-1]["key"] == "imported", rep5
assert sum('"CreateSession"' in l for l in LOG.read_text().splitlines()[n:]) == 3, "third try worked"
fake(drop_after_select=9, drop_id=2); rep6 = ct3.force_import()
assert rep6[-1]["key"] == "import_failed" and rep6[-1]["vars"]["error"] == "err_dropped" and "after PullAll" in rep6[-1]["vars"]["detail"], rep6
fake(no_obexd=True, refuse=True); rep4 = ct3.force_import()
assert (False, "obexd") in [(r["ok"], r["key"]) for r in rep4] and rep4[-1]["key"] == "import_failed" and rep4[-1]["vars"]["error"] == "err_refused", rep4
assert ct3.progress["AA:BB:CC:DD:EE:FF"]["phase"] == "error"
fake(connected=False); assert [r["key"] for r in ct3.diagnose() if r["key"].startswith("phone")] == ["phone_not_connected"]
fake()
print("5) progress (connecting -> receiving n of total -> placing -> done); check lists each step; Import now; errors in plain words")

# 5) through the head unit's search and settings (bridge)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nBUSCTL={FAKE}\nCONTACTS_EVERY=0.3\n")
(T / "usage").mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(data), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T),
       "PATH": f"{T}:{os.environ['PATH']}"}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def get(p):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=10).read())
def post(p, body):
    return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode(),
                                                                   headers={"Content-Type": "application/json"}), timeout=10).read())
try:
    for _ in range(60):
        try: st = get("/api/contacts"); break
        except OSError: time.sleep(0.25)
    for _ in range(40):
        st = get("/api/contacts")
        if st["phones"] and st["phones"][0]["state"] == "ok": break
        time.sleep(0.25)
    assert st["progress"][0]["phase"] == "done"
    assert post("/api/contacts", {"op": "import"})["started"]
    for _ in range(40):
        st = get("/api/contacts")
        if not st["importing"] and st["report"]: break
        time.sleep(0.25)
    assert st["report"][-1]["key"] == "imported" and st["report"][-1]["ok"], st["report"]
    assert get("/api/status")["contactsSync"]["phase"] == "done", "the map's progress chip"
    assert post("/api/contacts", {"op": "check"})["report"][0]["key"] == "bt_ok"
    p = st["phones"][0]; assert p["phone"] == "Robert's iPhone" and p["contacts"] == 2 and p["on_map"] == 1 and p["connected"], st
    assert [c["name"] for c in get("/api/contacts/book")["contacts"]] == ["Joe's, Tacos Shop", "Sean Q Example"]
    r = get("/api/search?q=" + urllib.parse.quote("sean") + "&near=-95.45,30.31")
    assert r[0]["kind"] == "contact" and r[0]["name"] == "Sean Q Example" and abs(r[0]["lat"] - 30.312) < 1e-6, r
    # the whole phonebook goes to the Address book on every connection - not into Places; a starred contact
    # (at most 3) shows in Places and moves when the phone's address changes
    assert not any(p["label"] == "contact" for p in get("/api/places")["places"]), "no contacts in Places by themselves"
    book = get("/api/contacts/book")["contacts"]; sean = next(c for c in book if c["name"] == "Sean Q Example")
    assert sean["src"] == "AA:BB:CC:DD:EE:FF" and sean["phone"] == "Robert's iPhone"
    a0 = sean["addresses"][0]
    r = post("/api/places", {"op": "fav_contact", "name": sean["name"], "ctype": a0["label"], "sub": a0["text"],
                             "lnglat": [a0["lon"], a0["lat"]], "src": sean["src"], "phone": sean["phone"]})
    pl = [p for p in r["places"] if p["label"] == "contact"]
    assert [(p["name"], p["ctype"]) for p in pl] == [("Sean Q Example", "home")] and abs(pl[0]["lnglat"][1] - 30.312) < 1e-6, pl
    VCF.write_text(IPHONE.replace("Sean Example", "Sean Q Example").replace("140 N Main St", "300 N Main St"))
    fake(connected=False); time.sleep(0.8); fake(); time.sleep(1.5)                 # the phone reconnects
    pl = [p for p in get("/api/places")["places"] if p["label"] == "contact"]
    assert len(pl) == 1 and abs(pl[0]["lnglat"][1] - 30.32) < 1e-6 and pl[0]["sub"].startswith("300 N Main"), ("replaced by the phone's", pl)
    assert [a["text"][:13] for a in next(c for c in get("/api/contacts/book")["contacts"] if c["name"] == "Sean Q Example")["addresses"]][0] == "300 N Main St"
    assert post("/api/contacts", {"op": "forget"})["forgotten"] == 1
    assert not any(p["label"] == "contact" for p in get("/api/places")["places"]), "Forget takes them out of Places too"
    time.sleep(0.8)
    assert not any(x["kind"] == "contact" for x in get("/api/search?q=sean")) and get("/api/contacts")["phones"] == [], \
        "forgotten, and not fetched again while the phone stays connected"
finally:
    b.terminate(); b.wait(5)
print("6) head unit: the phonebook goes to the Address book on every connection; 'sean' finds his house; a starred contact shows in Places and follows the phone; Forget")
print("contacts: all tests passed")
