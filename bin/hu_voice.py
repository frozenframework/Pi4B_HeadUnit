"""
Voice packs for spoken directions (Settings > Driving > Voice). See voices/README.md for the pack format.

  built-in packs   /opt/headunit/voices/<id>/voice.json            (shipped with the software)
  your packs       /var/lib/headunit/voices/packs/<id>/voice.json  (+ recorded .wav clips)
  Piper models     /var/lib/headunit/voices/models/<model>.onnx(.json)  (downloaded on Wi-Fi, kept across updates)
  spoken cache     /var/lib/headunit/voices/cache/<pack>/<hash>.wav

A pack's lines are English; in other head-unit languages the Standard voice speaks the translated text.
While a pack's Piper model is still downloading, it speaks with its own espeak settings instead.
"""
import asyncio, hashlib, io, json, logging, os, random, re, shutil, subprocess, threading, time, wave
from hu_speech import humanize
from pathlib import Path

log = logging.getLogger("hu-voice")
MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{fam}/{code}/{name}/{q}/{model}{ext}?download=true"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MODEL_RE = re.compile(r"^([a-z]{2,3})_([A-Z]{2})-([A-Za-z0-9_]+)-(x_low|low|medium|high)$")
CACHE_MAX = 1200
RANDOM_KEYS = ("greet", "route_started", "rerouting", "arrived", "sample")   # a different line each time; directions: always the same words
PREP_MAX = 40
# Kept ready for every Piper pack (spoken once, never pruned): the directions that come up on most drives
# with no street name in them, said at the turn and as a heads-up - the same words the map sends (see
# prompts() in apps/nav/nav.js: "In {dist}, {text}" with the road's heads-up distance).
COMMON_TURNS = ["Turn left.", "Turn right.", "Bear left.", "Bear right.", "Make a sharp left.", "Make a sharp right.",
                "Make a left U-turn.", "Make a right U-turn.", "Keep left at the fork.", "Keep right at the fork.",
                "Keep straight at the fork.", "Take the exit on the right.", "Take the exit on the left.",
                "Take the ramp on the right.", "Take the ramp on the left.", "Merge.", "Merge left.", "Merge right.",
                "Continue.", "Enter the roundabout and take the 1st exit.", "Enter the roundabout and take the 2nd exit.",
                "Enter the roundabout and take the 3rd exit.", "You have arrived at your destination.",
                "Your destination is on the right.", "Your destination is on the left."]
HEADS_UP = {"imperial": ("0.5 miles", "1000 feet"), "metric": ("800 meters", "300 meters")}

def model_url(model, ext):
    m = MODEL_RE.match(model)
    if not m: raise ValueError(f"not a Piper voice name: {model}")
    fam, cc, name, q = m.groups()
    return MODEL_URL.format(fam=fam, code=f"{fam}_{cc}", name=name, q=q, model=model, ext=ext)

def shape_speech(chunks, rate, pause=0.22, volume=1.0, lead=0.12, tail=0.08, fade=0.008, peak=0.86):
    """Join sentence chunks (float32 arrays) into one natural-sounding line: soft edges, breaths between
    sentences, a little lead-in silence, one overall loudness."""
    import numpy as np
    f = max(1, int(rate * fade)); ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
    parts = [np.zeros(int(rate * lead), np.float32)]
    for i, a in enumerate(chunks):
        a = a.astype(np.float32).copy()
        if a.size > 2 * f: a[:f] *= ramp; a[-f:] *= ramp[::-1]
        parts.append(a)
        if i < len(chunks) - 1: parts.append(np.zeros(int(rate * pause), np.float32))
    parts.append(np.zeros(int(rate * tail), np.float32))
    out = np.concatenate(parts); m = float(np.max(np.abs(out))) if out.size else 0.0
    if m > 1e-6: out = out * (peak / m) * volume
    return np.clip(out, -1.0, 1.0)

def trim_silence(a, rate, floor_db=-42.0, keep=0.03):
    """Piper pads every sentence with its own silence (different each time): cut it back to `keep`
    seconds so the breaks between sentences are the ones we choose."""
    import numpy as np
    if a.size == 0: return a
    peak = float(np.max(np.abs(a)))
    if peak < 1e-6: return a[:0]
    loud = np.flatnonzero(np.abs(a) > peak * 10 ** (floor_db / 20))
    k = int(rate * keep)
    return a[max(0, loud[0] - k): min(a.size, loud[-1] + k + 1)]

