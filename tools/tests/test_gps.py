"""GPS: every receiver in the parts lists reaches gpsd (udev/61-headunit-gps.rules - Debian's own list
misses the Adafruit Ultimate GPS USB), and when there's no position the map and Settings say why: no
receiver, a receiver gpsd isn't using, or one still finding satellites. A fake gpsd feeds the real bridge."""
import fnmatch, json, os, re, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())

# 1) the udev rule: which USB serial devices it hands to gpsd (a small evaluator for this one file)
RULES = (ROOT / "udev/61-headunit-gps.rules").read_text()
def handed_to_gpsd(attrs, text=RULES):
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    for l in lines:
        if l.startswith(("SUBSYSTEM", "ACTION")): continue
        conds = re.findall(r'(ATTRS\{\w+\})=="([^"]*)"', l)
        if not conds and l.startswith('GOTO="hu_gps_end"'): return False
        if conds and all(any(fnmatch.fnmatchcase(attrs.get(k[6:-1], ""), alt) for alt in v.split("|")) for k, v in conds) and 'GOTO="hu_gps"' in l:
            return True
    return False
assert handed_to_gpsd({"idVendor": "1546", "idProduct": "01a7", "product": "u-blox 7 - GPS/GNSS Receiver"}), "VK-162"
assert handed_to_gpsd({"idVendor": "10c4", "idProduct": "ea60", "product": "CP2102N USB to UART Bridge Controller"}), "Adafruit 4279"
assert handed_to_gpsd({"idVendor": "067b", "idProduct": "2303", "product": "USB-Serial Controller"}), "BU-353"
assert handed_to_gpsd({"idVendor": "abcd", "idProduct": "1234", "product": "Some GNSS Receiver"}), "named GNSS"
assert not handed_to_gpsd({"idVendor": "0403", "idProduct": "6001", "product": "FT232R USB UART"}), "a plain FTDI cable"
assert 'ENV{SYSTEMD_WANTS}="gpsdctl@%k.service headunit-gps-setup@%k.service"' in RULES and 'TAG+="systemd"' in RULES
if shutil.which("udevadm") and "verify" in subprocess.run(["udevadm", "--help"], capture_output=True, text=True).stdout:
    subprocess.run(["udevadm", "verify", str(ROOT / "udev/61-headunit-gps.rules")], check=True, capture_output=True)
# GPS_USB from headunit.conf adds a receiver (the awk line in setup.d/50-gps.sh)
stage = (ROOT / "setup.d/50-gps.sh").read_text()
awk = re.search(r"awk -v extra=\"\$extra\" '([^']*)'", stage).group(1)
extra = 'ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", GOTO="hu_gps"\\n'
out = subprocess.run(["awk", "-v", f"extra={extra}", awk, str(ROOT / "udev/61-headunit-gps.rules")], capture_output=True, text=True, check=True).stdout
assert handed_to_gpsd({"idVendor": "0403", "idProduct": "6015"}, out) and not handed_to_gpsd({"idVendor": "0403", "idProduct": "6015"})
assert "61-headunit-gps.rules" in stage and "udevadm trigger --subsystem-match=tty --action=add" in stage and 'OPTIONS=""' in stage
print("1) the VK-162, the Adafruit Ultimate GPS USB, a BU-353 and anything named GPS go to gpsd; GPS_USB adds more")

# 2) the bridge with a fake gpsd: what the map and Settings hear
class FakeGpsd:
    def __init__(self):
        self.s = socket.socket(); self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0)); self.s.listen(4); self.port = self.s.getsockname()[1]
        self.clients = []; threading.Thread(target=self.accept, daemon=True).start()
    def accept(self):
        while True:
            c, _ = self.s.accept(); c.recv(200)
            c.sendall(b'{"class":"VERSION","release":"3.25"}\n{"class":"DEVICES","devices":[]}\n{"class":"WATCH","enable":true}\n')
            self.clients.append(c)
    def send(self, *msgs):
        for c in self.clients:
            for m in msgs: c.sendall((json.dumps(m) + "\n").encode())
gpsd = FakeGpsd()
sys_usb = T / "usb"; (sys_usb / "1-1.2").mkdir(parents=True)
for k, v in {"idVendor": "10c4", "idProduct": "ea60", "product": "CP2102N USB to UART Bridge Controller"}.items(): (sys_usb / "1-1.2" / k).write_text(v + "\n")
(sys_usb / "1-1.3").mkdir(); (sys_usb / "1-1.3" / "idVendor").write_text("1314\n"); (sys_usb / "1-1.3" / "idProduct").write_text("1520\n")
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\nDIAG_AUTO=0\nGPSD_PORT={gpsd.port}\nSYS_USB={sys_usb}\nGPS_SETUP_FILE={T / 'gps.json'}\n")
(T / "gps.json").write_text(json.dumps({"device": "/dev/ttyUSB0", "chip": "mtk", "ok": True, "systems": ["GPS", "GLONASS", "Galileo"]}))
for d in ("user", "usage", "run", "data"): (T / d).mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(T / "data"), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T), "XDG_RUNTIME_DIR": str(T / "run")}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def gps():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10).read())["gps"]
def wait(cond, what):
    for _ in range(80):
        try:
            g = gps()
            if cond(g): return g
        except OSError: pass
        time.sleep(0.25)
    raise AssertionError(f"{what}: {g if 'g' in dir() else None}")
