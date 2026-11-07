"""Diagnostics bundles: secrets and personal details are kept out (Wi-Fi keys, passwords, the Hudiy order
number, most of each Bluetooth address, positions); a bundle has the report, logs and data files; only
the newest ones are kept and copied to the SD card; the bridge collects one from Settings, lists them,
serves them for download (only bundle names, no other files), makes one by itself after a slow start,
and the image builder leaves a local diag/ folder out."""
import json, os, socket, subprocess, sys, tarfile, tempfile, time, urllib.request, urllib.error
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())

# 1) what's kept out
raw = """psk=SuperSecret123
wifi password: hunter2
HUDIY_ORDER="#12345"
license_key = abc
Device 4C:32:75:A1:B2:C3 Robert's iPhone
{"lat": 30.312345, "lon": -95.456789, "speed": 3}
"lastPos": [-95.456789, 30.312345]
routing: ok
"""
out = subprocess.run(["bash", str(ROOT / "bin/hu-diag.sh"), "--redact-test"], input=raw, capture_output=True, text=True).stdout
for secret in ("SuperSecret123", "hunter2", "12345", "abc", "4C:32:75:A1", "30.312345", "95.456789"):
    assert secret not in out, (secret, out)
assert "XX:XX:XX:XX:B2:C3" in out and '"lat": 30.3,' in out and "routing: ok" in out and "Robert's iPhone" in out, out
print("1) kept out: Wi-Fi keys, passwords, the Hudiy order number, licences, most of each Bluetooth address, positions")

# 2) a bundle (on this test machine most system tools are missing; it still has to come out complete)
diag, boot = T / "diag", T / "bootfs"; boot.mkdir()
env = {**os.environ, "HU_DIAG_DIR": str(diag), "HU_BOOTFS": str(boot)}
paths = []
for i in range(12):
    r = subprocess.run(["bash", str(ROOT / "bin/hu-diag.sh"), "--bundle", "test run!"], env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    paths.append(Path(r.stdout.strip().splitlines()[-1]))
    if i == 0:
        with tarfile.open(paths[0]) as t:
            names = t.getnames()
        top = names[0].split("/")[0]
        assert top.startswith("headunit-diag-") and top.endswith("-testrun"), top
        assert f"{top}/report.txt" in names and f"{top}/logs/journal-this-start.txt" in names, names
        rep = tarfile.open(paths[0]).extractfile(f"{top}/report.txt").read().decode()
        assert "why: testrun" in rep and "start-up timeline" in rep and "phone contacts (counts only)" in rep, rep[:500]
    time.sleep(1.05)                                   # (names carry the second)
left = sorted(diag.glob("headunit-diag-*.tar.gz"))
assert len(left) == 10 and paths[-1] in left and paths[0] not in left, "the newest 10 are kept"
assert len(list((boot / "diag").glob("*.tar.gz"))) == 3, "and the newest 3 on the SD card"
print("2) bundle: report + logs + data; newest 10 kept, newest 3 on the SD card")

# 3) the bridge: collect from Settings, list, download (bundle names only), by itself after a slow start
fake = T / "fake-diag.sh"
fake.write_text(f"""#!/bin/sh
sleep 0.3; mkdir -p {diag}; n={diag}/headunit-diag-miata-9.9.9-$(date +%H%M%S%N)-$1.tar.gz; echo data > $n; echo $n
""")
fake.chmod(0o755)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\nDIAG_DIR={diag}\nDIAG_CMD={fake}\nDIAG_SLOW_START=1\n")
for d in ("user", "usage", "data", "run"): (T / d).mkdir()
benv = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(T / "data"), "APP_DIR": str(ROOT / "apps"),
        "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T),
        "XDG_RUNTIME_DIR": str(T / "run")}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=benv, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=20)
try:
    for _ in range(60):
        try: call("/api/diag"); break
        except OSError: time.sleep(0.25)
    for _ in range(40):                                # no map on screen 1 s after start (test setting): one by itself
        if any(x["name"].endswith("-slow-start.tar.gz") for x in json.loads(call("/api/diag").read())["bundles"]): break
        time.sleep(0.25)
    r = json.loads(call("/api/diag").read()); assert any(x["name"].endswith("-slow-start.tar.gz") for x in r["bundles"]), r
    r = json.loads(call("/api/diag", {"op": "collect"}).read())
    assert r["ok"] and r["name"].endswith("-settings.tar.gz") and r["bundles"][0]["name"] == r["name"], r
    resp = call(f"/api/diag/{r['name']}"); assert resp.read() == b"data\n" and "attachment" in resp.headers["Content-Disposition"]
    for bad in ("..%2Fhu.conf", "hu.conf", "headunit-diag-x.tar.gz"):
        try: call(f"/api/diag/{bad}"); raise AssertionError(bad)
        except urllib.error.HTTPError as e: assert e.code == 404, (bad, e.code)
finally:
    b.terminate(); b.wait(5)
print("3) bridge: by itself after a slow start, from Settings, listed, downloadable - nothing but bundles")

src = (ROOT / "image/make-image.py").read_text(); assert '"diag"' in src.split("SKIP_DIRS")[1].split("\n")[0]
assert "diag/" in (ROOT / ".gitignore").read_text()
print("4) a local diag/ folder stays out of images and git")
print("diag: all tests passed")
