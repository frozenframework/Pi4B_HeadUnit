#!/usr/bin/env python3
"""Adds the head-unit apps to Hudiy (applications.json + applications_menu.json) and sets
Android Auto's GPS source to gpsd. Keeps a one-time backup of each file it touches (*.hu-orig)."""
import json, shutil
from pathlib import Path

CFG = Path.home() / ".hudiy" / "share" / "config"
LOADER = "file:///opt/headunit/apps/loader.html?to="
APPS = [
    {"action": "hu_navigation", "label": "Navigation", "icon": "map", "page": "nav", "bg": True},
    {"action": "hu_music", "label": "iPod & USB", "icon": "library_music", "page": "music", "bg": False},
    {"action": "hu_radio", "label": "AM/FM Radio", "icon": "radio", "page": "radio", "bg": False},
    {"action": "hu_settings", "label": "Head Unit Settings", "icon": "palette", "page": "theme", "bg": False},
]

def load(name, default):
    p = CFG / name
    if not p.exists():
        return default
    if not (CFG / (name + ".hu-orig")).exists():
        shutil.copy2(p, CFG / (name + ".hu-orig"))
    return json.loads(p.read_text())

def save(name, data):
    (CFG / name).write_text(json.dumps(data, indent=4))

apps = load("applications.json", {"applications": []})
apps["applications"] = [a for a in apps.get("applications", []) if not a.get("action", "").startswith("hu_")]
for a in APPS:
    apps["applications"].append({"action": a["action"], "url": LOADER + a["page"], "allowBackground": a["bg"],
                                 "controlAudioFocus": False, "audioStreamCategory": "NONE", "zoomFactor": 1.0})
save("applications.json", apps)

menu = load("applications_menu.json", {"categories": [], "items": []})
menu["categories"] = [c for c in menu.get("categories", []) if c.get("label") != "Drive"]
menu["categories"].insert(0, {"iconFontFamily": "Material Symbols Rounded", "iconName": "directions_car", "label": "Drive"})
menu["items"] = [i for i in menu.get("items", []) if not i.get("action", "").startswith("hu_")]
for a in APPS:
    menu["items"].append({"categories": ["Drive"], "iconFontFamily": "Material Symbols Rounded", "iconName": a["icon"],
                          "action": a["action"], "label": a["label"]})
save("applications_menu.json", menu)

def conf(path="/etc/headunit/headunit.conf"):
    c = {}
    try:
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); c[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return c
HC = conf()
SCREEN = {"waveshare-7.9": (1280, 400), "hdmi-1024x600": (1024, 600), "pi-touch-display-2": (1280, 720)}.get(HC.get("DISPLAY_TYPE", "waveshare-7.9"), (1280, 400))
# Factory backup camera: a USB composite capture stick (NTSC 720x480), drawn full screen by Hudiy.
CAMERA_PIPELINE = "v4l2src device=/dev/hu-rearcam ! video/x-raw,width=720,height=480 ! videoconvert ! glupload ! qml6glsink name=video-sink"

# Android Auto gets the Pi's GPS (better than the phone's inside a metal cab); imperial units for CarPlay maneuvers.
main = load("main_configuration.json", None)
if main is not None:
    def walk(o, parent=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "gpsDataSource" and parent == "androidAuto": o[k] = "GPSD"
                elif k == "navigationUnits": o[k] = "imperial"
                else: walk(v, k)
    walk(main)
    if HC.get("CAMERA") == "1":
        cam = {"width": SCREEN[0], "height": SCREEN[1], "x": 0, "y": 0, "pipeline": CAMERA_PIPELINE}
        def find(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "reverseCamera" and isinstance(v, dict): return v
                    r = find(v)
                    if r is not None: return r
            return None
        rc = find(main)
        if rc is None: main["reverseCamera"] = cam
        else: rc.update(cam)
        print("reverse camera:", SCREEN, CAMERA_PIPELINE)
    save("main_configuration.json", main)
print("Hudiy config updated:", ", ".join(a["label"] for a in APPS))
