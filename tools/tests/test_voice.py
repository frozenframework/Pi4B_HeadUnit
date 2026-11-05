"""Voice packs: the four built-in personalities and the Standard voice load; each says its own lines
(templates with the distance and turn, the first turn after "route set"), recorded clips win over
speech, other head-unit languages fall back to the Standard voice, speech is queued and cached, Piper
models download with resume (espeak stands in until then), and packs import from a USB folder."""
import asyncio, json, os, shutil, sys, tempfile, threading
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
assert set(ps) == {"standard", "neon-guide", "race-caller", "street-narrator", "cruise-control"}, set(ps)
for pid, p in ps.items():
    if pid == "standard": continue
    assert p["engine"] == "piper" and hu_voice.MODEL_RE.match(p["model"]), pid
    for k in ("greet", "route_started", "rerouting", "arrived", "in_dist", "sample"): assert p["lines"].get(k), (pid, k)
    hu_voice.model_url(p["model"], ".onnx")
print("1) five packs; every personality has all its lines and a valid Piper voice name")

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
print("5) Piper model downloads, survives a cut-off and resumes from the last byte")
print("voice packs: all tests passed")
