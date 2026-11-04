#!/usr/bin/env python3
"""Background pictures for Hudiy's own screens (home, menus, music player), one per map skin.
Original abstract designs in each skin's colors (no game artwork): run after editing tools/themes.json.
    python3 tools/gen-hudiy-backgrounds.py      -> apps/styles/hudiy-bg/<skin>.jpg (1920x1080)
"""
import json, math, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "apps" / "styles" / "hudiy-bg"
W, H = 1920, 1080

def rgb(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
def mix(a, b, t): return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

def vgradient(top, bottom):
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for y in range(H): d.line([(0, y), (W, y)], fill=mix(top, bottom, y / (H - 1)))
    return im

def glow_layer(draw_fn, blur):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); draw_fn(ImageDraw.Draw(layer))
    return Image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)), layer)

def neon_overdrive(t):
    ui = {k: rgb(v) for k, v in t["ui"].items()}
    im = vgradient(mix(ui["bg"], (40, 10, 60), .5), ui["bg"]).convert("RGBA")
    hy = int(H * .58)
    def grid(d):            # perspective grid toward a horizon
        for i in range(-24, 25):
            d.line([(W / 2 + i * 26, hy), (W / 2 + i * 260, H)], fill=ui["accent2"] + (150,), width=2)
        for k in range(1, 14):
            y = hy + (H - hy) * (k / 13) ** 2.1
            d.line([(0, y), (W, y)], fill=ui["accent2"] + (120,), width=2)
    im = Image.alpha_composite(im, glow_layer(grid, 3))
    rnd = random.Random(7)
    def skyline(d):         # generic blocky skyline, cyan edge light
        x = 0
        while x < W:
            w = rnd.randint(40, 120); h = rnd.randint(60, 330)
            d.rectangle([x, hy - h, x + w, hy], fill=ui["panel"] + (255,), outline=ui["accent"] + (200,), width=2)
            for wy in range(hy - h + 14, hy - 10, 22):
                for wx in range(x + 10, x + w - 10, 18):
                    if rnd.random() < .28: d.rectangle([wx, wy, wx + 6, wy + 8], fill=ui["warn"] + (170,))
            x += w + rnd.randint(4, 30)
    return Image.alpha_composite(im, glow_layer(skyline, 4))

def sunset_junction(t):
    ui = {k: rgb(v) for k, v in t["ui"].items()}
    im = vgradient((255, 196, 120), (255, 118, 92)).convert("RGBA")
    hy = int(H * .66)
    def sun(d):
        cx, cy, r = W * .7, hy - 40, 230
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 236, 170, 255))
        for k in range(6):  # retro sun stripes
            y = cy + 30 + k * 34
            d.rectangle([cx - r, y, cx + r, y + 8 + k * 3], fill=(255, 150, 105, 255))
    im = Image.alpha_composite(im, glow_layer(sun, 10))
    d = ImageDraw.Draw(im)
    d.rectangle([0, hy, W, H], fill=mix(ui["accent2"], (20, 60, 90), .35) + (255,))
    for k in range(18):     # sea glints
        y = hy + 8 + k * 22; d.line([(W * .55, y), (W * .85, y)], fill=(255, 220, 160, 90), width=3)
    return im

def street_heat(t):
    ui = {k: rgb(v) for k, v in t["ui"].items()}
    im = vgradient(ui["panel"], ui["bg"]).convert("RGBA")
    rnd = random.Random(3)
    def streaks(d):         # light trails sweeping across
        for _ in range(26):
            y0 = rnd.uniform(H * .25, H * .95); c = ui["accent"] if rnd.random() < .7 else ui["warn"]
            pts = [(x, y0 + math.sin(x / 260 + y0) * 40 - x * .18) for x in range(-50, W + 60, 40)]
            d.line(pts, fill=c + (rnd.randint(90, 200),), width=rnd.randint(2, 7))
        for _ in range(6):
            y0 = rnd.uniform(H * .2, H * .8)
            d.line([(x, y0 - x * .12) for x in range(-50, W + 60, 40)], fill=ui["accent2"] + (120,), width=3)
    return Image.alpha_composite(im, glow_layer(streaks, 6))

def after_midnight(t):
    ui = {k: rgb(v) for k, v in t["ui"].items()}
    im = vgradient(ui["panel"], ui["bg"]).convert("RGBA")
    rnd = random.Random(11)
    def bokeh(d):           # out-of-focus city lights
        for _ in range(140):
            r = rnd.randint(6, 46); x = rnd.uniform(0, W); y = rnd.uniform(H * .35, H)
            c = rnd.choice([ui["accent2"], ui["warn"], ui["accent"], (255, 255, 255)])
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (rnd.randint(25, 90),))
    im = Image.alpha_composite(im, glow_layer(bokeh, 5))
    def lines(d):           # route-like accent curves
        for k in range(3):
            pts = [(x, H * (.55 + k * .1) + math.sin(x / (300 + 60 * k)) * 50) for x in range(-40, W + 50, 30)]
            d.line(pts, fill=ui["accent"] + (150,), width=4)
    return Image.alpha_composite(im, glow_layer(lines, 4))

DESIGNS = {"neon-overdrive": neon_overdrive, "sunset-junction": sunset_junction,
           "street-heat": street_heat, "after-midnight": after_midnight}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for t in json.loads((ROOT / "tools" / "themes.json").read_text())["themes"]:
        img = DESIGNS[t["id"]](t).convert("RGB")
        img.save(OUT / f"{t['id']}.jpg", quality=86, optimize=True, progressive=True)
        print("wrote", OUT / f"{t['id']}.jpg")
