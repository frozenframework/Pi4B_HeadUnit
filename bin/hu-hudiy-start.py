#!/usr/bin/env python3
"""
Which screen Hudiy shows when the head unit starts (Settings > Driving > "Start on the map").

  hu-hudiy-start.py --config   before Hudiy starts (desktop autostart): sets Hudiy's
                               application.defaultAction, the screen it opens first and goes back to
                               with the home button: "hu_navigation" (our offline map) or "" (Hudiy's
                               own dashboard)
  hu-hudiy-start.py --open     in the background right after Hudiy is launched: once Hudiy's API answers,
                               asks it once to open the map (DispatchAction), so the map is on screen
                               even if this Hudiy version only uses defaultAction for the home button

A phone that connects later still brings up CarPlay / Android Auto as usual. Setting "startOnMap": false
in ~/.config/headunit/settings.json (the switch in Settings) gives Hudiy's dashboard instead.
"""
import json, socket, sys, time
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".hudiy" / "share" / "config" / "main_configuration.json"
SETTINGS = HOME / ".config" / "headunit" / "settings.json"
MAP_ACTION = "hu_navigation"            # the Navigation app's action in applications.json (hu-hudiy-config.py)

def start_on_map():
    try: return json.loads(SETTINGS.read_text()).get("startOnMap", True) is not False
    except (OSError, ValueError): return True

def apply_config(on_map=None):
    """Set application.defaultAction. Returns True if the file changed."""
    on_map = start_on_map() if on_map is None else on_map
    if not CFG.exists(): return False
    data = json.loads(CFG.read_text())
    app = data.setdefault("application", {})
    want = MAP_ACTION if on_map else ""
    if app.get("defaultAction", "") == want: return False
    app["defaultAction"] = want
    tmp = CFG.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=4)); tmp.replace(CFG)
    return True

def open_map(wait=60):
    """Wait for Hudiy's API, then dispatch the map action once."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hu_hudiy_api import api_port, send_once, DISPATCH_ACTION, dispatch_action
    port, end = api_port(), time.time() + wait
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close(); break
        except OSError:
            time.sleep(1)
    else:
        return False
    time.sleep(2)                                   # let Hudiy finish drawing its first screen
    return send_once("headunit-start", [(DISPATCH_ACTION, dispatch_action(MAP_ACTION))])

def main():
    if "--config" in sys.argv:
        changed = apply_config()
        print("Hudiy start screen:", "map" if start_on_map() else "Hudiy dashboard", "(updated)" if changed else "")
    if "--open" in sys.argv and start_on_map():
        print("open map:", "sent" if open_map() else "Hudiy API didn't answer")

if __name__ == "__main__":
    main()
