#!/usr/bin/env python3
"""
Skin Hudiy (the CarPlay / Android Auto app) to match the head unit's map skin.

  hu-hudiy-theme.py --startup        before Hudiy starts (desktop autostart): write the skin into its config
  hu-hudiy-theme.py <skin> [--live]  after a skin change in Settings: write the config and, with --live,
                                     recolor the running Hudiy through its API (no restart)

What it changes in ~/.hudiy/share/config/main_configuration.json (keys from Hudiy's main_configuration.md):
  theme.darkThemeEnabled            dark or light, from the skin
  every theme section with a sourceColor (dark and light):
      sourceColor                   the skin's accent color (Hudiy builds its Material 3 palette from it)
      colorOverwrite                surface / primary / text roles set to the skin's own colors
      defaultBackground             the skin's background picture (apps/styles/hudiy-bg/<skin>.jpg)
  androidAuto.dayNightMode, autobox.dayNightMode = "common"
                                    Android Auto and CarPlay follow the dark/light setting, so a dark skin
                                    also puts CarPlay and Android Auto in their night look.
CarPlay's own screens are drawn by the iPhone; apart from that night/day look they can't be restyled.
"""
import json, os, sys
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".hudiy" / "share" / "config" / "main_configuration.json"
STYLES = Path(os.environ.get("HU_STYLES", "/opt/headunit/apps/styles"))
SETTINGS = HOME / ".config" / "headunit" / "settings.json"
BG_DIR = Path(os.environ.get("HU_HUDIY_BG", "/var/lib/headunit/hudiy-backgrounds"))   # survives software updates

def conf(key, default=""):
    try:
        for line in Path(os.environ.get("HU_CONF", "/etc/headunit/headunit.conf")).read_text().splitlines():
            if line.startswith(key + "="): v = line.split("=", 1)[1].strip().strip('"')
        return v
    except (OSError, UnboundLocalError):
        return default

def current_skin():
    try: s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError): s = {}
    if s.get("syncHudiyColors") is False: return None          # turned off in Settings > Driving
    return s.get("theme") or conf("DEFAULT_THEME", "neon-overdrive")

def roles(t):
    u = t["ui"]
    return {"primary": u["accent"], "onPrimary": u["bg"], "secondary": u["accent2"], "tertiary": u["accent2"],
            "surface": u["bg"], "onSurface": u["text"], "background": u["bg"], "onBackground": u["text"],
            "surfaceVariant": u["panel"], "onSurfaceVariant": u["muted"],
            "surfaceContainerLowest": u["bg"], "surfaceContainerLow": u["panel"], "surfaceContainer": u["panel"],
            "surfaceContainerHigh": u["panel"], "surfaceContainerHighest": u["panel"],
            "outline": u["muted"], "outlineVariant": u["muted"], "error": u["warn"]}

def background(t):
    src = STYLES / "hudiy-bg" / f"{t['id']}.jpg"
    if not src.exists(): return None
    dst = BG_DIR / src.name
    try:
        BG_DIR.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime: dst.write_bytes(src.read_bytes())
        return str(dst)
    except OSError:
        return str(src)

def apply_config(t):
    if not CFG.exists(): return False
    data = json.loads(CFG.read_text())
    orig = CFG.with_name(CFG.name + ".hu-orig")
    if not orig.exists(): orig.write_text(CFG.read_text())
    bg, r = background(t), roles(t)
    def walk(o, parent=""):
        if isinstance(o, dict):
            if "darkThemeEnabled" in o: o["darkThemeEnabled"] = bool(t["dark"])
            if "sourceColor" in o:
                o["sourceColor"] = t["hudiyColor"]
                o["colorOverwrite"] = {**(o.get("colorOverwrite") or {}), **r}
                if bg: o["defaultBackground"] = bg
            if parent in ("androidAuto", "autobox") and "dayNightMode" in o: o["dayNightMode"] = "common"
            for k, v in o.items(): walk(v, k)
        elif isinstance(o, list):
            for v in o: walk(v, parent)
    walk(data)
    tmp = CFG.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=4)); tmp.replace(CFG)
    return True

def apply_live(t):
    """Recolor the running Hudiy: SetDarkMode (37) and SetThemeColors (54) from its API."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hu_hudiy_api import send_once, set_dark_mode, set_theme_colors
    return send_once("headunit-theme", [(37, set_dark_mode(t["dark"])), (54, set_theme_colors(t["hudiyColor"], t["hudiyColor"]))])

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skin = args[0] if args else current_skin()
    if not skin: print("Hudiy skin sync is off"); return
    themes = json.loads((STYLES / "themes.json").read_text())
    t = next((x for x in themes if x["id"] == skin), None)
    if not t: print("unknown skin", skin); return
    ok = apply_config(t)
    print(f"Hudiy skin: {t['name']} ({'config written' if ok else 'Hudiy not installed yet'})")
    if "--live" in sys.argv:
        print("live recolor:", "sent" if apply_live(t) else "Hudiy API not reachable (applies at the next start)")

if __name__ == "__main__":
    main()
