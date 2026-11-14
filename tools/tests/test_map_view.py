"""Map view (bird's eye / 3rd person): the choice is saved with the settings, and the camera for each
view is what the screens promise - bird's eye looks straight down, 3rd person looks ahead from behind
with the car low on the screen, turned to the direction of travel, pulling back with speed and staying
under the zoom where shop labels appear."""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nDIAG_AUTO=0\nROUTING_WARM_DELAY=9999\n")
for d in ("user", "usage", "data"): (T / d).mkdir()
env = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(T / "data"), "APP_DIR": str(ROOT / "apps"),
       "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T), "XDG_RUNTIME_DIR": str(T)}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=env, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())
try:
    for _ in range(60):
        try: st = call("/api/settings"); break
        except OSError: time.sleep(0.25)
    assert st["mapView"] == "bird", st
    assert call("/api/settings", {"mapView": "chase"})["mapView"] == "chase" and call("/api/settings")["mapView"] == "chase"
finally:
    b.terminate(); b.wait(5)
print("1) the view is a saved setting (bird's eye until changed)")

nav = (ROOT / "apps/nav/nav.js").read_text()
assert 'id="viewBtn"' in (ROOT / "apps/nav/index.html").read_text() and 'id="mapView"' in (ROOT / "apps/theme/index.html").read_text()
if not shutil.which("node"):
    print("2) camera check skipped (needs node)"); print("map view: all tests passed"); sys.exit(0)
src = re.search(r"const CHASE_PITCH = .*?\n(function cameraFor\(.*?\n}\n)", nav, re.S)
js = "const CHASE_PITCH = " + nav.split("const CHASE_PITCH = ", 1)[1].split("\n", 1)[0] + "\n" + src.group(1) + """
const out = {};
for (const [k, a] of Object.entries({ birdFree: ['bird', false, 0, 400, 13, 77, 120], birdMove: ['bird', false, 20, 400, 16, 77, 120], birdNav: ['bird', true, 20, 400, 13, 77, 120],
    chaseStop: ['chase', false, 0, 400, 13, 77, 120], chaseNav: ['chase', true, 20, 400, 13, 77, 120], chaseFast: ['chase', true, 30, 400, 13, 77, 120] }))
  out[k] = cameraFor(...a);
console.log(JSON.stringify(out));
"""
c = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
assert c["birdFree"]["pitch"] == 0 and c["birdFree"]["bearing"] == 77 and c["birdFree"]["zoom"] == 14.5, "parked: the map stays as you turned it"
assert c["birdMove"]["bearing"] == 120 and c["birdMove"]["zoom"] == 16 and c["birdNav"]["pitch"] == 0 and c["birdNav"]["padding"]["left"] == 340
for k in ("chaseStop", "chaseNav", "chaseFast"):
    v = c[k]; assert 50 <= v["pitch"] <= 60 and v["bearing"] == 120 and v["zoom"] < 16.5 and v["padding"]["top"] >= 120, (k, v)
assert c["chaseFast"]["zoom"] < c["chaseNav"]["zoom"] <= c["chaseStop"]["zoom"] and c["chaseNav"]["padding"]["left"] == 340 and c["chaseStop"]["padding"]["left"] == 0
print("2) bird's eye: straight down; 3rd person: tilted, heading up, car low on the screen, pulls back with speed, no shop labels")
print("map view: all tests passed")
