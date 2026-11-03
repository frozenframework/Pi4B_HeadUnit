#!/bin/sh
# DISPLAY_TYPE=monitor only: Hudiy supports screens up to 1920x1080, so on a bigger monitor switch to
# the largest mode that fits before Hudiy starts. Runs from the desktop autostart (labwc).
command -v wlr-randr >/dev/null || exit 0
i=0; until wlr-randr >/dev/null 2>&1 || [ $i -ge 20 ]; do sleep 0.5; i=$((i+1)); done
wlr-randr --json 2>/dev/null | python3 -c '
import json, subprocess, sys
try: outs = json.load(sys.stdin)
except ValueError: sys.exit(0)
for o in outs:
    modes = o.get("modes") or []
    cur = next((m for m in modes if m.get("current")), None)
    if not o.get("enabled") or not cur or (cur["width"] <= 1920 and cur["height"] <= 1080): continue
    fits = [m for m in modes if m["width"] <= 1920 and m["height"] <= 1080]
    if not fits: continue
    b = max(fits, key=lambda m: (m["width"] * m["height"], bool(m.get("preferred")), m["refresh"]))
    mode = "%dx%d@%.3f" % (b["width"], b["height"], b["refresh"])
    print("headunit: %s %dx%d is above 1920x1080, using %s" % (o["name"], cur["width"], cur["height"], mode))
    subprocess.run(["wlr-randr", "--output", o["name"], "--mode", mode])
'
exit 0
