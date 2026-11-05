"""Day and night skins: sunrise/sunset math against published times, and the real bridge switching the skin
for the part of the day it is at the head unit's position (night somewhere dark, day somewhere sunny)."""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import hu_sun

# 1) accuracy: NOAA / timeanddate.com times for Chicago (CDT -5 h in June, CST -6 h in December)
def utc(y, mo, d, h, mi): return time.mktime((y, mo, d, h, mi, 0, 0, 0, 0)) - time.timezone
for (day, rise, sett) in [((2026, 6, 21), (10, 15), (1, 29)), ((2026, 12, 21), (13, 15), (22, 23))]:
    start = utc(*day, 6, 0)                                       # 06:00 UTC = local midnight-ish in Chicago
    r, s = hu_sun.rise_set(41.8781, -87.6298, start)
    want_r = utc(*day, *rise); want_s = utc(*day, *sett) + (86400 if sett[0] < 6 else 0)
    assert abs(r - want_r) < 120 and abs(s - want_s) < 120, (day, r - want_r, s - want_s)
print("1) sunrise/sunset within 2 minutes of the published Chicago times (June and December)")

# places where it is surely night / day right now
now = time.time()
night_lon = next(lon for lon in range(-180, 180, 10) if hu_sun.elevation(0, lon, now) < -20)
day_lon = next(lon for lon in range(-180, 180, 10) if hu_sun.elevation(0, lon, now) > 20)

T = Path(tempfile.mkdtemp()); (T / "user").mkdir(); (T / "data").mkdir()
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "headunit.conf").write_text(f"BRIDGE_PORT={port}\nMAP_REGION=us\nPHONE_LINK=0\n")
(T / "user" / "settings.json").write_text(json.dumps({"theme": "street-heat", "lastPos": [night_lon, 0.0], "syncHudiyColors": False}))
env = {**os.environ, "HU_CONF": str(T / "headunit.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(T / "data"),
       "APP_DIR": str(ROOT / "apps"), "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"),
       "POWER_STATE_FILE": str(T / "state.json"), "HOME": str(T)}
(T / "usage").mkdir()
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "bridge.log", "w"), stderr=subprocess.STDOUT)
def call(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())
try:
    for _ in range(50):
        try: call("/api/settings"); break
        except OSError: time.sleep(0.2)
    else: raise SystemExit(open(T / "bridge.log").read())
    sun = call("/api/sun")
    assert sun["night"] and sun["source"] == "last", sun
    s = call("/api/settings", {"dayNight": True, "dayTheme": "sunset-junction", "nightTheme": "after-midnight"})
    assert s["theme"] == "after-midnight", s["theme"]
    print("2) turning it on at night switches straight to the night skin")
    s = call("/api/settings", {"theme": "neon-overdrive"})          # picked on the map while it's night
    assert s["nightTheme"] == "neon-overdrive" and s["dayTheme"] == "sunset-junction" and s["theme"] == "neon-overdrive", s
    print("3) a skin picked at night becomes the night skin; the day skin stays")
    s = call("/api/settings", {"lastPos": [day_lon, 0.0]})            # drove into daylight
    deadline = time.time() + 40
    while time.time() < deadline and call("/api/settings")["theme"] != "sunset-junction": time.sleep(1)
    assert call("/api/settings")["theme"] == "sunset-junction" and not call("/api/sun")["night"]
    print("4) at sunrise (here: daylight at the position) the bridge switches to the day skin by itself")
    s = call("/api/settings", {"dayNight": False, "theme": "street-heat"})
    time.sleep(1); assert call("/api/settings")["theme"] == "street-heat"
    print("5) switched off: one skin again, nothing changes it")
finally:
    b.terminate(); b.wait(5)
print("day/night skins: all tests passed")
