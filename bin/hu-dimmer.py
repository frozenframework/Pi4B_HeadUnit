#!/usr/bin/env python3
"""
Screen dimmer for every head-unit screen (runs in the desktop session).

Picks the best method the screen supports:
  backlight  real backlight control through /sys/class/backlight   (Raspberry Pi Touch Display 2)
  ddc        the screen's own brightness over the HDMI cable (DDC/CI) (many HDMI controller boards)
  overlay    a click-through black layer above everything, Hudiy and CarPlay included (any screen,
             e.g. the Waveshare 7.9" whose brightness is only a button on the back)

Settings (Head Unit Settings > Display) live in ~/.config/headunit/settings.json under "display":
  level  manual brightness 5-100, used when auto is off
  auto   follow the dash-light wire: "night" level when the headlights are on, "day" level otherwise
  day / night  the two auto levels; method: auto | backlight | ddc | overlay
"""
import json, os, shutil, subprocess, sys, time, logging
from pathlib import Path

log = logging.getLogger("hu-dimmer")
USER_DIR = Path(os.environ.get("USER_DIR", Path.home() / ".config" / "headunit"))
SETTINGS = USER_DIR / "settings.json"
STATUS = USER_DIR / "display-status.json"
POWER_STATE = Path(os.environ.get("POWER_STATE_FILE", "/run/headunit/state.json"))
BACKLIGHT_DIR = Path(os.environ.get("HU_BACKLIGHT_DIR", "/sys/class/backlight"))
DEFAULTS = {"level": 100, "auto": True, "day": 100, "night": 45, "method": "auto"}
MIN_LEVEL = {"backlight": 5, "ddc": 5, "overlay": 20}     # never dim to an unreadable black screen

def read_json(p, default):
    try: return json.loads(Path(p).read_text())
    except Exception: return default

def display_settings():
    d = dict(DEFAULTS); d.update(read_json(SETTINGS, {}).get("display", {})); return d

def target_level(d, night):
    lvl = (d["night"] if night else d["day"]) if d.get("auto") else d["level"]
    return max(0, min(100, int(lvl)))

# ---------------------------------------------------------------- methods
class Backlight:
    name = "backlight"; label = "Screen backlight"
    def __init__(self):
        devs = sorted(BACKLIGHT_DIR.glob("*/brightness")) if BACKLIGHT_DIR.exists() else []
        if not devs: raise RuntimeError("no backlight device")
        self.path = devs[0]; self.max = int((self.path.parent / "max_brightness").read_text())
    def set(self, pct):
        self.path.write_text(str(max(1, round(self.max * pct / 100))))

class DDC:
    name = "ddc"; label = "Screen brightness over HDMI (DDC/CI)"
    def __init__(self):
        if not shutil.which("ddcutil"): raise RuntimeError("ddcutil missing")
        r = subprocess.run(["ddcutil", "--brief", "getvcp", "10"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or "VCP 10" not in r.stdout: raise RuntimeError("screen doesn't answer DDC/CI")
        self.last = None
    def set(self, pct):
        if pct != self.last:
            subprocess.run(["ddcutil", "--noverify", "setvcp", "10", str(pct)], capture_output=True, timeout=15)
            self.last = pct

class Overlay:
    """Fullscreen black layer on the Wayland 'overlay' layer with an empty input region, so taps go through."""
    name = "overlay"; label = "Software dimming (overlay)"
    def __init__(self):
        import gi
        gi.require_version("Gtk", "3.0"); gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell, GLib
        import cairo
        self.Gtk, self.GLib, self.cairo = Gtk, GLib, cairo
        self.alpha = 0.0
        w = Gtk.Window(); self.win = w
        GtkLayerShell.init_for_window(w)
        GtkLayerShell.set_layer(w, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(w, edge, True)
        GtkLayerShell.set_exclusive_zone(w, -1)
        GtkLayerShell.set_keyboard_mode(w, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(w, "headunit-dimmer")
        w.set_app_paintable(True)
        visual = w.get_screen().get_rgba_visual()
        if visual: w.set_visual(visual)
        w.connect("draw", self._draw)
        w.connect("realize", lambda *_: w.input_shape_combine_region(cairo.Region()))
    def _draw(self, _w, cr):
        cr.set_source_rgba(0, 0, 0, self.alpha); cr.set_operator(self.cairo.OPERATOR_SOURCE); cr.paint()
        return False
    def set(self, pct):
        a = round(1 - pct / 100, 3)
        if a == self.alpha: return
        self.alpha = a
        if a <= 0.001: self.win.hide()
        else:
            self.win.show_all(); self.win.input_shape_combine_region(self.cairo.Region()); self.win.queue_draw()

def pick(method):
    order = {"auto": [Backlight, DDC, Overlay], "backlight": [Backlight], "ddc": [DDC], "overlay": [Overlay]}[method]
    errors = []
    for cls in order:
        try:
            m = cls(); log.info("dimming method: %s", m.name); return m, errors
        except Exception as e:
            errors.append(f"{cls.name}: {e}")
    raise RuntimeError("; ".join(errors))

# ---------------------------------------------------------------- loop
class Dimmer:
    def __init__(self, method_obj, errors=()):
        self.m, self.errors, self.applied = method_obj, list(errors), None
    def step(self):
        d = display_settings()
        night = bool(read_json(POWER_STATE, {}).get("illum", False))
        want = max(MIN_LEVEL[self.m.name], target_level(d, night))
        if want != self.applied:
            self.m.set(want); self.applied = want
            STATUS.parent.mkdir(parents=True, exist_ok=True)
            STATUS.write_text(json.dumps({"method": self.m.name, "label": self.m.label, "applied": want, "night": night,
                                          "min": MIN_LEVEL[self.m.name], "notes": self.errors, "t": time.time()}))
        return True

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = display_settings()
    for _ in range(60):                       # the desktop may still be starting
        try:
            m, errs = pick(d.get("method", "auto")); break
        except Exception as e:
            log.info("waiting for display: %s", e); time.sleep(2)
    else:
        sys.exit("no dimming method available")
    dim = Dimmer(m, errs)
    if isinstance(m, Overlay):                # GTK main loop drives the overlay; poll settings from it
        m.GLib.timeout_add(250, dim.step); dim.step(); m.Gtk.main()
    else:
        while True:
            dim.step(); time.sleep(0.25)

if __name__ == "__main__":
    main()
