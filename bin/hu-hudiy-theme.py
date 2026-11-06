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
      colorOverwrite                surface / primary / text roles set to the skin's own colors, and the
                                    inverse / container roles Hudiy's notifications and toasts use
      defaultBackground             the skin's background picture (apps/styles/hudiy-bg/<skin>.jpg)
  androidAuto.dayNightMode, autobox.dayNightMode = "common"
                                    Android Auto and CarPlay follow the dark/light setting, so a dark skin
                                    also puts CarPlay and Android Auto in their night look.
CarPlay's own screens are drawn by the iPhone; apart from that night/day look they can't be restyled.

Day and night skins (Settings > Map skin > Day and night): Hudiy's "light" theme section gets the day
skin and its "dark" section the night skin, and dark mode is on from sunset to sunrise. So at sunset the
bridge only has to turn Hudiy's dark mode on (--live): its colors, notifications, background and
CarPlay / Android Auto's night look all change together, without restarting Hudiy.
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

def user_settings():
    try: return json.loads(SETTINGS.read_text())
    except (OSError, ValueError): return {}

def current_skin():
    s = user_settings()
    if s.get("syncHudiyColors") is False: return None          # turned off in Settings > Driving
    return s.get("theme") or conf("DEFAULT_THEME", "neon-overdrive")

def plan(themes, skin):
    """{"dark": skin, "light": skin, "darkMode": bool}: with day and night skins on, the night skin in
    the dark section, the day skin in the light one, and dark mode while the night skin is showing."""
    by = {x["id"]: x for x in themes}; t = by[skin]; s = user_settings()
    day, night = by.get(s.get("dayTheme")), by.get(s.get("nightTheme"))
    if s.get("dayNight") and day and night and day["id"] != night["id"]:
        return {"dark": night, "light": day, "darkMode": skin == night["id"] or (skin != day["id"] and bool(t["dark"]))}
    return {"dark": t, "light": t, "darkMode": bool(t["dark"])}

def roles(t):
    u = t["ui"]
    return {"primary": u["accent"], "onPrimary": u["bg"], "secondary": u["accent2"], "tertiary": u["accent2"],
            "surface": u["bg"], "onSurface": u["text"], "background": u["bg"], "onBackground": u["text"],
            "surfaceVariant": u["panel"], "onSurfaceVariant": u["muted"],
            "surfaceContainerLowest": u["bg"], "surfaceContainerLow": u["panel"], "surfaceContainer": u["panel"],
            "surfaceContainerHigh": u["panel"], "surfaceContainerHighest": u["panel"],
            "outline": u["muted"], "outlineVariant": u["muted"], "error": u["warn"],
            # Hudiy's notifications, toasts (volume, now playing, phone, Bluetooth) and pop-ups are drawn with
            # the inverse and container roles: without these they keep Hudiy's default look on every skin
            "inverseSurface": u["panel"], "inverseOnSurface": u["text"], "inversePrimary": u["accent"],
            "primaryContainer": u["panel"], "onPrimaryContainer": u["accent"],
            "secondaryContainer": u["panel"], "onSecondaryContainer": u["text"],
            "tertiaryContainer": u["panel"], "onTertiaryContainer": u["accent2"],
            "errorContainer": u["panel"], "onErrorContainer": u["warn"],
            "onSecondary": u["bg"], "onTertiary": u["bg"], "onError": u["bg"],
            "surfaceDim": u["bg"], "surfaceBright": u["panel"], "scrim": "#000000", "shadow": "#000000"}

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

def apply_config(p):
    if not CFG.exists(): return False
    data = json.loads(CFG.read_text())
    orig = CFG.with_name(CFG.name + ".hu-orig")
    if not orig.exists(): orig.write_text(CFG.read_text())
    def walk(o, parent=""):
        if isinstance(o, dict):
            if "darkThemeEnabled" in o: o["darkThemeEnabled"] = p["darkMode"]
            if "sourceColor" in o:
                t = p["light"] if parent == "light" else p["dark"]       # Hudiy's light section = the day skin
                bg = background(t)
                o["sourceColor"] = t["hudiyColor"]
                o["colorOverwrite"] = {**(o.get("colorOverwrite") or {}), **roles(t)}
                if bg: o["defaultBackground"] = bg
            if parent in ("androidAuto", "autobox") and "dayNightMode" in o:   # same spelling as Hudiy's own file
                o["dayNightMode"] = "COMMON" if str(o["dayNightMode"]).isupper() else "common"
            for k, v in o.items(): walk(v, k)
        elif isinstance(o, list):
            for v in o: walk(v, parent)
    walk(data)
    tmp = CFG.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=4)); tmp.replace(CFG)
    return True

def apply_live(p):
    """Recolor the running Hudiy: SetDarkMode (37) and SetThemeColors (54) from its API."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hu_hudiy_api import send_once, set_dark_mode, set_theme_colors
    return send_once("headunit-theme", [(37, set_dark_mode(p["darkMode"])),
                                        (54, set_theme_colors(p["dark"]["hudiyColor"], p["light"]["hudiyColor"]))])

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skin = args[0] if args else current_skin()
    if not skin: print("Hudiy skin sync is off"); return
    themes = json.loads((STYLES / "themes.json").read_text())
    t = next((x for x in themes if x["id"] == skin), None)
    if not t: print("unknown skin", skin); return
    p = plan(themes, skin)
    ok = apply_config(p)
    both = f" (day {p['light']['name']}, night {p['dark']['name']}, {'night' if p['darkMode'] else 'day'} now)" if p["dark"] is not p["light"] else ""
    print(f"Hudiy skin: {t['name']}{both} ({'config written' if ok else 'Hudiy not installed yet'})")
    if "--live" in sys.argv:
        print("live recolor:", "sent" if apply_live(p) else "Hudiy API not reachable (applies at the next start)")

if __name__ == "__main__":
    main()
