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
           "Connected": {{"type": "b", "data": s.get("connected", True)}},
           "UUIDs": {{"type": "as", "data": ["0000112F-0000-1000-8000-00805F9B34FB", "0000111e-0000-1000-8000-00805f9b34fb"]}}}}
    out("a{{oa{{sa{{sv}}}}}}", [{{"/org/bluez/hci0/dev_AA": {{"org.bluez.Device1": dev}}, "/org/bluez/hci0": {{"org.bluez.Adapter1": {{}}}}}}])
elif a[3:5] == ["org.bluez.obex.Client1", "CreateSession"]:
    if s.get("refuse"): print("Call failed: Not Allowed", file=sys.stderr); sys.exit(1)
    out("o", ["/org/bluez/obex/client/session7"])
elif a[4:5] == ["PullAll"]:
    f = {str(T / "obexd-tmp.vcf")!r}; shutil.copy({str(VCF)!r}, f)
    out("oa{{sv}}", ["/org/bluez/obex/client/session7/transfer0", {{"Filename": {{"type": "s", "data": f}}, "Size": {{"type": "t", "data": 0}}}}])
elif a[0] == "get-property":
    print("Unknown object", file=sys.stderr); sys.exit(1)       # obexd already dropped the finished transfer
""")
FAKE.chmod(0o755)
fake = lambda **k: (T / "fake.json").write_text(json.dumps(k))
fake()
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
print("2) phonebook pulled through obexd, session closed and the file removed; saved 0600 with names + addresses only")

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
assert s0["state"] == "error" and "Not Allowed" in s0["error"] and ct2.search("sean") == []
n = len(LOG.read_text().splitlines()); ct2.tick()
assert not any("CreateSession" in l for l in LOG.read_text().splitlines()[n:]), "waits before asking again"
fake(); ct2.state["AA:BB:CC:DD:EE:FF"]["next_try"] = 0; ct2.tick(); assert ct2.status()[0]["state"] == "ok"
assert ct2.forget() == 1 and ct2.search("sean") == [] and not list((T / "user2").glob("*.json"))
print("4) refused sync retried later; Forget removes the saved contacts")

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
    p = st["phones"][0]; assert p["phone"] == "Robert's iPhone" and p["contacts"] == 2 and p["on_map"] == 1 and p["connected"], st
    assert [c["name"] for c in get("/api/contacts/book")["contacts"]] == ["Joe's, Tacos Shop", "Sean Q Example"]
    r = get("/api/search?q=" + urllib.parse.quote("sean") + "&near=-95.45,30.31")
    assert r[0]["kind"] == "contact" and r[0]["name"] == "Sean Q Example" and abs(r[0]["lat"] - 30.312) < 1e-6, r
    # Settings > Maps > Add contacts to the address book: in Places now, and after every connection
    assert not any(p["label"] == "contact" for p in get("/api/places")["places"])
    post("/api/settings", {"contactsToPlaces": True}); time.sleep(0.8)
    pl = [p for p in get("/api/places")["places"] if p["label"] == "contact"]
    assert [(p["name"], p["ctype"]) for p in pl] == [("Sean Q Example", "home")] and abs(pl[0]["lnglat"][1] - 30.312) < 1e-6, pl
    VCF.write_text(IPHONE.replace("Sean Example", "Sean Q Example").replace("140 N Main St", "300 N Main St"))
    fake(connected=False); time.sleep(0.8); fake(); time.sleep(1.5)                 # the phone reconnects
    pl = [p for p in get("/api/places")["places"] if p["label"] == "contact"]
    assert len(pl) == 1 and abs(pl[0]["lnglat"][1] - 30.32) < 1e-6 and pl[0]["sub"].startswith("300 N Main"), ("moved", pl)
    assert post("/api/contacts", {"op": "forget"})["forgotten"] == 1
    assert not any(p["label"] == "contact" for p in get("/api/places")["places"]), "Forget takes them out of Places too"
    time.sleep(0.8)
    assert not any(x["kind"] == "contact" for x in get("/api/search?q=sean")) and get("/api/contacts")["phones"] == [], \
        "forgotten, and not fetched again while the phone stays connected"
finally:
    b.terminate(); b.wait(5)
print("5) head unit: contacts arrive by themselves, 'sean' finds his house first; into the address book on every connection; Forget")
print("contacts: all tests passed")
