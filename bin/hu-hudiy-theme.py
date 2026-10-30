#!/usr/bin/env python3
"""Optional: recolor Hudiy to match the selected map skin (Settings > Driving > Match Hudiy colors).
Changes darkThemeEnabled/sourceColor wherever they appear in main_configuration.json, then restarts Hudiy."""
import json, os, subprocess, sys, time
from pathlib import Path

theme_id = sys.argv[1]
themes = json.loads(Path("/opt/headunit/apps/styles/themes.json").read_text())
t = next((x for x in themes if x["id"] == theme_id), None)
cfg = Path.home() / ".hudiy" / "share" / "config" / "main_configuration.json"
if not t or not cfg.exists():
    sys.exit(0)
data = json.loads(cfg.read_text()); changed = []
def walk(o):
    if isinstance(o, dict):
        for k in list(o):
            if k == "sourceColor" and isinstance(o[k], str): o[k] = t["hudiyColor"]; changed.append(k)
            elif k == "darkThemeEnabled" and isinstance(o[k], bool): o[k] = bool(t["dark"]); changed.append(k)
            else: walk(o[k])
    elif isinstance(o, list):
        for v in o: walk(v)
walk(data)
if not changed:
    print("no theme keys found in Hudiy config; nothing changed"); sys.exit(0)
cfg.write_text(json.dumps(data, indent=4))
run = Path.home() / ".hudiy" / "share" / "hudiy_run.sh"
env = dict(os.environ, WAYLAND_DISPLAY=os.environ.get("WAYLAND_DISPLAY", "wayland-0"), XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}")
if run.exists() and subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "hudiy"], capture_output=True).returncode == 0:
    subprocess.run(["pkill", "-u", str(os.getuid()), "-f", "/\\.hudiy/"])
    time.sleep(2)
    subprocess.Popen(["setsid", "bash", str(run)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Hudiy recolored:", t["name"])
