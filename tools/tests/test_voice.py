"""Voice packs: the four built-in personalities and the Standard voice load; each says its own lines
(templates with the distance and turn, the first turn after "route set"), recorded clips win over
speech, other head-unit languages fall back to the Standard voice, speech is queued and cached, Piper
models download with resume (espeak stands in until then), and packs import from a USB folder."""
import asyncio, json, os, shutil, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import hu_voice
from hu_voice import Voices

T = Path(tempfile.mkdtemp()); BIN = T / "bin"; BIN.mkdir()
(BIN / "espeak").write_text(f'#!/bin/sh\necho "espeak $*" >> {T}/espeak.log\nprintf RIFFfake\n'); (BIN / "espeak").chmod(0o755)
(BIN / "play").write_text(f'#!/bin/sh\ncat >> {T}/played.bin\necho >> {T}/played.bin\n'); (BIN / "play").chmod(0o755)
v = Voices(ROOT / "voices", T / "data", play_cmd=[str(BIN / "play")], espeak=str(BIN / "espeak"))

ps = v.packs()
ORIG = {"neon-guide", "race-caller", "street-narrator", "cruise-control"}
assert set(ps) == {"standard"} | ORIG | {f"{x}-hd" for x in ORIG}, set(ps)
for pid, p in ps.items():
    if pid == "standard": continue
    assert p["engine"] == "piper" and hu_voice.MODEL_RE.match(p["model"]), pid
    for k in ("greet", "route_started", "rerouting", "arrived", "in_dist", "sample"): assert p["lines"].get(k), (pid, k)
    hu_voice.model_url(p["model"], ".onnx")
for x in ORIG:                                   # the HD packs: same personality and lines, Piper's high-quality voice
    hd, o = ps[f"{x}-hd"], ps[x]
    assert hd["hd"] and hd["model"].endswith("-high") and hd["lines"] == o["lines"] and hd["name"] == o["name"] + " HD", x
    assert hd["dir"].endswith(f"{x}-hd") and not o.get("hd")
assert len({ps[f"{x}-hd"]["model"] for x in ORIG}) == 4, "each HD pack its own speaker"
print("1) nine packs: Standard, the four personalities, and each in HD; all lines, valid Piper voice names")

rc = v.pack("race-caller")
kind, said = v.compose(rc, "in_dist", "In 0.5 miles, Turn left.", {"dist": "0.5 miles", "text": "Turn left onto Main Street"}, "en")
assert said == "Coming up in 0.5 miles: Turn left onto Main Street!", said
kind, said = v.compose(rc, "route_started", "Head north.", {"then": "Head north on Elm Street."}, "en")
assert said.endswith(" Head north on Elm Street.") and said.split("!")[0] in ("And they're off", "Green light"), said
assert v.compose(rc, "turn", "Turn left onto Main Street.", {}, "en") == ("text", "Turn left onto Main Street.")
assert v.compose(v.pack("standard"), "arrived", "You have arrived.", {}, "en") == ("text", "You have arrived.")
assert v.compose(v.pack("standard"), "greet", "", {}, "en") == ("text", "")
assert v.compose(rc, "arrived", "Ha llegado.", {}, "es") == ("text", "Ha llegado."), "pack lines are English only"
print("2) lines: templates with distance + turn, first turn after 'route set', plain turns, other languages")

# 2b) directions always in the same words (so they can be spoken ahead of time); greetings vary
cc = dict(v.pack("cruise-control")); cc["phrases"] = {"Turn left": ["Turn left", "Take a left", "Hang a left"]}
same = {v.words("cruise-control", "in_dist", "In 0.5 miles, Turn left onto Elm St.", {"dist": "0.5 miles", "text": "Turn left onto Elm St"}, "en")[2] for _ in range(8)}
assert len(same) == 1, same
greets = {v.words("neon-guide", "greet", "", {}, "en")[2] for _ in range(40)}
assert len(greets) > 1, "greetings still vary"
print("2b) directions: the same words every time (cacheable); greetings vary")

# 2c) spoken ahead of time: the next turns go into the cache in the background, then play at once
import time as _t
items = [{"key": "in_dist", "text": "In 0.5 miles, Turn left onto Oak St.", "vars": {"dist": "0.5 miles", "text": "Turn left onto Oak St"}},
         {"key": "turn", "text": "Turn left onto Oak St.", "vars": {}}, "junk"]
