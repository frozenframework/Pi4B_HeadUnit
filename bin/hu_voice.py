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
from pathlib import Path

log = logging.getLogger("hu-voice")
MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{fam}/{code}/{name}/{q}/{model}{ext}?download=true"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MODEL_RE = re.compile(r"^([a-z]{2,3})_([A-Z]{2})-([A-Za-z0-9_]+)-(x_low|low|medium|high)$")
CACHE_MAX = 400

def model_url(model, ext):
    m = MODEL_RE.match(model)
    if not m: raise ValueError(f"not a Piper voice name: {model}")
    fam, cc, name, q = m.groups()
    return MODEL_URL.format(fam=fam, code=f"{fam}_{cc}", name=name, q=q, model=model, ext=ext)

class Voices:
    def __init__(self, builtin: Path, data: Path, play_cmd=("pw-play", "-"), espeak="espeak-ng"):
        self.builtin, self.data = Path(builtin), Path(data)
        self.user = self.data / "packs"; self.models = self.data / "models"; self.cache = self.data / "cache"
        self.play_cmd, self.espeak = list(play_cmd), espeak
        self.lock = asyncio.Lock(); self.waiting = 0
        self._piper = {}; self._piper_lock = threading.Lock()
        self.downloads = {}        # model -> {"state": "downloading"|"ready"|"waiting"|"error", "pct": n}
        self.greeted = False

    # ---------------------------------------------------------- packs
    def packs(self):
        out = {}
        for root, kind in ((self.builtin, "built-in"), (self.user, "yours")):
            for d in sorted(root.iterdir()) if root.exists() else []:
                if d.is_dir() and ID_RE.match(d.name) and (d / "voice.json").exists():
                    try: p = json.loads((d / "voice.json").read_text())
                    except (OSError, ValueError): continue
                    p.update(id=d.name, dir=str(d), kind=kind); out.setdefault(d.name, p)   # built-in wins on a name clash
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
                        "pct": self.downloads.get(m, {}).get("pct") if m else None, "current": pid == current})
        out.sort(key=lambda x: (x["id"] != "standard", x["kind"] != "built-in", x["name"].lower()))
        return out

    # ---------------------------------------------------------- what to say
    def compose(self, p, key, text, vars, lang):
        """-> ("clip", path) | ("text", words). The pack's own lines only in its language."""
        if p.get("language", "en") not in ("*", lang):
            return ("text", text)
        clips = (p.get("clips") or {}).get(key)
        if clips:
            clips = [clips] if isinstance(clips, str) else clips
            files = [Path(p["dir"]) / c for c in clips if "/" not in c and "\\" not in c and (Path(p["dir"]) / c).exists()]
            if files: return ("clip", random.choice(files))
        line = (p.get("lines") or {}).get(key)
        if line:
            line = random.choice(line) if isinstance(line, list) else line
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
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=p.get("length_scale"), noise_scale=p.get("noise_scale"), volume=float(p.get("volume", 1.0)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._piper_voice(p["model"]).synthesize_wav(text, w, syn_config=cfg)
        return buf.getvalue()

    def synth_espeak(self, p, text, fallback_voice):
        e = p.get("espeak") or {}
        cmd = [self.espeak, "-v", e.get("voice") or fallback_voice, "-s", str(e.get("speed", 165)), "-p", str(e.get("pitch", 50)), "--stdout", text]
        return subprocess.run(cmd, capture_output=True, timeout=20).stdout

    def wav_for(self, p, text, fallback_voice):
        """Spoken audio for text (cached on disk, so repeated lines start instantly)."""
        use_piper = p.get("engine") == "piper" and self.model_ready(p.get("model"))
        how = f"piper:{p.get('model')}:{p.get('length_scale')}" if use_piper else f"espeak:{json.dumps(p.get('espeak'))}:{fallback_voice}"
        f = self.cache / p["id"] / (hashlib.sha1(f"{how}|{text}".encode()).hexdigest() + ".wav")
        if f.exists():
            return f.read_bytes()
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
        files = sorted(self.cache.rglob("*.wav"), key=lambda x: x.stat().st_atime)
        for x in files[:max(0, len(files) - CACHE_MAX)]: x.unlink(missing_ok=True)

    async def say(self, pack_id, key, text, vars, lang, fallback_voice):
        """Queue one utterance (they never talk over each other; a long backlog drops the extras)."""
        p = self.pack(pack_id)
        if lang != "en" and p.get("language", "en") not in ("*", lang):
            p = self.pack("standard")
        kind, what = self.compose(p, key, text, vars or {}, lang)
        if not what: return False
        if self.waiting >= 3: return False                  # one speaking + two waiting at most
        self.waiting += 1
        try:
            async with self.lock:
                loop = asyncio.get_running_loop()
                data = what.read_bytes() if kind == "clip" else await loop.run_in_executor(None, self.wav_for, p, what, fallback_voice)
                if not data: return False
                proc = await asyncio.create_subprocess_exec(*self.play_cmd, stdin=asyncio.subprocess.PIPE,
                                                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await proc.communicate(data)
                return True
        finally:
            self.waiting -= 1

    # ---------------------------------------------------------- downloading Piper models (on Wi-Fi)
    async def download_models(self, session, first=None):
        """Fetch missing Piper models of every pack (the chosen one first). Resumes partial downloads."""
        wanted = []
        for p in sorted(self.packs().values(), key=lambda p: p["id"] != first):
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
