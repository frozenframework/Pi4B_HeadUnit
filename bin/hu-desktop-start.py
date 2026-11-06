#!/usr/bin/env python3
"""
Desktop autostart, before Hudiy: everything Hudiy needs ready, in ONE quick program instead of three
(each Python start costs a few hundred milliseconds on the Pi, and Hudiy waits for all of them):
  - Hudiy's colors for the map skin (day / night) - only written if they changed (bin/hu-hudiy-theme.py)
  - Hudiy's first screen: the map (Settings > Driving > Start on the map) (bin/hu-hudiy-start.py)
  - a "desktop" mark for the start-up timeline (Settings > Developer)
"""
import importlib.util, sys, time
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m

def main():
    import hu_boot
    hu_boot.mark("desktop")
    try:
        th = load("hu_hudiy_theme", "hu-hudiy-theme.py")
        skin = th.current_skin()
        if skin:
            import json
            themes = json.loads((th.STYLES / "themes.json").read_text())
            if any(x["id"] == skin for x in themes):
                print("Hudiy skin:", "updated" if th.apply_config(th.plan(themes, skin)) else "unchanged")
    except Exception as e:
        print("Hudiy skin:", e)
    try:
        st = load("hu_hudiy_start", "hu-hudiy-start.py")
        print("Hudiy start screen:", "updated" if st.apply_config() else "unchanged")
    except Exception as e:
        print("Hudiy start screen:", e)

if __name__ == "__main__":
    main()