assert v.prepare("street-narrator", items, "en", "en-us") == 2
for _ in range(100):
    if v.prepared >= 2 and not (v.prep_thread and v.prep_thread.is_alive()): break
    _t.sleep(0.05)
before = len((T / "espeak.log").read_text().splitlines()) if (T / "espeak.log").exists() else 0
asyncio.run(v.say("street-narrator", "turn", "Turn left onto Oak St.", {}, "en", "en-us"))
after = len((T / "espeak.log").read_text().splitlines())
assert v.prepared >= 2 and after == before, "the prompt was ready: nothing worked out at the turn"
assert v.prepare_lines("neon-guide", "en", "en-us") >= 4, "greetings / rerouting / arrived ahead of time"
(T / "played.bin").unlink()
nav = (ROOT / "apps/nav/nav.js").read_text()
assert "api('/api/say/prepare'" in nav and "say(P.heads.text, P.heads.key, P.heads.vars)" in nav, "the map asks for the next turns ahead"
print("2c) the next turns spoken ahead of time (background): the prompt plays with no delay")

# a pack of your own with recorded clips
mine = T / "stick" / "HEADUNIT_VOICES" / "My-Voice"; mine.mkdir(parents=True)
(mine / "voice.json").write_text(json.dumps({"name": "Mine", "language": "en", "engine": "espeak", "espeak": {"pitch": 70},
                                             "clips": {"arrived": ["here.wav", "../../etc/passwd"]}, "lines": {"greet": "Hi."}}))
(mine / "here.wav").write_bytes(b"RIFFclip"); (mine / "evil.sh").write_text("rm -rf /")
assert v.import_from(T / "stick" / "HEADUNIT_VOICES") == ["my-voice"]
assert sorted(x.name for x in (T / "data/packs/my-voice").iterdir()) == ["here.wav", "voice.json"]
mp = v.pack("my-voice"); assert mp["kind"] == "yours"
k, f = v.compose(mp, "arrived", "You have arrived.", {}, "en"); assert k == "clip" and f.name == "here.wav"
print("3) USB pack imported (only .json/.wav), recorded clip used, path tricks ignored")

async def speak():
    await asyncio.gather(v.say("neon-guide", "arrived", "You have arrived.", {}, "en", "en-us"),
                         v.say("my-voice", "arrived", "x", {}, "en", "en-us"),
                         v.say("street-narrator", "turn", "Turn right.", {}, "en", "en-us"),
                         v.say("race-caller", "turn", "dropped: queue full", {}, "en", "en-us"))
asyncio.run(speak())
played = (T / "played.bin").read_bytes()
assert played.count(b"RIFFfake") == 2 and b"RIFFclip" in played, played
log = (T / "espeak.log").read_text()
assert "-v en-us+f3" in log and "-p 62" in log and "-v en-gb+m1" in log, log    # each pack's own espeak sound until Piper is ready
n = len(log.splitlines())
asyncio.run(v.say("neon-guide", "turn", "Turn right.", {}, "en", "en-us")); asyncio.run(v.say("neon-guide", "turn", "Turn right.", {}, "en", "en-us"))
assert len((T / "espeak.log").read_text().splitlines()) == n + 1, "second time comes from the cache"
print("4) queued one at a time (backlog trimmed), each pack's own fallback sound, repeated lines cached")

# Piper model download with resume, from a stand-in server
from aiohttp import web, ClientSession
model = "en_US-joe-medium"; blob = os.urandom(1_500_000); cfgj = b'{"audio": {"sample_rate": 22050}}'; hits = []
async def serve(req):
    name = req.match_info["f"]; hits.append((name, req.headers.get("Range")))
    body = cfgj if name.endswith(".json") else blob
    a = int(req.headers["Range"][6:-1]) if req.headers.get("Range") else 0
    if name.endswith(".onnx") and len(hits) == 2:                  # first try of the big file is cut off
        r = web.StreamResponse(status=200, headers={"Content-Length": str(len(body))}); await r.prepare(req)
        await r.write(body[:600_000]); req.transport.close(); return r
    return web.Response(body=body[a:], status=206 if a else 200)