def car_polish(audio, rate, presence_db=3.0, highpass_hz=110.0, ratio=2.5, threshold_db=-24.0, target_rms_db=-17.0, peak=0.89):
    """Make speech easy to follow over road noise (numpy only, done once per line and cached):
      - low rumble removed below ~110 Hz (car speakers and road noise boom there, it only muddies words),
      - a gentle lift around 3 kHz where consonants live (+3 dB: "fifth" vs "fifty"),
      - soft compression so quiet syllables and word endings don't drop under the engine,
      - every line at the same loudness (RMS of the speech, not its loudest peak), peaks kept under -1 dB."""
    import numpy as np
    x = audio.astype(np.float32)
    if x.size < 64 or float(np.max(np.abs(x))) < 1e-6: return x
    n = 1 << int(np.ceil(np.log2(x.size + rate // 10)))            # zero-padded: no wrap-around
    X = np.fft.rfft(x, n); f = np.fft.rfftfreq(n, 1.0 / rate)
    hp = 1.0 / np.sqrt(1.0 + (highpass_hz / np.maximum(f, 1.0)) ** 4)    # 2nd-order-like high-pass
    bell = 10 ** ((presence_db / 20) * np.exp(-0.5 * (np.log2(np.maximum(f, 1.0) / 3000.0) / 0.6) ** 2))
    y = np.fft.irfft(X * hp * bell, n)[: x.size].astype(np.float32)
    # compression on a 10 ms RMS envelope
    w = max(1, int(rate * 0.010))
    env = np.sqrt(np.convolve(y * y, np.ones(w, np.float32) / w, mode="same") + 1e-12)
    lvl = 20 * np.log10(env)
    gain_db = np.where(lvl > threshold_db, (threshold_db - lvl) * (1 - 1 / ratio), 0.0)
    sm = max(1, int(rate * 0.005))                                 # smoothed gain: no pumping clicks
    gain = 10 ** (np.convolve(gain_db, np.ones(sm) / sm, mode="same") / 20)
    y = y * gain.astype(np.float32)
    # loudness of the speech itself (the quiet gaps don't count)
    voiced = np.abs(y) > 10 ** (-45 / 20) * float(np.max(np.abs(y)) or 1)
    rms = float(np.sqrt(np.mean(y[voiced] ** 2))) if voiced.any() else 0.0
    if rms > 1e-6: y = y * (10 ** (target_rms_db / 20) / rms)
    over = np.abs(y) > peak                                         # soft limit (no hard clipping)
    if over.any():
        y = np.where(over, np.sign(y) * (peak + (1 - peak) * np.tanh((np.abs(y) - peak) / (1 - peak))), y)
    return np.clip(y, -1.0, 1.0).astype(np.float32)

def pcm_wav(audio, rate):
    import numpy as np
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((audio * 32767.0).astype(np.int16).tobytes())
    return buf.getvalue()

class Voices:
    def __init__(self, builtin: Path, data: Path, play_cmd=("pw-play", "-"), espeak="espeak-ng", prep_delay=0.05):
        self.builtin, self.data = Path(builtin), Path(data)
        self.user = self.data / "packs"; self.models = self.data / "models"; self.cache = self.data / "cache"
        self.play_cmd, self.espeak = list(play_cmd), espeak
        self.lock = asyncio.Lock(); self.waiting = 0
        self._piper = {}; self._piper_lock = threading.Lock()
        self.downloads = {}        # model -> {"state": "downloading"|"ready"|"waiting"|"error", "pct": n}
        self.greeted = False
        self.on_speak_start = self.on_speak_end = None   # set by the bridge: duck other audio through Hudiy
        self.prep_queue, self.prep_lock, self.prep_thread, self.prep_delay = [], threading.Lock(), None, prep_delay
        self.prepared = 0
        self.preloads = {}          # pack -> {"done": n, "total": n} while its common lines are being made

    # ---------------------------------------------------------- packs
    def packs(self):
        out = {}
        for root, kind in ((self.builtin, "built-in"), (self.user, "yours")):
            for d in sorted(root.iterdir()) if root.exists() else []:
                if d.is_dir() and ID_RE.match(d.name) and (d / "voice.json").exists():
                    try: p = json.loads((d / "voice.json").read_text())
                    except (OSError, ValueError): continue
                    p.update(id=d.name, dir=str(d), kind=kind); out.setdefault(d.name, p)   # built-in wins on a name clash
        # "extends": another pack's lines and wording with its own voice (the HD packs: same personality,
        # Piper's high-quality model)
        for pid, p in list(out.items()):
            base = out.get(p.get("extends") or "")
            if base and not base.get("extends"):
                merged = {k: v for k, v in base.items() if k not in ("id", "dir", "kind", "hd", "download")}
                merged.update(p); out[pid] = merged
        return out

    def pack(self, pid):
        ps = self.packs()
        return ps.get(pid) or ps.get("standard") or {"id": "standard", "engine": "espeak", "lines": {}, "language": "*"}

    def model_ready(self, model):
        return bool(model) and (self.models / f"{model}.onnx").exists() and (self.models / f"{model}.onnx.json").exists()

    def status(self, current):
        out = []
        for pid, p in self.packs().items():
            m = p.get("model") if p.get("engine") == "piper" else None
            st = "ready" if not m or self.model_ready(m) else self.downloads.get(m, {}).get("state", "waiting")
            out.append({"id": pid, "name": p.get("name", pid), "description": p.get("description", ""), "kind": p["kind"],
                        "language": p.get("language", "en"), "engine": p.get("engine", "espeak"), "state": st,
                        "pct": self.downloads.get(m, {}).get("pct") if m else None, "current": pid == current,
                        "hd": bool(p.get("hd")), "size_mb": p.get("size_mb"), "preload": self.preloads.get(pid)})
        out.sort(key=lambda x: (x["id"] != "standard", x["kind"] != "built-in", x["name"].lower()))
        return out

    # ---------------------------------------------------------- what to say
    def rng(self, key, text, vars):
        """Directions always come out in the same words (so they can be spoken ahead of time and cached);
        greetings and the like vary."""
        if key in RANDOM_KEYS: return random
        return random.Random(hashlib.sha1(f"{key}|{text}|{json.dumps(vars, sort_keys=True)}".encode()).hexdigest())

    def compose(self, p, key, text, vars, lang, rng=random):
        """-> ("clip", path) | ("text", words). The pack's own lines only in its language."""
        if p.get("language", "en") not in ("*", lang):
            return ("text", text)
        clips = (p.get("clips") or {}).get(key)
        if clips:
            clips = [clips] if isinstance(clips, str) else clips
            files = [Path(p["dir"]) / c for c in clips if "/" not in c and "\\" not in c and (Path(p["dir"]) / c).exists()]
            if files: return ("clip", rng.choice(files))
        line = (p.get("lines") or {}).get(key)
        if line:
            line = rng.choice(line) if isinstance(line, list) else line
            try: said = line.format(**{"text": text, **vars})
            except (KeyError, IndexError, ValueError): said = None
            if said:
                then = vars.get("then")                       # e.g. the route's first turn after "Route set"
                if then and "{text}" not in line and "{then}" not in line: said = f"{said} {then}"
                return ("text", said)
        return ("text", text)

    # ---------------------------------------------------------- speaking
    def _piper_voice(self, model):
        with self._piper_lock:
            if model not in self._piper:
                from piper import PiperVoice
                self._piper = {model: PiperVoice.load(str(self.models / f"{model}.onnx"))}   # keep one in memory
            return self._piper[model]

    def synth_piper(self, p, text):
        """Natural delivery: a little more expressive rhythm than Piper's defaults, a short breath between
        sentences, one loudness for the whole line (Piper evens out each sentence on its own, which
        sounds jumpy), soft starts/ends instead of clicks, and a moment of silence up front so the
        amplifier is awake before the first word."""
        import numpy as np
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=p.get("length_scale"), noise_scale=p.get("noise_scale", 0.72),
                              noise_w_scale=p.get("noise_w", 0.9), normalize_audio=False, volume=1.0)
        chunks, rate = [], 22050
        for ch in self._piper_voice(p["model"]).synthesize(text, cfg):
            rate = ch.sample_rate; a = trim_silence(np.asarray(ch.audio_float_array, dtype=np.float32).reshape(-1), rate)
            if a.size: chunks.append(a)
        if not chunks: return b""
        out = shape_speech(chunks, rate, pause=float(p.get("pause", 0.22)))
        if p.get("car_eq", True): out = car_polish(out, rate, presence_db=float(p.get("presence_db", 3.0)))
        return pcm_wav(out * float(p.get("volume", 1.0)), rate)

    def synth_espeak(self, p, text, fallback_voice):
        e = p.get("espeak") or {}
        cmd = [self.espeak, "-v", e.get("voice") or fallback_voice, "-s", str(e.get("speed", 165)), "-p", str(e.get("pitch", 50)), "--stdout", text]
        return subprocess.run(cmd, capture_output=True, timeout=20).stdout

    def _cache_files(self, p, text, fallback_voice):
        """(kept, ordinary) cache paths for a line."""
        use_piper = p.get("engine") == "piper" and self.model_ready(p.get("model"))
        how = f"piper3:{p.get('car_eq', True)}:{p.get('presence_db')}:{p.get('volume')}:{p.get('model')}:{p.get('length_scale')}:{p.get('noise_scale')}:{p.get('noise_w')}:{p.get('pause')}" if use_piper else f"espeak:{json.dumps(p.get('espeak'))}:{fallback_voice}"
        name = hashlib.sha1(f"{how}|{text}".encode()).hexdigest() + ".wav"
        return self.cache / p["id"] / "keep" / name, self.cache / p["id"] / name

    def wav_for(self, p, text, fallback_voice, keep=False):
        """Spoken audio for text (cached on disk, so repeated lines start instantly; `keep`: never pruned)."""
        use_piper = p.get("engine") == "piper" and self.model_ready(p.get("model"))
        kept, f = self._cache_files(p, text, fallback_voice)
        for c in (kept, f):
            if c.exists(): return c.read_bytes()
        if keep and use_piper: f = kept
        try:
            data = self.synth_piper(p, text) if use_piper else self.synth_espeak(p, text, fallback_voice)
        except Exception as e:
            log.warning("voice %s: %s; using the plain voice", p["id"], e)
            data = self.synth_espeak({"espeak": {}}, text, fallback_voice)
        if data:
            try:
                f.parent.mkdir(parents=True, exist_ok=True); tmp = f.with_suffix(".tmp"); tmp.write_bytes(data); tmp.replace(f)
                self._prune()
            except OSError:
                pass
        return data

    def _prune(self):
        files = sorted((x for x in self.cache.rglob("*.wav") if x.parent.name != "keep"), key=lambda x: x.stat().st_atime)
        for x in files[:max(0, len(files) - CACHE_MAX)]: x.unlink(missing_ok=True)

    async def _focus(self, which):
        hook = self.on_speak_start if which == "start" else self.on_speak_end
        if hook:
            try: hook()
            except Exception as e: log.debug("audio focus: %s", e)

    def words(self, pack_id, key, text, vars, lang):
        """(pack, "clip"|"text", what) - exactly what will be said for this prompt."""
        p = self.pack(pack_id)
        if lang != "en" and p.get("language", "en") not in ("*", lang):
            p = self.pack("standard")
        rng = self.rng(key, text, vars)
        kind, what = self.compose(p, key, text, vars, lang, rng)
        if kind == "text" and lang == "en":                     # read like a person would say it
            what = humanize(what, p.get("phrases"), rng)
        return p, kind, what

    def prepare(self, pack_id, items, lang, fallback_voice, keep=False):
        """Speak ahead of time (into the cache, in the background, never while something is being said):
        the next turns' prompts, so each one starts the moment it's due - no pause while the voice is
        worked out. items = [{key, text, vars}]. Returns how many were queued."""
        jobs = []
        for it in items[:PREP_MAX]:
            if not isinstance(it, dict): continue
            p, kind, what = self.words(pack_id, str(it.get("key", ""))[:30], str(it.get("text", ""))[:300],
                                       {k: str(v)[:200] for k, v in (it.get("vars") or {}).items() if isinstance(k, str)}, lang)
            if kind == "text" and what: jobs.append((p, what, fallback_voice, keep))
        with self.prep_lock:
            have = {(j[0]["id"], j[1]) for j in self.prep_queue}
            new = [j for j in jobs if (j[0]["id"], j[1]) not in have]
            if keep: self.prep_queue.extend(new)                       # common lines: after everything else
            else:                                                      # the route's next turns first (the newest route wins)
                routes = [j for j in self.prep_queue if not j[3]]; keeps = [j for j in self.prep_queue if j[3]]
                self.prep_queue[:] = (new + routes)[:PREP_MAX] + keeps
            if not (self.prep_thread and self.prep_thread.is_alive()):
                self.prep_thread = threading.Thread(target=self._prep_run, daemon=True); self.prep_thread.start()
        return len(new)

    def preload(self, pack_id, lang, fallback_voice, units="imperial"):
        """Make a Piper pack's most common lines once (kept, never pruned): its greetings / rerouting /
        arrived lines and the usual directions without a street name, at the turn and as heads-ups.
        Only once its model is on the head unit (not with the stand-in voice). Returns how many were queued."""
        p = self.pack(pack_id)
        if p.get("engine") != "piper" or not self.model_ready(p.get("model")) or p.get("language", "en") not in ("*", lang): return 0
        items = []
        for key in ("greet", "rerouting", "arrived"):
            line = (p.get("lines") or {}).get(key)
            for l in (line if isinstance(line, list) else [line] if line else []):
                if "{" not in l: items.append({"key": "_line", "text": l})
        for turn in COMMON_TURNS:
            items.append({"key": "turn", "text": turn, "vars": {}})
            for d in HEADS_UP.get(units, HEADS_UP["imperial"]):
                items.append({"key": "in_dist", "text": f"In {d}, {turn}", "vars": {"dist": d, "text": turn}})
        todo = [it for it in items if not self.cached(pack_id, it, lang, fallback_voice)]
        if not todo: return 0
        self.preloads[p["id"]] = {"done": 0, "total": len(todo)}
        n = sum(self.prepare(pack_id, todo[i:i + PREP_MAX], lang, fallback_voice, keep=True) for i in range(0, len(todo), PREP_MAX))
        if n: self.preloads[p["id"]]["total"] = n
        else: self.preloads.pop(p["id"], None)
        return n

    def cached(self, pack_id, item, lang, fallback_voice):
        p, kind, what = self.words(pack_id, item.get("key", ""), item.get("text", ""), item.get("vars") or {}, lang)
        return kind != "text" or not what or any(f.exists() for f in self._cache_files(p, what, fallback_voice))

    def prepare_lines(self, pack_id, lang, fallback_voice):
        """The pack's fixed lines (greetings, rerouting, arrived) - every variant - ahead of time."""
        p = self.pack(pack_id)
        if p.get("language", "en") not in ("*", lang) or not (p.get("lines")): return 0
        items = []
        for key in ("greet", "rerouting", "arrived"):
            line = p["lines"].get(key)
            for l in (line if isinstance(line, list) else [line] if line else []):
                if "{" not in l: items.append({"key": "_line", "text": l})
        return self.prepare(pack_id, items, lang, fallback_voice)

    def _prep_run(self):
        try: os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)   # behind the map and the music
        except (OSError, AttributeError): pass
        while True:
            with self.prep_lock:
                if not self.prep_queue: return
                p, what, fb, keep = self.prep_queue.pop(0)
            while self.waiting > 0: time.sleep(0.2)                  # something is being said: wait
            try:
                self.wav_for(p, what, fb, keep=keep); self.prepared += 1
            except Exception as e:
                log.debug("prepare %r: %s", what, e)
            if keep and p["id"] in self.preloads:
                pl = self.preloads[p["id"]]; pl["done"] += 1
                if pl["done"] >= pl["total"]: self.preloads.pop(p["id"], None); log.info("voice %s: common lines ready", p["id"])
            time.sleep(self.prep_delay)

    async def say(self, pack_id, key, text, vars, lang, fallback_voice):
        """Queue one utterance (they never talk over each other; a long backlog drops the extras)."""
        p, kind, what = self.words(pack_id, key, text, vars or {}, lang)
        if not what: return False
        if self.waiting >= 3: return False                  # one speaking + two waiting at most
        self.waiting += 1
        try:
            async with self.lock:
                loop = asyncio.get_running_loop()
                data = what.read_bytes() if kind == "clip" else await loop.run_in_executor(None, self.wav_for, p, what, fallback_voice)
                if not data: return False
                await self._focus("start")                     # music / CarPlay audio dips while it speaks
                try:
                    proc = await asyncio.create_subprocess_exec(*self.play_cmd, stdin=asyncio.subprocess.PIPE,
                                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await proc.communicate(data)
                finally:
                    await self._focus("end")
                return True
        finally:
            self.waiting -= 1

    # ---------------------------------------------------------- downloading Piper models (on Wi-Fi)
    async def download_models(self, session, first=None):
        """Fetch missing Piper models of every pack (the chosen one first). Resumes partial downloads."""
        wanted = []
        for p in sorted(self.packs().values(), key=lambda p: p["id"] != first):
            if p.get("hd") and p["id"] != first: continue          # big high-quality models: only the one you pick
            m = p.get("model") if p.get("engine") == "piper" else None
            if m and MODEL_RE.match(m) and not self.model_ready(m) and m not in wanted: wanted.append(m)
        self.models.mkdir(parents=True, exist_ok=True)
        for m in wanted:
            self.downloads[m] = {"state": "downloading", "pct": 0}
            try:
                for ext in (".onnx.json", ".onnx"):
                    await self._fetch(session, model_url(m, ext), self.models / f"{m}{ext}", m, ext == ".onnx")
                json.loads((self.models / f"{m}.onnx.json").read_text())       # a broken config = not ready
                self.downloads[m] = {"state": "ready", "pct": 100}; log.info("voice model %s ready", m)
            except Exception as e:
                for ext in (".onnx", ".onnx.json"):
                    f = self.models / f"{m}{ext}"
                    if f.exists() and ext == ".onnx.json": f.unlink(missing_ok=True)
                self.downloads[m] = {"state": "waiting", "pct": self.downloads[m].get("pct"), "error": str(e)[:160]}
                log.info("voice model %s: %s (will retry)", m, e)
                return

    async def _fetch(self, session, url, dest, model, progress):
        part = dest.with_suffix(dest.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        hdr = {"Range": f"bytes={have}-"} if have else {}
        async with session.get(url, headers=hdr) as r:
            if r.status == 416 and have: part.replace(dest); return
            if r.status not in (200, 206): raise IOError(f"HTTP {r.status}")
            if r.status == 200: have = 0
            total = have + int(r.headers.get("Content-Length", 0) or 0)
            with open(part, "ab" if have else "wb") as f:
                got = have
                async for chunk in r.content.iter_chunked(1 << 16):
                    f.write(chunk); got += len(chunk)
                    if progress and total: self.downloads[model]["pct"] = round(got * 100 / total)
            if total and got < total: raise IOError("download cut short")
        if progress and dest.suffix == ".onnx" and part.stat().st_size < 1_000_000:
            part.unlink(missing_ok=True); raise IOError("model file too small")
        part.replace(dest)

    def import_from(self, folder: Path):
        """Copy voice packs from a USB stick's HEADUNIT_VOICES folder. Returns the names copied."""
        done = []
        for d in sorted(folder.iterdir()) if folder.is_dir() else []:
            if not (d.is_dir() and ID_RE.match(d.name.lower()) and (d / "voice.json").exists()): continue
            try: json.loads((d / "voice.json").read_text())
            except (OSError, ValueError): continue
            dst = self.user / d.name.lower(); tmp = self.user / f".{d.name.lower()}.incoming"
            shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
            for f in d.iterdir():
                if f.is_file() and not f.is_symlink() and f.suffix.lower() in (".json", ".wav") and f.stat().st_size < 20_000_000:
                    shutil.copyfile(f, tmp / f.name)
            shutil.rmtree(dst, ignore_errors=True); tmp.replace(dst); done.append(d.name.lower())
            shutil.rmtree(self.cache / d.name.lower(), ignore_errors=True)
        return done
