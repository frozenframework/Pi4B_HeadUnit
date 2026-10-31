"""Steering-wheel controls: learn mode + matcher + debounce/repeat, with a simulated resistor ladder."""
import importlib.util, json, os, sys, tempfile
BIN = os.path.join(os.path.dirname(__file__), "..", "..", "bin")
tmp = tempfile.mkdtemp(); os.environ["HU_SWC_MAP"] = os.path.join(tmp, "swc.json")
spec = importlib.util.spec_from_file_location("swc", os.path.join(BIN, "hu-swc.py")); swc = importlib.util.module_from_spec(spec); spec.loader.exec_module(swc)
swc.time.sleep = lambda s: None          # learn() without real waiting

# Simulated Nissan-style wheel: two key wires, 1 kOhm pull-ups. Button resistance R -> V = R / (R + 1k)
ladder = {"vol_up": (0, 0.0), "vol_down": (0, 330.0), "next": (0, 1000.0), "prev": (0, 3300.0),
          "mode": (1, 0.0), "answer": (1, 470.0), "end": (1, 1500.0)}
def v(ch, R): return R / (R + 1000.0) if R is not None else 1.0
state = {"btn": None}
def read():
    b = state["btn"]; ch, R = ladder[b] if b else (None, None)
    return {0: v(0, R) if ch == 0 else 0.998, 1: v(1, R) if ch == 1 else 0.997}

# --- learn: script the presses the way a person would (hold each, release, skip "mute")
order = [b for b, *_ in swc.BUTTONS]
calls = {"n": 0}
# drive: each button held for 30 reads then released for 5; "mute" never pressed -> times out (time mocked)
plan = []
for b in order:
    if b == "mute":
        plan += [None] * 300            # person doesn't have a mute button: waits out the 8 s prompt
        continue
    plan += [b] * 30 + [None] * 5
it = iter(plan)
def learn_read():
    calls["n"] += 1
    if calls["n"] <= 40: state["btn"] = None
    else: state["btn"] = next(it, None)
    return read()
t = {"now": 0.0}
swc.time.time = lambda: (t.__setitem__("now", t["now"] + 0.03) or t["now"])
swc.learn(learn_read, [0, 1])
m = json.load(open(os.environ["HU_SWC_MAP"]))
assert set(m["buttons"]) == set(ladder), m["buttons"].keys()
print("learned:", {k: (b["ch"], b["v"]) for k, b in m["buttons"].items()})

# --- run: debounce, mapping, repeat
sent = []
r = swc.Runner(swc.Matcher(m), {n: a for n, _, a in swc.BUTTONS}, sent.append)
def hold(b, t0, secs):
    state["btn"] = b; tt = t0
    while tt < t0 + secs: r.tick(read(), tt); tt += 0.02
    state["btn"] = None; r.tick(read(), tt); r.tick(read(), tt + 0.02); return tt + 0.1
tt = hold("next", 0, 0.03)                                # 30 ms blip: ignored (debounce 60 ms)
assert sent == [], sent
tt = hold("next", tt, 0.3); assert sent == ["now_playing_next_track"], sent
tt = hold("answer", tt, 0.2); assert sent[-1] == "answer_call"
sent.clear(); tt = hold("vol_up", tt, 1.5)                # held 1.5 s: 1 + repeats every 0.2 s after 0.5 s
assert sent.count("output_volume_up") >= 5 and set(sent) == {"output_volume_up"}, sent
sent.clear(); tt = hold("prev", tt, 1.5); assert sent == ["now_playing_previous_track"], "seek doesn't repeat"
print("runner ok")
