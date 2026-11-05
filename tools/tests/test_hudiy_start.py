"""Start screen: hu-hudiy-start.py sets Hudiy's defaultAction to the map (or back to Hudiy's dashboard)
and, once Hudiy's API answers, dispatches the map action exactly once."""
import json, os, socket, struct, subprocess, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
H = Path(tempfile.mkdtemp()); cfg = H / ".hudiy/share/config/main_configuration.json"; cfg.parent.mkdir(parents=True)
st = H / ".config/headunit/settings.json"; st.parent.mkdir(parents=True)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
cfg.write_text(json.dumps({"application": {"defaultAction": "", "showMenuIcon": True}, "api": {"tcpEndpointPort": port}}))
env = {**os.environ, "HOME": str(H)}
def run(*a): return subprocess.run([sys.executable, str(ROOT / "bin/hu-hudiy-start.py"), *a], env=env, capture_output=True, text=True, timeout=30)

r = run("--config"); app = json.loads(cfg.read_text())["application"]
assert app["defaultAction"] == "hu_navigation" and app["showMenuIcon"] is True, (app, r.stderr)
st.write_text(json.dumps({"startOnMap": False})); run("--config")
assert json.loads(cfg.read_text())["application"]["defaultAction"] == "", "switch off should give Hudiy's dashboard"
print("1) defaultAction follows the Start on the map switch (default on)")

got = []
def fake_hudiy():                       # Hudiy's API comes up a few seconds after the head unit starts
    time.sleep(3)
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", port)); srv.listen()
    srv.settimeout(20)
    while True:
        try: c, _ = srv.accept()
        except OSError: return
        buf = b""
        c.settimeout(3)
        try:
            while True:
                d = c.recv(4096)
                if not d: break
                buf += d
        except OSError: pass
        while len(buf) >= 12:
            n, mid, _ = struct.unpack("<III", buf[:12]); got.append((mid, buf[12:12 + n])); buf = buf[12 + n:]
threading.Thread(target=fake_hudiy, daemon=True).start()
st.write_text(json.dumps({"startOnMap": True}))
r = run("--open"); time.sleep(0.5)
assert "sent" in r.stdout, (r.stdout, r.stderr)
ids = [m for m, _ in got]
assert ids[0] == 1 and 43 in ids, got          # hello, then DispatchAction
payload = dict(got)[43]; assert b"hu_navigation" in payload, payload
assert ids.count(43) == 1
print("2) waits for Hudiy's API, then opens the map once:", ids)
st.write_text(json.dumps({"startOnMap": False})); got.clear()
r = run("--open"); assert "open map" not in r.stdout and not got
print("3) switch off: nothing dispatched")
print("hudiy start screen: all tests passed")