async def dl():
    app = web.Application(); app.router.add_get("/{f}", serve)
    runner = web.AppRunner(app); await runner.setup(); site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    hu_voice.MODEL_URL = f"http://127.0.0.1:{port}/" + "{model}{ext}"
    for pid in ("neon-guide", "race-caller", "street-narrator"): shutil.rmtree(T / "x", ignore_errors=True)
    v2 = Voices(T / "onlyjoe", T / "data")
    (T / "onlyjoe/cruise-control").mkdir(parents=True); shutil.copy(ROOT / "voices/cruise-control/voice.json", T / "onlyjoe/cruise-control/voice.json")
    async with ClientSession() as cs:
        await v2.download_models(cs)                                  # cut off -> waiting
        assert not v2.model_ready(model) and v2.downloads[model]["state"] == "waiting"
        await v2.download_models(cs)                                  # resumes
    await runner.cleanup()
    return v2
v2 = asyncio.run(dl())
assert v2.model_ready(model) and (T / "data/models" / f"{model}.onnx").read_bytes() == blob
assert any(r and r.startswith("bytes=6") for _, r in hits), hits
assert [x for x in v2.status("cruise-control") if x["id"] == "cruise-control"][0]["state"] == "ready"
# HD models are big: fetched only for the HD pack you pick
v3 = Voices(ROOT / "voices", T / "data3"); got = []
async def fake_fetch(session, url, dest, model, progress): got.append(model); raise IOError("stop")
v3._fetch = fake_fetch
asyncio.run(v3.download_models(None, first="neon-guide"))
assert got and not got[0].endswith("-high"), got
got.clear(); asyncio.run(v3.download_models(None, first="race-caller-hd")); assert got == ["en_US-ryan-high"], got
print("5) Piper model downloads, survives a cut-off and resumes from the last byte; an HD model only once it's picked")

# 6) the most common lines made once for the chosen Piper voice, and kept
import types, numpy as np
class _Cfg:
    def __init__(self, **k): pass
class _Chunk:
    def __init__(self): self.sample_rate = 22050; self.audio_float_array = (0.2 * np.sin(np.linspace(0, 300, 8000))).astype(np.float32)
class _Voice:
    calls = 0
    def synthesize(self, text, cfg): _Voice.calls += 1; yield _Chunk()
sys.modules["piper"] = types.SimpleNamespace(SynthesisConfig=_Cfg)
v4 = Voices(ROOT / "voices", T / "data4", play_cmd=[str(BIN / "play")], espeak=str(BIN / "espeak"), prep_delay=0)
(T / "data4/models").mkdir(parents=True)
for ext in (".onnx", ".onnx.json"): (T / "data4/models" / f"en_US-ryan-high{ext}").write_bytes(b"x")
v4._piper_voice = lambda m: _Voice()
assert v4.preload("neon-guide-hd", "en", "en-us") == 0, "model not there yet: nothing made with the stand-in voice"
n = v4.preload("race-caller-hd", "en", "en-us")
lines = v4.pack("race-caller-hd")["lines"]
fixed = sum(len(lines[k]) if isinstance(lines[k], list) else 1 for k in ("greet", "rerouting", "arrived"))
assert n == fixed + 3 * len(hu_voice.COMMON_TURNS), n
assert v4.status("race-caller-hd") and [x for x in v4.status("race-caller-hd") if x["id"] == "race-caller-hd"][0]["preload"]["total"] == n
for _ in range(400):
    if not v4.preloads and not (v4.prep_thread and v4.prep_thread.is_alive()): break
    time.sleep(0.02)
kept = list((T / "data4/cache/race-caller-hd/keep").glob("*.wav")); assert len(kept) == n, len(kept)
assert v4.preload("race-caller-hd", "en", "en-us") == 0, "made once"
c0 = _Voice.calls
asyncio.run(v4.say("race-caller-hd", "in_dist", "In 0.5 miles, Turn left.", {"dist": "0.5 miles", "text": "Turn left."}, "en", "en-us"))
asyncio.run(v4.say("race-caller-hd", "turn", "Keep right at the fork.", {}, "en", "en-us"))
assert _Voice.calls == c0, "common lines play straight from the kept copies"
for i in range(hu_voice.CACHE_MAX + 5): (T / "data4/cache/race-caller-hd" / f"x{i}.wav").write_bytes(b"x")
v4._prune(); assert len(list((T / "data4/cache/race-caller-hd/keep").glob("*.wav"))) == n, "kept lines aren't pruned"
assert v4.preload("race-caller-hd", "en", "en-us", units="metric") == 2 * len(hu_voice.COMMON_TURNS), "metric heads-ups too"
print("6) the chosen voice's common lines (greetings, the usual turns + heads-ups) made once, kept, played at once")
print("voice packs: all tests passed")
