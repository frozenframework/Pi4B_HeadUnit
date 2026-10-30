#!/usr/bin/env python3
"""Draws the small icon sprite each theme needs (one-way arrow, route shield, POI dot, town dot).
Output: ../apps/sprites/<theme>/sprite(.png|.json) and sprite@2x. Requires Pillow."""
import json, os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
themes = json.load(open(os.path.join(HERE, "themes.json")))["themes"]

def hex2rgba(h, a=255):
    h = h.lstrip("#"); return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)

def draw(theme, r):
    m = theme["map"]
    icons = {}
    W = 160 * r; H = 40 * r
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    x = 0
    # shield: 30x18 rounded rectangle, highway color with halo outline (text is drawn by MapLibre)
    sw, sh = 30 * r, 18 * r
    d.rounded_rectangle([x + r, r, x + sw - r, sh - r], radius=4 * r, fill=hex2rgba(m["highway"]), outline=hex2rgba(m["label_halo"]), width=max(1, r))
    icons["shield"] = {"x": x, "y": 0, "width": sw, "height": sh, "pixelRatio": r, "content": [4 * r, 2 * r, sw - 4 * r, sh - 2 * r], "stretchX": [[8 * r, sw - 8 * r]]}
    x += sw + 2 * r
    # one-way arrow 14x10
    aw, ah = 14 * r, 10 * r
    d.polygon([(x + 1 * r, 3 * r), (x + 8 * r, 3 * r), (x + 8 * r, 0), (x + 13 * r, 5 * r), (x + 8 * r, 10 * r), (x + 8 * r, 7 * r), (x + 1 * r, 7 * r)], fill=hex2rgba(m["label_minor"], 220))
    icons["arrow"] = {"x": x, "y": 0, "width": aw, "height": ah, "pixelRatio": r}
    x += aw + 2 * r
    # poi dot 10x10
    pw = 10 * r
    d.ellipse([x + r, r, x + pw - r, pw - r], fill=hex2rgba(m["label"]), outline=hex2rgba(m["label_halo"]), width=max(1, r))
    icons["poi"] = {"x": x, "y": 0, "width": pw, "height": pw, "pixelRatio": r}
    x += pw + 2 * r
    # town / capital 12x12 ring
    tw = 12 * r
    d.ellipse([x + r, r, x + tw - r, tw - r], fill=hex2rgba(m["label_halo"]), outline=hex2rgba(m["city_label"]), width=2 * r)
    icons["townspot"] = {"x": x, "y": 0, "width": tw, "height": tw, "pixelRatio": r}
    x += tw + 2 * r
    d.ellipse([x + r, r, x + tw - r, tw - r], fill=hex2rgba(m["city_label"]), outline=hex2rgba(m["label_halo"]), width=2 * r)
    icons["capital"] = {"x": x, "y": 0, "width": tw, "height": tw, "pixelRatio": r}
    return img, icons

for t in themes:
    out = os.path.join(HERE, "..", "apps", "sprites", t["id"]); os.makedirs(out, exist_ok=True)
    for r, suffix in ((1, ""), (2, "@2x")):
        img, icons = draw(t, r)
        img.save(os.path.join(out, f"sprite{suffix}.png"))
        json.dump(icons, open(os.path.join(out, f"sprite{suffix}.json"), "w"))
    print("sprite", t["id"])
