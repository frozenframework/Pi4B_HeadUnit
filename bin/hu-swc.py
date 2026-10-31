#!/usr/bin/env python3
"""
Steering-wheel controls for resistor-ladder wheels (Nissan and many others).

How the wheel works: each button connects a key wire to ground through a different resistor.
With a pull-up on the Pi side, every button gives its own voltage, which an MCP3008 ADC reads.
Values differ per vehicle, so instead of a fixed table this LEARNS your buttons once:

    sudo /opt/headunit/bin/hu-swc.py --learn      (press each button when asked)

then runs as headunit-swc.service and turns presses into Hudiy actions (volume, tracks, calls).
Wiring: each key wire -> MCP3008 CHn and a 1 kOhm pull-up to 3.3 V; wheel-control ground -> Pi GND.
"""
import json, os, sys, time, logging
from pathlib import Path

MAP_FILE = Path(os.environ.get("HU_SWC_MAP", "/etc/headunit/swc.json"))
BUTTONS = [  # name, prompt, default Hudiy action
    ("vol_up", "VOLUME UP", "output_volume_up"),
    ("vol_down", "VOLUME DOWN", "output_volume_down"),
    ("next", "SEEK / NEXT (up arrow)", "now_playing_next_track"),
    ("prev", "SEEK / PREVIOUS (down arrow)", "now_playing_previous_track"),
    ("mode", "SOURCE / MODE", "go_home"),
    ("mute", "MUTE (if your wheel has it)", "toggle_output_muted"),
    ("answer", "PHONE ANSWER / TALK", "answer_call"),
    ("end", "PHONE HANG UP", "end_call"),
]
REPEAT = {"vol_up", "vol_down"}          # held = keeps stepping
IDLE_MARGIN, STABLE_MS = 0.06, 60

log = logging.getLogger("hu-swc")

def conf(path="/etc/headunit/headunit.conf"):
    c = {}
    try:
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); c[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return c

def make_reader(channels):
    from gpiozero import MCP3008
    adcs = {ch: MCP3008(channel=ch) for ch in channels}
    return lambda: {ch: a.value for ch, a in adcs.items()}

def pressed(sample, idle):
    """The channel pulled lowest below its idle level, or None if nothing is pressed."""
    best = None
    for ch, v in sample.items():
        drop = idle.get(ch, 1.0) - v
        if drop > IDLE_MARGIN and (best is None or drop > best[2]):
            best = (ch, v, drop)
    return None if best is None else (best[0], best[1])

class Matcher:
    def __init__(self, mapping):
        self.buttons = mapping["buttons"]; self.idle = {int(k): v for k, v in mapping["idle"].items()}
        self.tol = mapping.get("tolerance", 0.035)
    def match(self, sample):
        p = pressed(sample, self.idle)
        if not p: return None
        ch, v = p
        cands = [(abs(b["v"] - v), name) for name, b in self.buttons.items() if b["ch"] == ch and abs(b["v"] - v) <= self.tol]
        return min(cands)[1] if cands else "unknown"

class Runner:
    """Debounce + repeat logic, separated from hardware so it can be tested."""
    def __init__(self, matcher, actions, send):
        self.m, self.actions, self.send = matcher, actions, send
        self.cand = None; self.cand_since = 0; self.active = None; self.next_repeat = 0
    def tick(self, sample, now):
        b = self.m.match(sample)
        if b != self.cand:
            self.cand, self.cand_since = b, now
            if b is None: self.active = None
            return
        if b and b != "unknown" and self.active is None and now - self.cand_since >= STABLE_MS / 1000:
            self.active = b; self.fire(b); self.next_repeat = now + 0.5
        elif self.active in REPEAT and now >= self.next_repeat:
            self.fire(self.active); self.next_repeat = now + 0.2
    def fire(self, b):
        act = self.actions.get(b)
        if act: self.send(act); log.info("%s -> %s", b, act)

def learn(read, channels):
    print("Steering-wheel learning. Don't touch the wheel for 2 seconds...")
    samples = [read() for _ in range(40) if not time.sleep(0.05)]
    idle = {ch: sum(s[ch] for s in samples) / len(samples) for ch in channels}
    print("idle levels:", {ch: round(v, 3) for ch, v in idle.items()})
    if all(v < 0.5 for v in idle.values()):
        sys.exit("All channels read low with nothing pressed: check the 1 kOhm pull-ups and 3.3 V.")
    buttons = {}
    for name, prompt, _ in BUTTONS:
        print(f"\nPress and HOLD  {prompt}  (or wait 8 s to skip)")
        t0 = time.time(); got = None
        while time.time() - t0 < 8:
            p = pressed(read(), idle)
            if p:
                vals = []
                for _ in range(10):
                    q = pressed(read(), idle)
                    if q and q[0] == p[0]: vals.append(q[1])
                    time.sleep(0.03)
                if len(vals) >= 8 and max(vals) - min(vals) < 0.03:
                    got = {"ch": p[0], "v": round(sum(vals) / len(vals), 4)}; break
            time.sleep(0.03)
        if got:
            clash = [n for n, b in buttons.items() if b["ch"] == got["ch"] and abs(b["v"] - got["v"]) < 0.05]
            print(f"  learned: channel {got['ch']} at {got['v']:.3f}" + (f"  (WARNING: very close to {clash})" if clash else ""))
            buttons[name] = got
            print("  release it...")
            while pressed(read(), idle): time.sleep(0.05)
        else:
            print("  skipped")
    mapping = {"idle": idle, "buttons": buttons, "tolerance": 0.035, "learned": time.strftime("%Y-%m-%d %H:%M")}
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAP_FILE.write_text(json.dumps(mapping, indent=1))
    print(f"\nSaved {len(buttons)} buttons to {MAP_FILE}. Start it with: sudo systemctl restart headunit-swc")

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    C = conf()
    channels = [int(x) for x in C.get("SWC_CHANNELS", "0,1").split(",") if x.strip()]
    read = make_reader(channels)
    if "--learn" in sys.argv:
        return learn(read, channels)
    if "--raw" in sys.argv:          # wiring check: shows live ADC values
        while True:
            print("  ".join(f"ch{ch}={v:.3f}" for ch, v in read().items()), flush=True); time.sleep(0.2)
    if not MAP_FILE.exists():
        sys.exit(f"No button map yet: run  sudo {sys.argv[0]} --learn")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hu_hudiy_api import HudiyLink
    link = HudiyLink("headunit-swc", port=int(C.get("HUDIY_API_PORT", 44405)))
    actions = {name: C.get(f"SWC_ACTION_{name.upper()}", default) for name, _, default in BUTTONS}
    r = Runner(Matcher(json.loads(MAP_FILE.read_text())), actions, link.action)
    while True:
        r.tick(read(), time.monotonic()); time.sleep(0.02)

if __name__ == "__main__":
    main()
