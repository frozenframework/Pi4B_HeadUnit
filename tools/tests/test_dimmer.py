"""Dimmer: backlight + DDC methods with fake hardware, method auto-pick, auto night/day, floor clamp."""
import importlib.util, json, os, stat, sys, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
os.environ.update(USER_DIR=str(tmp / "user"), POWER_STATE_FILE=str(tmp / "state.json"), HU_BACKLIGHT_DIR=str(tmp / "bl"))
BIN = os.path.join(os.path.dirname(__file__), "..", "..", "bin")
def load():
    spec = importlib.util.spec_from_file_location("dim", os.path.join(BIN, "hu-dimmer.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
(tmp / "user").mkdir()
def settings(**d): (tmp / "user" / "settings.json").write_text(json.dumps({"display": d}))
def lights(on): (tmp / "state.json").write_text(json.dumps({"illum": on}))

# --- fake ddcutil on PATH
binp = tmp / "fakebin"; binp.mkdir(); log = tmp / "ddc.log"
(binp / "ddcutil").write_text(f'#!/bin/sh\necho "$@" >> {log}\ncase "$*" in *getvcp*) echo "VCP 10 C 80 100";; esac\n')
(binp / "ddcutil").chmod(0o755); os.environ["PATH"] = f"{binp}:{os.environ['PATH']}"

# 1) no backlight device -> auto picks DDC
dim = load(); m, errs = dim.pick("auto"); assert m.name == "ddc", (m.name, errs)
settings(level=60, auto=False); d = dim.Dimmer(m); d.step()
assert "setvcp 10 60" in log.read_text(), log.read_text()
# 2) backlight device present -> auto picks backlight (preferred over DDC)
bl = tmp / "bl" / "panel"; bl.mkdir(parents=True); (bl / "brightness").write_text("255"); (bl / "max_brightness").write_text("255")
m, _ = dim.pick("auto"); assert m.name == "backlight"
d = dim.Dimmer(m)
settings(level=50, auto=False); d.step(); assert (bl / "brightness").read_text() == "128"
settings(level=100, auto=True, day=90, night=30); lights(True); d.step(); assert (bl / "brightness").read_text() == "76", "night 30%"
lights(False); d.step(); assert (bl / "brightness").read_text() == "230", "day 90%"
settings(level=1, auto=False); d.step(); assert (bl / "brightness").read_text() == "13", "floor 5% on backlight"
st = json.loads((tmp / "user" / "display-status.json").read_text()); assert st["method"] == "backlight" and st["applied"] == 5
# 3) forced method that isn't available -> clear error
os.environ["PATH"] = "/usr/bin:/bin"
try: dim.pick("ddc"); raise SystemExit("should fail")
except RuntimeError as e: assert "ddc" in str(e)
print("dimmer ok: ddc, backlight, auto night/day, floor, method errors")