try:
    g = wait(lambda g: g["state"] == "no_receiver", "no receiver handed to gpsd")
    assert g["plugged"] == [["10c4:ea60", "CP2102N USB to UART Bridge Controller"]], g["plugged"]   # (the Carlinkit isn't a GPS)
    gpsd.send({"class": "DEVICE", "path": "/dev/ttyUSB0", "driver": "NMEA0183", "activated": 1},
              {"class": "SKY", "nSat": 7, "uSat": 0, "satellites": [{"PRN": p, "gnssid": gid, "used": False} for p, gid in
               ((3, 0), (7, 0), (12, 0), (68, 6), (71, 6), (303, 2))] + [{"PRN": 305}]},
              {"class": "TPV", "device": "/dev/ttyUSB0", "mode": 1})
    g = wait(lambda g: g["state"] == "searching" and g["seen"] == 7, "receiver, finding satellites")
    assert g["receivers"] == 1 and g["drivers"] == ["NMEA0183"]
    assert g["systems"] == {"GPS": [3, 0], "GLONASS": [2, 0], "Galileo": [2, 0]}, g["systems"]          # (PRN 305: Galileo by number)
    assert g["setup"]["systems"] == ["GPS", "GLONASS", "Galileo"], g["setup"]
    # the map's live stream carries the same
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/gps/stream", timeout=10)
    first = json.loads(r.readline().decode()[5:]); assert first["state"] == "searching" and first["seen"] == 7, first
    gpsd.send({"class": "SKY", "nSat": 9, "uSat": 6}, {"class": "TPV", "device": "/dev/ttyUSB0", "mode": 3, "lat": 30.3, "lon": -95.5, "speed": 0})
    g = wait(lambda g: g["state"] == "fix", "a fix")
    assert g["used"] == 6 and g["lat"] == 30.3
    seen = []
    for _ in range(6):
        line = r.readline().decode().strip()
        if line.startswith("data:"): seen.append(json.loads(line[5:]))
        if seen and seen[-1].get("state") == "fix": break
    assert seen[-1]["mode"] == 3 and seen[-1]["lon"] == -95.5, seen
    r.close()
    gpsd.send({"class": "DEVICE", "path": "/dev/ttyUSB0", "activated": 0})        # unplugged
    g = wait(lambda g: g["state"] == "no_receiver" and g["mode"] == 0, "receiver unplugged"); assert g["setup"] is None
    for c in gpsd.clients: c.close()
    gpsd.s.close()
    wait(lambda g: g["state"] == "no_gpsd", "gpsd gone")
finally:
    b.terminate(); b.wait(5)
print("2) the bridge reports: no receiver (and one plugged in that gpsd isn't using), finding satellites by system, a fix, gpsd gone")

# 3) bin/hu-gps-setup.py: each receiver set to GPS + GLONASS + Galileo, through a fake gpsd (JSON port +
#    control socket) answering like the receiver would
class FakeReceiver:
    def __init__(self, device, answer):
        self.device, self.answer, self.sent = device, answer, []
        self.j = socket.socket(); self.j.bind(("127.0.0.1", 0)); self.j.listen(2); self.port = self.j.getsockname()[1]
        self.ctl = str(T / f"ctl{self.port}.sock"); self.c = socket.socket(socket.AF_UNIX); self.c.bind(self.ctl); self.c.listen(4)
        self.client = None
        threading.Thread(target=self.json_side, daemon=True).start(); threading.Thread(target=self.control_side, daemon=True).start()
    def json_side(self):
        self.client, _ = self.j.accept(); self.client.recv(200)
        self.client.sendall(b'{"class":"DEVICES","devices":[{"path":"/dev/ttyUSB0","driver":"NMEA0183"}]}\n')
        time.sleep(0.2); self.client.sendall((json.dumps(self.device) + "\n").encode())
    def control_side(self):
        while True:
            c, _ = self.c.accept(); cmd = c.recv(4096).decode().strip()
            assert cmd.startswith("&/dev/ttyUSB0="), cmd
            data = bytes.fromhex(cmd.split("=", 1)[1]); self.sent.append(data); c.sendall(b"OK\n"); c.close()
            for line in self.answer(data): self.client.sendall(line.encode() + b"\r\n")
