#!/usr/bin/env python3
"""
Turn on every satellite system a USB GPS receiver can use - GPS, GLONASS and Galileo - so it has more
satellites to work with: a faster first fix, and a steadier one under trees, between buildings or with
the receiver tucked under the dash. Runs (as root) each time a receiver is plugged in and at every start
(systemd/headunit-gps-setup@.service, wanted by udev/61-headunit-gps.rules), because these receivers
keep their settings only while powered (the Adafruit only longer with its coin cell).

    hu-gps-setup.py /dev/ttyUSB0

Talks to the receiver through gpsd, which owns the port: gpsd says what the receiver is (DEVICE), and
the command goes through gpsd's control socket. Which systems: GPS_SYSTEMS in headunit.conf
(default gps,glonass,galileo; GPS is always on).

  - Adafruit Ultimate GPS (MediaTek, PA1616D): $PMTK353; if the receiver can't do all of them at once,
    the most it can (GPS + GLONASS, then GPS alone). Older 66-channel boards are GPS-only.
  - u-blox M8 and newer: UBX-CFG-GNSS (GPS, SBAS, QZSS + the asked ones), then a GNSS restart.
  - u-blox 7 (VK-162): listens to one system at a time, so it stays on GPS (the most satellites).
The result goes to /run/headunit-gps.json for Settings > Developer > GPS.
"""
import json, os, socket, struct, sys, time
from pathlib import Path

GPSD = os.environ.get("HU_GPSD", "127.0.0.1:2947")
CONTROL = os.environ.get("GPSD_SOCKET", "/run/gpsd.sock")
OUT = Path(os.environ.get("HU_GPS_SETUP_FILE", "/run/headunit-gps.json"))
SYS_TTY = Path(os.environ.get("HU_SYS_CLASS_TTY", "/sys/class/tty"))
HU_CONF = Path(os.environ.get("HU_CONF", "/etc/headunit/headunit.conf"))
NAMES = {"gps": "GPS", "glonass": "GLONASS", "galileo": "Galileo"}
IDENTIFY_S = float(os.environ.get("HU_GPS_IDENTIFY_S", "45"))
ACK_S = float(os.environ.get("HU_GPS_ACK_S", "4"))

def log(*a):
    print(*a, flush=True)

def wanted():
    v = "gps,glonass,galileo"
    try:
        for line in HU_CONF.read_text().splitlines():
            if line.startswith("GPS_SYSTEMS="): v = line.split("=", 1)[1].strip().strip('"').strip("'") or v
    except OSError:
        pass
    got = [s for s in (x.strip().lower() for x in v.split(",")) if s in NAMES]
    return ["gps"] + [s for s in ("glonass", "galileo") if s in got]

def nmea(body):
    cs = 0
    for c in body.encode(): cs ^= c
    return f"${body}*{cs:02X}\r\n".encode()

def ubx(cls, mid, payload):
    m = bytes([cls, mid]) + struct.pack("<H", len(payload)) + payload
    a = b = 0
    for c in m: a = (a + c) & 0xFF; b = (b + a) & 0xFF
    return b"\xb5\x62" + m + bytes([a, b])

def usb_vid(dev):
    """The USB vendor id behind a tty (/sys/class/tty/ttyUSB0/device/... up to the USB device)."""
    try: p = (SYS_TTY / Path(dev).name / "device").resolve()
    except OSError: return ""
    for d in [p, *p.parents][:6]:
        try: return (d / "idVendor").read_text().strip()
        except OSError: continue
    return ""

