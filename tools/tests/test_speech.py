"""Natural speech: directions read the way a person says them (distances, road names, highways,
sentence flow, two close turns in one breath, per-pack wording), Piper audio shaped into one smooth
line (breaths between sentences, soft edges, one loudness, lead-in silence), and voice prompts lower
other audio through Hudiy's audio focus (registered after hello; duck before, release after)."""
import asyncio, io, os, random, sys, tempfile, time, wave
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin")); sys.path.insert(0, str(Path(__file__).resolve().parent))
from hu_speech import humanize, say_distance
import hu_hudiy_api as H

cases = {
    "In 0.5 miles, Turn left onto N Main St.": "In half a mile, turn left onto North Main Street.",
    "Take exit 87B toward I-45 S/US-59.": "Take exit 87 B toward Interstate 45 South, U.S. 59.",
    "In 1.5 miles, Keep right to take TX-105 W.": "In a mile and a half, keep right to take Texas 105 West.",
    "Turn right onto St Johns Rd": "Turn right onto Saint Johns Road.",
    "Continue on FM 1488 for 2.3 miles.": "Continue on F.M. 1488 for 2 and a half miles.",
    "In 500 ft, turn left onto Dr Martin Luther King Jr Blvd.": "In 500 feet, turn left onto Doctor Martin Luther King Junior Boulevard.",
    "Turn left onto Main St. Then Turn right.": "Turn left onto Main Street. Then turn right.",
    "Turn right onto W Davis St, then Turn left onto Frazier St": "Turn right onto West Davis Street, then turn left onto Frazier Street.",
    "Your destination is on the right": "Your destination is on the right.",
    "In 12.4 miles, Take the exit on the right.": "In 12 miles, take the exit on the right.",
    "Continue on CR 123 for 0.25 miles..": "Continue on County Road 123 for a quarter mile.",
}
for src, want in cases.items():
    got = humanize(src); assert got == want, (src, got)
assert [say_distance(v, "miles") for v in (0.1, 0.5, 0.75, 1.0, 1.5, 3.1, 3.5, 3.9, 25.4)] == \
    ["0.1 miles", "half a mile", "three quarters of a mile", "a mile", "a mile and a half", "about 3 miles", "3 and a half miles", "almost 4 miles", "25 miles"]
r = random.Random(1)
assert humanize("Turn left onto Elm St, then Turn right onto Oak Ave.", {"Turn left": ["Hang a left"], "Turn right": ["Make a right"]}, r) \
    == "Hang a left onto Elm Street, then make a right onto Oak Avenue."
print("1) directions read like a person says them:", humanize("In 0.5 miles, Turn left onto N Main St."))

# 2) Piper audio shaping (numpy only; Piper itself isn't needed for this part)
import numpy as np
from hu_voice import shape_speech, pcm_wav
rate = 22050
s1 = (0.3 * np.sin(np.linspace(0, 400, rate))).astype(np.float32); s2 = (0.05 * np.sin(np.linspace(0, 300, rate // 2))).astype(np.float32)
out = shape_speech([s1, s2], rate, pause=0.25)
assert abs(float(np.max(np.abs(out))) - 0.86) < 0.01, "one loudness for the whole line"
lead = int(rate * 0.12); assert np.all(out[:lead] == 0), "lead-in silence wakes the amplifier first"
gap = out[lead + len(s1) + 5: lead + len(s1) + int(rate * 0.25) - 5]; assert np.all(gap == 0), "a breath between sentences"
assert abs(out[lead]) < 1e-6 and abs(out[lead + len(s1) - 1]) < 0.02, "soft edges, no clicks"
q = out[lead + len(s1) + int(rate * 0.25):][: len(s2)]
assert np.max(np.abs(q)) > 0.1, "the quiet sentence is brought up with the rest (not left at 0.05)"
w = wave.open(io.BytesIO(pcm_wav(out, rate))); assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (rate, 2, 1)
print("2) Piper lines: lead-in, breaths between sentences, soft edges, even loudness")

# 3) audio focus: register after hello, duck before speaking, release after
from fake_hudiy import FakeHudiy
REG_OK = bytes([0x08, 1, 0x10, 7])                         # result OK, id 7
hud = FakeHudiy(after_hello=[], delay=0.2)
link = H.HudiyLink("headunit-voice-test", port=hud.port, audio_focus=("headunit-voice", H.CATEGORY_COMMUNICATION, 100))
time.sleep(0.8)
assert not hud.early and H.REGISTER_AUDIO_FOCUS_RECEIVER_REQUEST in hud.ids(), hud.ids()
reg = H.decode(dict(hud.got)[H.REGISTER_AUDIO_FOCUS_RECEIVER_REQUEST]); assert reg == {1: [b"headunit-voice"], 2: [2], 3: [100]}, reg
assert link.duck() is False, "no receiver id yet: nothing sent"
link.focus_id = 7                                           # (the fake doesn't answer the registration)
from hu_voice import Voices
T = Path(tempfile.mkdtemp()); (T / "play").write_text("#!/bin/sh\ncat > /dev/null\n"); (T / "play").chmod(0o755)
(T / "espeak").write_text("#!/bin/sh\nprintf RIFFfake\n"); (T / "espeak").chmod(0o755)
v = Voices(ROOT / "voices", T / "data", play_cmd=[str(T / "play")], espeak=str(T / "espeak"))
v.on_speak_start, v.on_speak_end = link.duck, link.release
asyncio.run(v.say("standard", "turn", "Turn left onto Main St.", {}, "en", "en-us")); time.sleep(0.3)
changes = [H.decode(p) for m, p in hud.got if m == H.AUDIO_FOCUS_CHANGE_REQUEST]
assert changes == [{1: [7], 2: [H.FOCUS_DUCK]}, {1: [7], 2: [H.FOCUS_RELEASE]}], changes
print("3) speech lowers other audio: registered after hello, duck before, release after")
print("speech: all tests passed")
