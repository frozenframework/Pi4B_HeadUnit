"""Hudiy skin sync: the config gets dark/light, source color, color roles and background in both theme
sections plus CarPlay/Android Auto day-night = common; --live sends SetDarkMode (37) and SetThemeColors (54);
syncHudiyColors=false turns it off; unrelated keys survive."""
import json, os, socket, struct, subprocess, sys, tempfile, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp()); home = T / "home"; cfgdir = home / ".hudiy/share/config"; cfgdir.mkdir(parents=True)
cfg = cfgdir / "main_configuration.json"
base = {"theme": {"darkThemeEnabled": False, "availableColors": ["#ff0000"],
                  "dark": {"sourceColor": "#123456", "contrastLevel": 0.0, "colorOverwrite": {"surfaceTint": "#010101"}},
                  "light": {"sourceColor": "#654321", "contrastLevel": 0.0}},
        "androidAuto": {"dayNightMode": "day", "dpi": 140}, "autobox": {"dayNightMode": "NIGHT", "productId": 5409},
        "api": {"tcpEndpointPort": 0}, "appearance": {"showClock": True}}

# fake Hudiy API: record message ids and payloads
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
base["api"]["tcpEndpointPort"] = port
cfg.write_text(json.dumps(base))
got = []
def serve():
    c, _ = srv.accept(); buf = b""
    while True:
        d = c.recv(4096)
        if not d: break
        buf += d
    while len(buf) >= 12:
        n, mid, _ = struct.unpack("<III", buf[:12]); got.append((mid, buf[12:12 + n])); buf = buf[12 + n:]
threading.Thread(target=serve, daemon=True).start()

env = {**os.environ, "HOME": str(home), "HU_STYLES": str(ROOT / "apps/styles"), "HU_HUDIY_BG": str(T / "bg"), "HU_CONF": str(T / "none")}
run = lambda *a: subprocess.run([sys.executable, str(ROOT / "bin/hu-hudiy-theme.py"), *a], env=env, capture_output=True, text=True)
r = run("neon-overdrive", "--live"); print(r.stdout.strip())
d = json.loads(cfg.read_text())
assert d["theme"]["darkThemeEnabled"] is True
for sec in ("dark", "light"):
    s = d["theme"][sec]
    assert s["sourceColor"] == "#00E5FF" and s["colorOverwrite"]["primary"] == "#00f0ff" and s["colorOverwrite"]["surface"] == "#07060d"
    assert s["defaultBackground"].endswith("neon-overdrive.jpg") and Path(s["defaultBackground"]).exists()
assert d["theme"]["dark"]["colorOverwrite"]["surfaceTint"] == "#010101", "existing overwrite kept"
assert d["androidAuto"]["dayNightMode"] == "common" and d["autobox"]["dayNightMode"] == "COMMON"   # keeps Hudiy's spelling
assert d["androidAuto"]["dpi"] == 140 and d["autobox"]["productId"] == 5409 and d["appearance"]["showClock"] is True
assert (cfgdir / "main_configuration.json.hu-orig").exists()
import time; time.sleep(0.6)
ids = [m for m, _ in got]
assert ids[:3] == [1, 37, 54] and ids[-1] == 49, ids
assert got[1][1] == b"\x08\x01", got[1]                       # SetDarkMode enabled=true
assert b"#00E5FF" in got[2][1], got[2]
# light skin, at startup, from settings.json
(home / ".config/headunit").mkdir(parents=True); (home / ".config/headunit/settings.json").write_text(json.dumps({"theme": "sunset-junction"}))
print(run("--startup").stdout.strip())
d = json.loads(cfg.read_text()); assert d["theme"]["darkThemeEnabled"] is False and d["theme"]["light"]["sourceColor"] == "#FF6A1A"
# switched off in Settings
(home / ".config/headunit/settings.json").write_text(json.dumps({"theme": "street-heat", "syncHudiyColors": False}))
print(run("--startup").stdout.strip())
assert json.loads(cfg.read_text())["theme"]["light"]["sourceColor"] == "#FF6A1A", "unchanged when off"
print("hudiy theme: all tests passed")