class Gpsd:
    """gpsd's JSON port, watching with raw packets too (to see the receiver's answers)."""
    def __init__(self):
        host, port = GPSD.rsplit(":", 1)
        self.s = socket.create_connection((host, int(port)), timeout=5)
        self.s.sendall(b'?WATCH={"enable":true,"json":true,"raw":1}\n')
        self.buf = b""
    def lines(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                yield line.decode(errors="replace").strip()
            self.s.settimeout(max(0.05, end - time.time()))
            try: chunk = self.s.recv(4096)
            except socket.timeout: return
            if not chunk: return
            self.buf += chunk
    def close(self):
        try: self.s.close()
        except OSError: pass

def control(dev, data):
    """Send bytes to the receiver through gpsd's control socket. True when gpsd took it."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(5)
    try:
        s.connect(CONTROL)
        s.sendall(b"&" + dev.encode() + b"=" + data.hex().encode() + b"\n")
        return s.recv(64).startswith(b"OK")
    except OSError as e:
        log("gpsd control socket:", e); return False
    finally:
        s.close()

def identify(g, dev):
    """(driver, subtype) once gpsd knows what the receiver is."""
    driver = sub = ""
    for line in g.lines(IDENTIFY_S):
        if not line.startswith("{"): continue
        try: m = json.loads(line)
        except ValueError: continue
        devs = m.get("devices", []) if m.get("class") == "DEVICES" else [m] if m.get("class") == "DEVICE" else []
        for d in devs:
            if d.get("path") == dev and d.get("driver"):
                driver, sub = d["driver"], " ".join(str(d.get(k, "")) for k in ("subtype", "subtype1"))
                if driver not in ("NMEA0183", "Unknown"): return driver, sub
    return driver, sub

def kind(driver, sub, vid):
    d = driver.lower()
    if "u-blox" in d or "ublox" in d or (vid == "1546" and not d.startswith("mtk")):
        hw = sub.split("HW ", 1)[1][:8] if "HW " in sub else ""
        pv = sub.split("PROTVER", 1)[1].lstrip(" =")[:2] if "PROTVER" in sub else ""
        newer = (hw[:4] == "0008" or hw[:4] > "0008") if len(hw) == 8 and hw.isdigit() else (pv.isdigit() and int(pv) >= 15)
        return "ublox8" if newer else "ublox7"
    if d.startswith("mtk") or vid == "10c4":
        return "mtk"
    return ""

def mtk(g, dev, want):
    tries = [want] + [w for w in (["gps", "glonass"], ["gps"]) if len(w) < len(want)]
    for combo in tries:
        body = "PMTK353,{},{},{},0,0".format(1, int("glonass" in combo), int("galileo" in combo))
        if not control(dev, nmea(body)): return None, "gpsd didn't take the command"
        for line in g.lines(ACK_S):
            if line.startswith("$PMTK001,353,"):
                flag = line.split(",")[2].split("*")[0]
                if flag == "3": return combo, ""
                log(f"{body}: the receiver said {flag} (not supported)"); break
        else:
            return combo, "sent; the receiver didn't confirm"
    return ["gps"], "GPS only (an older GPS-only board)"

def ublox8(dev, want):
    on = lambda s: 0x01010001 if s in want else 0x01010000
    blocks = [(0, 8, 16, 0x01010001), (1, 1, 3, 0x01010001), (2, 4, 8, on("galileo")), (3, 8, 16, 0x01010000),
              (4, 0, 8, 0x03010000), (5, 0, 3, 0x05050001), (6, 8, 14, on("glonass"))]
    payload = struct.pack("<BBBB", 0, 0, 0xFF, len(blocks)) + b"".join(struct.pack("<BBBBI", g, r, m, 0, f) for g, r, m, f in blocks)
    if not control(dev, ubx(0x06, 0x3E, payload)): return None, "gpsd didn't take the command"
    time.sleep(0.3)
    control(dev, ubx(0x06, 0x04, struct.pack("<HBB", 0x0000, 0x02, 0)))     # GNSS restart (hot) so it takes effect
    return want, ""

def main():
    if len(sys.argv) != 2: sys.exit(__doc__)
    dev = sys.argv[1] if sys.argv[1].startswith("/") else "/dev/" + sys.argv[1]
    want = wanted(); vid = usb_vid(dev)
    res = {"device": dev, "asked": [NAMES[s] for s in want], "at": int(time.time())}
    try:
        g = Gpsd()
    except OSError as e:
        res.update(ok=False, note=f"gpsd isn't answering: {e}"); write(res); return
    try:
        driver, sub = identify(g, dev)
        k = kind(driver, sub, vid)
        res.update(driver=driver, chip=k)
        if k == "mtk": got, note = mtk(g, dev, want)
        elif k == "ublox8": got, note = ublox8(dev, want)
        elif k == "ublox7": got, note = ["gps"], "one system at a time"
        else: got, note = None, f"unknown receiver ({driver or 'not identified'}): left as it is"
    finally:
        g.close()
    res.update(ok=got is not None, systems=[NAMES[s] for s in got or []], note=note)
    write(res)

def write(res):
    log("gps setup:", json.dumps(res))
    try:
        tmp = OUT.with_suffix(".tmp"); tmp.write_text(json.dumps(res)); os.chmod(tmp, 0o644); os.replace(tmp, OUT)
    except OSError as e:
        log("couldn't save", OUT, e)

if __name__ == "__main__":
    main()