def run_setup(device, answer, systems=None, vid="10c4"):
    rx = FakeReceiver(device, answer)
    tty = T / f"tty{rx.port}"; usb = tty / "usbdev"; (usb / "port").mkdir(parents=True); (usb / "idVendor").write_text(vid + "\n")
    (tty / "ttyUSB0").mkdir(); (tty / "ttyUSB0" / "device").symlink_to(usb / "port")
    conf = T / f"c{rx.port}.conf"; conf.write_text(f'GPS_SYSTEMS="{systems}"\n' if systems else "")
    out = T / f"r{rx.port}.json"
    env = {**os.environ, "HU_GPSD": f"127.0.0.1:{rx.port}", "GPSD_SOCKET": rx.ctl, "HU_GPS_SETUP_FILE": str(out),
           "HU_SYS_CLASS_TTY": str(tty), "HU_CONF": str(conf), "HU_GPS_IDENTIFY_S": "3", "HU_GPS_ACK_S": "1.5"}
    subprocess.run([sys.executable, str(ROOT / "bin/hu-gps-setup.py"), "/dev/ttyUSB0"], env=env, check=True, capture_output=True, timeout=30)
    return json.loads(out.read_text()), rx.sent
def pmtk_ack(supported):
    def answer(data):
        body = data.decode().strip()
        assert body.startswith("$PMTK353,") and body[-3] == "*", body
        cs = 0
        for ch in body[1:-3].encode(): cs ^= ch
        assert f"{cs:02X}" == body[-2:], body
        args = body[9:-3].split(",")
        return [f"$PMTK001,353,{3 if [int(a) for a in args[:3]] <= supported else 2},{','.join(args)}*00"]
    return answer
MTK = {"class": "DEVICE", "path": "/dev/ttyUSB0", "driver": "MTK-3301", "subtype": "AXN_5.1.7"}
r, sent = run_setup(MTK, pmtk_ack([1, 1, 1]))
assert sent == [b"$PMTK353,1,1,1,0,0*2A\r\n"] and r["ok"] and r["systems"] == ["GPS", "GLONASS", "Galileo"] and r["chip"] == "mtk", (r, sent)
r, sent = run_setup(MTK, pmtk_ack([1, 1, 0]))                      # a chip without Galileo: GPS + GLONASS
assert [x[:18] for x in sent] == [b"$PMTK353,1,1,1,0,0", b"$PMTK353,1,1,0,0,0"] and r["systems"] == ["GPS", "GLONASS"], (r, sent)
r, sent = run_setup(MTK, pmtk_ack([1, 1, 1]), systems="gps,glonass")
assert sent == [b"$PMTK353,1,1,0,0,0*2B\r\n"] and r["systems"] == ["GPS", "GLONASS"], (r, sent)
r, sent = run_setup({**MTK, "driver": "NMEA0183"}, pmtk_ack([1, 1, 1]))   # gpsd never said MTK: the Adafruit's USB chip tells
assert r["systems"] == ["GPS", "GLONASS", "Galileo"], r
r, sent = run_setup({"class": "DEVICE", "path": "/dev/ttyUSB0", "driver": "u-blox", "subtype": "SW 1.00 (59842),HW 00070000"}, lambda d: [], vid="1546")
assert sent == [] and r["chip"] == "ublox7" and r["systems"] == ["GPS"], (r, sent)          # VK-162: one system at a time
r, sent = run_setup({"class": "DEVICE", "path": "/dev/ttyUSB0", "driver": "u-blox", "subtype": "SW ROM CORE 3.01 (107888),HW 00080000",
                     "subtype1": "FWVER=SPG 3.01,PROTVER=18.00"}, lambda d: [], vid="1546")
gnss, rst = sent
assert gnss[:4] == b"\xb5\x62\x06\x3e" and rst[:4] == b"\xb5\x62\x06\x04" and r["chip"] == "ublox8", (r, sent)
a = b = 0
for ch in gnss[2:-2]: a = (a + ch) & 0xFF; b = (b + a) & 0xFF
assert gnss[-2:] == bytes([a, b]), "UBX checksum"
blocks = {g: f for g, _, _, _, f in (struct.unpack("<BBBBI", gnss[10 + i * 8: 18 + i * 8]) for i in range(gnss[9]))}
assert all(blocks[g] & 1 for g in (0, 2, 6)) and not blocks[3] & 1, blocks                    # GPS, Galileo, GLONASS on; BeiDou off
print("3) setup: Adafruit -> GPS + GLONASS + Galileo (or the most it can), GPS_SYSTEMS honoured, u-blox 7 stays GPS, u-blox M8 gets CFG-GNSS")

# 4) the screens use it
nav = (ROOT / "apps/nav/nav.js").read_text(); theme = (ROOT / "apps/theme/theme.js").read_text()
assert "toast(gpsWaiting())" in nav and "nav.gps_searching" in nav and "state.gps = f" in nav
assert "set.gps_unused" in theme and "set.gps_systems" in theme and 'id="gpsInfo"' in (ROOT / "apps/theme/index.html").read_text()
diag = (ROOT / "bin/hu-diag.sh").read_text()
assert "gpsdctl@*" in diag and "/dev/serial/by-id" in diag and '"lat"' not in diag.split('sec "GPS')[1].split("sec ")[0]
print("4) map: says why it's waiting; Settings > Developer: GPS line; diagnostics: receivers, satellites, no positions")
print("gps: all tests passed")
