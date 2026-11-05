#!/usr/bin/env python3
"""
headunit bridge: one small local web service that the Hudiy web apps talk to.

  /apps/...                 static web apps (nav, music, radio, theme)
  /maps/basemap.pmtiles     offline map tiles (HTTP range requests)
  /api/gps/stream           live GPS (Server-Sent Events, from gpsd)
  /api/route                offline routing (Valhalla)
  /api/search               offline place search (SQLite FTS5)
  /api/music/...            iPod / USB library through MPD
  /api/radio/...            AM/FM through RTL-SDR
  /api/settings, /api/favorites, /api/log, /api/status

It listens on 127.0.0.1 only: nothing on the Wi-Fi network can reach it.
"""
import asyncio, json, os, re, shutil, signal, sqlite3, subprocess, sys, time, logging, math, threading
from pathlib import Path
from aiohttp import web

log = logging.getLogger("hu-bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------- configuration
def load_conf(path):
    conf = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return conf

CONF = load_conf(os.environ.get("HU_CONF", "/etc/headunit/headunit.conf"))
def C(key, default=""):
    return os.environ.get(key, CONF.get(key, default))

PORT = int(C("BRIDGE_PORT", "8765"))
APP_DIR = Path(C("APP_DIR", "/opt/headunit/apps"))
VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip() if (Path(__file__).resolve().parent.parent / "VERSION").exists() else "dev"
DATA_DIR = Path(C("DATA_DIR", "/var/lib/headunit"))
USER_DIR = Path(C("USER_DIR", str(Path.home() / ".config" / "headunit")))
LOG_DIR = Path(C("USAGE_LOG_DIR", str(Path.home() / "headunit-data" / "usage")))
MEDIA_ROOT = Path(C("MEDIA_ROOT", "/media/hu"))
STATE_FILE = Path(C("POWER_STATE_FILE", "/run/headunit/state.json"))
ANT_GPIO = C("ANT_GPIO", "")
MAPS_DIR = DATA_DIR / "maps"
ROUTING_TAR = DATA_DIR / "routing" / "valhalla_tiles.tar"
SEARCH_DB = DATA_DIR / "search" / "search.sqlite"
for d in (USER_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Languages offered in Settings > Region & language (the 10 most spoken in US homes, Census ACS 2024).
# valhalla = turn-by-turn text from the router (None: the nav app writes it from its own phrase list),
# voice = espeak-ng voice for spoken prompts (the first one installed is used).
LANGS = {
    "en": {"valhalla": "en-US", "voice": ["en-us"]},
    "es": {"valhalla": "es-ES", "voice": ["es-419", "es"]},
    "zh": {"valhalla": None, "voice": ["cmn", "zh"]},
    "tl": {"valhalla": None, "voice": ["tl", "fil", "id"]},
    "vi": {"valhalla": "vi-VN", "voice": ["vi"]},
    "ar": {"valhalla": "ar-SA", "voice": ["ar"]},
    "fr": {"valhalla": "fr-FR", "voice": ["fr-fr", "fr"]},
    "ko": {"valhalla": "ko-KR", "voice": ["ko"]},
    "pt": {"valhalla": "pt-BR", "voice": ["pt-br", "pt"]},
    "hi": {"valhalla": "hi-IN", "voice": ["hi"]},
}
def system_language():
    """Language code from the system locale (/etc/default/locale), e.g. es_US.UTF-8 -> es."""
    try:
        for line in Path("/etc/default/locale").read_text().splitlines():
            if line.startswith("LANG="):
                code = line.split("=", 1)[1].strip('"')[:2]
                return code if code in LANGS else "en"
    except OSError:
        pass
    return "en"

DEFAULT_SETTINGS = {"theme": C("DEFAULT_THEME", "neon-overdrive"), "units": "imperial", "voice": True,
                    "language": system_language(), "lastPos": None,
                    "syncHudiyColors": True, "nightAuto": True, "startOnMap": True, "phoneScreen": "map",
                    "dayNight": False, "dayTheme": "sunset-junction", "nightTheme": C("DEFAULT_THEME", "neon-overdrive"),
                    "display": {"level": 100, "auto": True, "day": 100, "night": 45, "method": "auto"}, "radio": {"band": "FM", "FM": 101100, "AM": 740,
                    "presets": {"FM": [], "AM": []}}}

def read_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default

def write_json(p, data):
    p = Path(p); tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1)); tmp.replace(p)

def settings():
    s = dict(DEFAULT_SETTINGS); s.update(read_json(USER_DIR / "settings.json", {}))
    return s

# ---------------------------------------------------------------- usage log (for tuning after test drives)
_log_lock = threading.Lock()
def usage(event, **fields):
    rec = {"t": round(time.time(), 3), "event": event, **fields}
    path = LOG_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")
    with _log_lock, open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")

# ---------------------------------------------------------------- GPS (gpsd)
class GPS:
    def __init__(self):
        self.fix = {"mode": 0}
        self.subscribers = set()
    async def run(self):
        while True:
            try:
                r, w = await asyncio.open_connection("127.0.0.1", 2947)
                w.write(b'?WATCH={"enable":true,"json":true}\n'); await w.drain()
                async for line in r:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("class") == "TPV":
                        self.fix = {k: msg.get(k) for k in ("mode", "lat", "lon", "speed", "track", "alt", "time", "eph")}
                        data = json.dumps(self.fix)
                        for q in list(self.subscribers):
                            if q.qsize() < 5:
                                q.put_nowait(data)
            except Exception as e:
                self.fix = {"mode": 0, "error": str(e)}
            await asyncio.sleep(3)

gps = GPS()

async def gps_stream(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q = asyncio.Queue(); gps.subscribers.add(q)
    try:
        await resp.write(f"data: {json.dumps(gps.fix)}\n\n".encode())
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=15)
                await resp.write(f"data: {data}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        gps.subscribers.discard(q)
    return resp

# ---------------------------------------------------------------- routing (Valhalla)
_actor = None
_actor_mtime = 0
_actor_lock = threading.Lock()

def get_actor():
    global _actor, _actor_mtime
    if not ROUTING_TAR.exists():
        return None
    mt = ROUTING_TAR.stat().st_mtime
    with _actor_lock:
        if _actor is None or mt != _actor_mtime:
            from valhalla import Actor, get_config
            cfg = get_config(tile_extract=str(ROUTING_TAR), tile_dir="", verbose=False)
            _actor = Actor(cfg); _actor_mtime = mt
            log.info("valhalla loaded %s", ROUTING_TAR)
    return _actor

def decode_polyline6(s):
    coords, idx, lat, lon = [], 0, 0, 0
    while idx < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63; idx += 1
                result |= (b & 0x1F) << shift; shift += 5
                if b < 0x20: break
            d = ~(result >> 1) if result & 1 else (result >> 1)
            if which == 0: lat += d
            else: lon += d
        coords.append([lon / 1e6, lat / 1e6])
    return coords

def do_route(body):
    actor = get_actor()
    if actor is None:
        return {"error": "no_routing_data", "message": "Routing data isn't installed yet. See README: Maps."}
    units = "miles" if settings().get("units") == "imperial" else "kilometers"
    req = {"locations": [{"lon": body["from"][0], "lat": body["from"][1], **({"heading": body["heading"], "heading_tolerance": 45} if body.get("heading") is not None else {})},
                         {"lon": body["to"][0], "lat": body["to"][1]}],
           "costing": body.get("costing", "auto"), "units": units,
           "language": LANGS.get(settings().get("language"), {}).get("valhalla") or "en-US",
           "directions_options": {"units": units}}
    raw = actor.route(json.dumps(req))
    res = json.loads(raw) if isinstance(raw, str) else raw
    leg = res["trip"]["legs"][0]
    shape = decode_polyline6(leg["shape"])
    man = [{"instruction": m.get("instruction"), "verbal": m.get("verbal_pre_transition_instruction") or m.get("instruction"),
            "type": m.get("type"), "length": m.get("length"), "time": m.get("time"), "begin": m.get("begin_shape_index"),
            "end": m.get("end_shape_index"), "streets": m.get("street_names") or [], "exit": (m.get("sign") or {}).get("exit_number_elements")}
           for m in leg["maneuvers"]]
    s = res["trip"]["summary"]
    return {"shape": shape, "maneuvers": man, "summary": {"length": s["length"], "time": s["time"], "units": units}}

async def route(request):
    body = await request.json()
    t0 = time.time()
    try:
        out = await asyncio.get_running_loop().run_in_executor(None, do_route, body)
    except Exception as e:
        out = {"error": "route_failed", "message": str(e)[:300]}
    usage("route", ms=int((time.time() - t0) * 1000), ok="error" not in out)
    return web.json_response(out)

# ---------------------------------------------------------------- offline search
def do_search(q, near):
    if not SEARCH_DB.exists() or not q.strip():
        return []
    terms = [re.sub(r'[^0-9A-Za-zÀ-ɏ]', '', t) for t in q.split()]
    terms = [t for t in terms if t]
    if not terms:
        return []
    fts = " ".join(f'"{t}"*' for t in terms)
    con = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT name, kind, detail, lon, lat FROM places WHERE places MATCH ? LIMIT 200", (fts,)).fetchall()
    finally:
        con.close()
    def dist(r):
        if not near: return 0
        dx = (r[3] - near[0]) * math.cos(math.radians(near[1])); dy = r[4] - near[1]
        return dx * dx + dy * dy
    rows.sort(key=dist)
    return [{"name": r[0], "kind": r[1], "detail": r[2], "lon": r[3], "lat": r[4]} for r in rows[:25]]

async def search(request):
    q = request.query.get("q", "")
    near = None
    if "near" in request.query:
        try: near = [float(x) for x in request.query["near"].split(",")]
        except ValueError: pass
    res = await asyncio.get_running_loop().run_in_executor(None, do_search, q, near)
    usage("search", n=len(res), qlen=len(q))
    return web.json_response(res)

# ---------------------------------------------------------------- music (MPD; iPod and USB drives)
_mpd_lock = threading.Lock()
def mpd_call(fn):
    try:
        from mpd import MPDClient
    except ImportError:
        return {"error": "mpd_python_missing"}
    with _mpd_lock:
        c = MPDClient(); c.timeout = 10
        try:
            c.connect(C("MPD_HOST", "127.0.0.1"), int(C("MPD_PORT", "6600")))
            return fn(c)
        except Exception as e:
            return {"error": "mpd", "message": str(e)}
        finally:
            try: c.disconnect()
            except Exception: pass

def _tracks(items):
    out = []
    for t in items:
        if "file" not in t: continue
        out.append({"file": t["file"], "title": t.get("title") or Path(t["file"]).stem, "artist": t.get("artist", ""),
                    "album": t.get("album", ""), "track": t.get("track", ""), "time": float(t.get("duration", t.get("time", 0)) or 0)})
    return out

async def music(request):
    action = request.match_info["action"]
    q = request.query
    loop = asyncio.get_running_loop()
    if action == "artists":
        def f(c):
            names = {r.get("albumartist") for r in c.list("albumartist")} | {r.get("artist") for r in c.list("artist")}
            return sorted(names - {None, ""}, key=str.lower)
        res = await loop.run_in_executor(None, mpd_call, f)
    elif action == "albums":
        artist = q.get("artist")
        def f(c):
            if artist:
                rows = c.list("album", "albumartist", artist) + c.list("album", "artist", artist)
            else:
                rows = c.list("album")
            return sorted({r["album"] for r in rows if r.get("album")}, key=str.lower)
        res = await loop.run_in_executor(None, mpd_call, f)
    elif action == "tracks":
        def f(c):
            def num(v):
                try: return int(str(v).split("/")[0])
                except ValueError: return 0
            if q.get("album"):
                items = c.find("album", q["album"])
                if q.get("artist"):
                    items = [t for t in items if q["artist"] in (t.get("albumartist"), t.get("artist"))]
                items.sort(key=lambda t: (num(t.get("disc", 1)), num(t.get("track", 0))))
            elif q.get("artist"):
                items = c.find("artist", q["artist"]) or c.find("albumartist", q["artist"])
            else:
                items = [t for t in c.listallinfo() if "file" in t][:3000]
                items.sort(key=lambda t: (t.get("title") or t["file"]).lower())
            return _tracks(items)
        res = await loop.run_in_executor(None, mpd_call, f)
    elif action == "status":
        def f(c):
            st = c.status(); cur = c.currentsong()
            return {"state": st.get("state"), "elapsed": float(st.get("elapsed", 0) or 0), "duration": float(st.get("duration", 0) or 0),
                    "random": st.get("random") == "1", "volume": st.get("volume"), "updating": "updating_db" in st,
                    "song": _tracks([cur])[0] if cur else None, "sources": sources()}
        res = await loop.run_in_executor(None, mpd_call, f)
    else:
        return web.json_response({"error": "unknown"}, status=404)
    return web.json_response(res)

async def music_control(request):
    body = await request.json()
    a = body.get("action")
    def f(c):
        if a == "play_files":
            c.clear()
            for fn in body.get("files", []): c.add(fn)
            c.play(int(body.get("start", 0)))
        elif a == "toggle":
            st = c.status().get("state")
            c.pause(1) if st == "play" else c.play()
        elif a == "pause": c.pause(1)
        elif a == "play": c.play()
        elif a == "next": c.next()
        elif a == "prev": c.previous()
        elif a == "shuffle": c.random(0 if c.status().get("random") == "1" else 1)
        elif a == "seek": c.seekcur(float(body.get("pos", 0)))
        elif a == "rescan": c.update()
        return {"ok": True}
    if a in ("play_files", "play", "toggle"):
        await radio.stop()   # one audio source at a time
    res = await asyncio.get_running_loop().run_in_executor(None, mpd_call, f)
    usage("music", action=a)
    return web.json_response(res)

def sources():
    out = []
    if MEDIA_ROOT.exists():
        for d in sorted(MEDIA_ROOT.iterdir()):
            if d.is_dir() and os.path.ismount(d):
                out.append({"name": d.name, "ipod": (d / "iPod_Control").exists() or (d / "IPOD_CONTROL").exists()})
    return out

async def media_watch(app):
    """Rescan MPD when an iPod or USB drive is plugged in or removed; import map packs from USB."""
    last = None
    while True:
        cur = json.dumps(sources())
        if cur != last:
            if last is not None:
                usage("media_change", sources=json.loads(cur))
                await asyncio.get_running_loop().run_in_executor(None, mpd_call, lambda c: c.update())
                await asyncio.get_running_loop().run_in_executor(None, import_map_pack)
            last = cur
        await asyncio.sleep(4)

def import_map_pack():
    """A USB drive with a HEADUNIT_MAPS folder (made by maps/build-routing.py) is copied in automatically."""
    for d in MEDIA_ROOT.iterdir() if MEDIA_ROOT.exists() else []:
        pack = d / "HEADUNIT_MAPS"
        man = pack / "manifest.json"
        if not man.exists():
            continue
        m = read_json(man, {})
        stamp = DATA_DIR / "maps" / "imported.json"
        if read_json(stamp, {}).get("built") == m.get("built"):
            continue
        log.info("importing map pack %s", pack)
        r = subprocess.run(["sudo", "-n", "/opt/headunit/bin/hu-import-maps.sh", str(pack)], capture_output=True, text=True)
        usage("map_import", ok=r.returncode == 0, region=m.get("region"), err=r.stderr[-300:])

# ---------------------------------------------------------------- radio (RTL-SDR: AM via rtl_fm, FM via rtl_fm wbfm)
class Radio:
    def __init__(self):
        self.proc = None; self.band = None; self.freq = None
        self.ant = None
        if ANT_GPIO:
            try:
                from gpiozero import OutputDevice
                self.ant = OutputDevice(int(ANT_GPIO), active_high=True, initial_value=False)
            except Exception as e:
                log.warning("antenna GPIO unavailable: %s", e)
    async def tune(self, band, freq):
        await self.stop()
        if not shutil.which("rtl_fm"):
            return {"error": "rtl_fm_missing", "message": "RTL-SDR tools aren't installed (tier 3 / RADIO=1)."}
        if band == "AM":
            # freq in kHz. The RTL-SDR Blog V4 switches its HF upconverter on automatically below 28.8 MHz.
            rtl = f"rtl_fm -M am -f {int(freq)*1000} -s 12k -r 48k -A fast -l 0 -E dc -"
        else:
            rtl = f"rtl_fm -M fm -f {int(freq)*1000} -s 200k -r 48k -A fast -l 0 -E deemp -"
        play = "pw-cat --playback --rate 48000 --channels 1 --format s16 -"
        self.proc = await asyncio.create_subprocess_shell(f"{rtl} 2>/dev/null | {play}", start_new_session=True)
        self.band, self.freq = band, freq
        if self.ant: self.ant.on()
        s = settings(); s.setdefault("radio", {})[band] = freq; s["radio"]["band"] = band
        write_json(USER_DIR / "settings.json", s)
        await asyncio.get_running_loop().run_in_executor(None, mpd_call, lambda c: c.pause(1))
        usage("radio_tune", band=band, freq=freq)
        return {"ok": True, "band": band, "freq": freq}
    async def stop(self):
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:
                pass
        self.proc = None
        if self.ant: self.ant.off()
        return {"ok": True}
    def status(self):
        on = bool(self.proc and self.proc.returncode is None)
        return {"playing": on, "band": self.band, "freq": self.freq, "available": bool(shutil.which("rtl_fm"))}

radio = Radio()

async def radio_api(request):
    a = request.match_info["action"]
    if a == "status":
        return web.json_response({**radio.status(), "settings": settings().get("radio")})
    body = await request.json() if request.can_read_body else {}
    if a == "tune":
        return web.json_response(await radio.tune(body.get("band", "FM"), body["freq"]))
    if a == "stop":
        return web.json_response(await radio.stop())
    if a == "presets":
        s = settings(); s["radio"]["presets"] = body.get("presets", s["radio"].get("presets", {}))
        write_json(USER_DIR / "settings.json", s); return web.json_response({"ok": True})
    return web.json_response({"error": "unknown"}, status=404)

# ---------------------------------------------------------------- voice prompts
_voices = None
def voice_for(lang):
    """First espeak-ng voice for the language that is actually installed (falls back to US English)."""
    global _voices
    if _voices is None:
        try:
            out = subprocess.run(["espeak-ng", "--voices"], capture_output=True, text=True, timeout=10).stdout
            _voices = {l.split()[1] for l in out.splitlines()[1:] if len(l.split()) > 1}
        except Exception:
            _voices = set()
    for v in LANGS.get(lang, LANGS["en"])["voice"]:
        if v in _voices: return v
    return "en-us"

async def say(request):
    body = await request.json()
    text = str(body.get("text", ""))[:300]
    if not settings().get("voice", True) or not text or not shutil.which("espeak-ng"):
        return web.json_response({"ok": False})
    cmd = ["sh", "-c", 'espeak-ng -v "$2" -s 165 --stdout "$1" | pw-play -', "say", text, voice_for(settings().get("language", "en"))]
    await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return web.json_response({"ok": True})

# ---------------------------------------------------------------- settings / favorites / theme sync
async def settings_api(request):
    if request.method == "GET":
        return web.json_response(settings())
    body = await request.json()
    s = settings(); old_theme = s.get("theme"); old_start = s.get("startOnMap")
    if "theme" in body and not ({"dayTheme", "nightTheme"} & set(body)) and {**s, **body}.get("dayNight"):
        # a skin picked while day/night skins are on becomes the skin for the part of the day it is now
        body = {**body, ("nightTheme" if sun_info()["night"] else "dayTheme"): body["theme"]}
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS: continue
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**DEFAULT_SETTINGS[k], **s[k], **v} if isinstance(DEFAULT_SETTINGS[k], dict) else {**s[k], **v}
        else:
            s[k] = v
    if "display" in body:
        usage("display", **{k: s["display"].get(k) for k in ("level", "auto", "day", "night")})
    if s.get("dayNight") and {"dayNight", "dayTheme", "nightTheme"} & set(body):
        s["theme"] = day_night_theme(s)                       # switch right away to the skin for now
    write_json(USER_DIR / "settings.json", s)
    if s.get("startOnMap") != old_start:   # Hudiy's first screen: offline map or its own dashboard
        subprocess.Popen([sys.executable, "/opt/headunit/bin/hu-hudiy-start.py", "--config"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if s.get("theme") != old_theme:
        theme_changed(s, old_theme, "settings")
    return web.json_response(s)

def theme_changed(s, old_theme, why):
    usage("theme_change", theme=s["theme"], prev=old_theme, why=why)
    if s.get("syncHudiyColors"):   # skin Hudiy too: colors right away, backgrounds at its next start
        subprocess.Popen([sys.executable, "/opt/headunit/bin/hu-hudiy-theme.py", s["theme"], "--live"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------------------------------------------------------------- day / night skins (sunrise and sunset)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hu_sun
_last_pos = {"pos": None}

def sun_position():
    """Where to work out sunrise/sunset: GPS now, else the last GPS position, else the map region's middle."""
    f = gps.fix
    if f.get("mode", 0) >= 2 and f.get("lat") is not None and f.get("lon") is not None:
        _last_pos["pos"] = (f["lat"], f["lon"]); return f["lat"], f["lon"], "gps"
    if _last_pos["pos"]: return (*_last_pos["pos"], "gps")
    lp = settings().get("lastPos")
    if lp: return lp[1], lp[0], "last"
    try:
        region = C("MAP_REGION", "texas-plus")
        w, so, e, n = json.loads((APP_DIR.parent / "maps" / "regions.json").read_text())[region]["bbox"]
        return (so + n) / 2, (w + e) / 2, "region"
    except Exception:
        return 41.88, -87.63, "default"

def sun_info(t=None):
    t = time.time() if t is None else t
    lat, lon, src = sun_position()
    rise, sett = hu_sun.rise_set(lat, lon, hu_sun.local_midnight(t))
    night = hu_sun.is_night(lat, lon, t)
    nxt = None
    for x in sorted(v for v in (rise, sett) if v):
        if x > t: nxt = x; break
    if nxt is None:                                          # after today's sunset: tomorrow's sunrise
        nxt = hu_sun.rise_set(lat, lon, hu_sun.local_midnight(t) + 86400 + 3600)[0]
    return {"night": night, "sunrise": rise, "sunset": sett, "next": nxt, "source": src}

def day_night_theme(s):
    return s.get("nightTheme") if sun_info()["night"] else s.get("dayTheme")

async def day_night_watch():
    """With day/night skins on, switch the skin at sunrise and sunset (checked every 30 s)."""
    while True:
        try:
            s = settings()
            if s.get("dayNight"):
                want = await asyncio.get_running_loop().run_in_executor(None, day_night_theme, s)
                if want and want != s.get("theme"):
                    old = s.get("theme"); s["theme"] = want
                    write_json(USER_DIR / "settings.json", s)
                    log.info("day/night skins: %s -> %s", old, want)
                    theme_changed(s, old, "sun")
        except Exception as e:
            log.warning("day/night skins: %s", e)
        await asyncio.sleep(30)

async def sun_api(request):
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, sun_info))

async def favorites_api(request):
    p = USER_DIR / "favorites.json"
    if request.method == "GET":
        return web.json_response(read_json(p, []))
    favs = await request.json()
    write_json(p, favs[:50]); usage("favorites_saved", n=len(favs))
    return web.json_response({"ok": True})

async def log_api(request):
    body = await request.json()
    events = body if isinstance(body, list) else [body]
    for e in events[:50]:
        if isinstance(e, dict) and "event" in e:
            usage(str(e.pop("event"))[:40], **{k: e[k] for k in list(e)[:12]})
    return web.json_response({"ok": True})

async def status(request):
    man = read_json(MAPS_DIR / "manifest.json", {})
    st = read_json(STATE_FILE, {})
    return web.json_response({
        "maps": {"basemap": (MAPS_DIR / "basemap.pmtiles").exists(), "routing": ROUTING_TAR.exists(), "search": SEARCH_DB.exists(), **man},
        "gps": gps.fix, "power": st, "radio": radio.status(), "sources": sources(), "theme": settings().get("theme"),
        "vehicle": C("VEHICLE_NAME", ""), "night": st.get("illum", False), "version": VERSION,
        "dayNight": settings().get("dayNight", False)})


# ---------------------------------------------------------------- maps update + log summary
async def maps_update(request):
    r = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "headunit-update-maps.service"], capture_output=True, text=True)
    usage("maps_update_request", ok=r.returncode == 0)
    if r.returncode != 0:
        return web.json_response({"ok": False, "message": "Couldn't start the updater: " + (r.stderr.strip()[-160:] or "unknown error")})
    return web.json_response({"ok": True, "message": "Map update started. It keeps going in the background."})

async def maps_update_status(request):
    """Progress for Settings > Maps (written by hu-update-maps.sh / hu-maps-progress.py), plus whether the
    updater is still running, so a download cut off by a reboot shows as stopped, not stuck at 87%."""
    st = read_json(MAPS_DIR / "update-status.json", {"message": ""})
    r = await asyncio.create_subprocess_exec("systemctl", "is-active", "headunit-update-maps.service", "headunit-maps-firstrun.service",
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await r.communicate()
    st["running"] = any(x in ("active", "activating", "reloading") for x in out.decode().split())
    return web.json_response(st)

# ---------------------------------------------------------------- region & language, developer tools
SYSTEM_HELPER = "/opt/headunit/bin/hu-system.sh"

def run_helper(*args, timeout=180):
    """Run the root helper through sudo (allowed in /etc/sudoers.d/headunit). Returns its JSON."""
    try:
        r = subprocess.run(["sudo", "-n", SYSTEM_HELPER, *args], capture_output=True, text=True, timeout=timeout)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"ok": False, "error": (r.stderr.strip() or "no answer")[-160:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}

def countries():
    out = []
    try:
        for line in Path("/usr/share/zoneinfo/iso3166.tab").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                code, name = line.split("\t", 1); out.append({"code": code, "name": name})
    except OSError:
        out = [{"code": "US", "name": "United States"}]
    return sorted(out, key=lambda c: (c["code"] != "US", c["name"]))

US_ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
            "America/Anchorage", "Pacific/Honolulu", "America/Puerto_Rico"]
def timezones():
    zones = set()
    try:
        for line in Path("/usr/share/zoneinfo/tzdata.zi").read_text().splitlines():
            if line.startswith("Z "): zones.add(line.split()[1])
            elif line.startswith("L "): zones.add(line.split()[2])
    except OSError:
        pass
    zones = sorted(z for z in zones if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/", "posix", "right")))
    return US_ZONES + [z for z in zones if z not in US_ZONES]

def addresses():
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
        return [a for a in out if ":" not in a] or out
    except Exception:
        return []

async def system_api(request):
    loop = asyncio.get_running_loop()
    if request.method == "GET":
        st = await loop.run_in_executor(None, run_helper, "status")
        return web.json_response({**st, "language": settings().get("language", "en"), "countries": countries(),
                                  "timezones": timezones(), "hostname": os.uname().nodename, "addresses": addresses(),
                                  "version": VERSION})
    body = await request.json()
    out = {"ok": True}
    for key in ("country", "timezone", "language", "ssh"):
        if key not in body: continue
        val = str(body[key])
        if key == "language":
            if val not in LANGS: return web.json_response({"ok": False, "error": "unknown language"})
            s = settings(); s["language"] = val; write_json(USER_DIR / "settings.json", s)   # the apps switch right away
            # The system locale (desktop, Hudiy) changes in the background; it needs locale files built first.
            loop.run_in_executor(None, run_helper, "language", val)
            usage("language", lang=val); out["restart"] = True; continue
        if key == "ssh": val = "on" if body[key] in (True, "on", 1) else "off"
        r = await loop.run_in_executor(None, run_helper, key, val)
        usage("system_set", key=key, ok=bool(r.get("ok")))
        if not r.get("ok"): return web.json_response(r)
    return web.json_response(out)

def session_env():
    """Environment for starting a window in the head unit's desktop session (labwc)."""
    env = dict(os.environ)
    run = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    env["XDG_RUNTIME_DIR"] = str(run)
    if not env.get("WAYLAND_DISPLAY"):
        socks = sorted(p.name for p in run.glob("wayland-[0-9]") if p.is_socket())
        env["WAYLAND_DISPLAY"] = socks[0] if socks else "wayland-0"
    return env

async def dev_api(request):
    action = request.match_info["action"]
    if action == "terminal":
        term = shutil.which("lxterminal") or shutil.which("x-terminal-emulator")
        if not term: return web.json_response({"ok": False, "error": "no terminal program installed"})
        subprocess.Popen([term, "--working-directory=" + str(Path.home())] if term.endswith("lxterminal") else [term],
                         env=session_env(), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        usage("dev_terminal"); return web.json_response({"ok": True})
    if action == "keyboard":   # Raspberry Pi OS's on-screen keyboard (squeekboard)
        r = subprocess.run(["busctl", "call", "--user", "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible", "b", "true"],
                           env=session_env(), capture_output=True, text=True, timeout=5)
        return web.json_response({"ok": r.returncode == 0, "error": r.stderr.strip()[-160:]})
    return web.json_response({"ok": False, "error": "unknown action"}, status=404)

# ---------------------------------------------------------------- software updates (bin/hu-update.py)
UPDATE_DIR = Path("/var/lib/headunit/update")

async def update_status(request):
    st = read_json(UPDATE_DIR / "status.json", {})
    st["version"] = VERSION
    st["pending"] = read_json(UPDATE_DIR / "state.json", {}).get("pending")
    st["enabled"] = C("UPDATES", "1") != "0"
    return web.json_response(st)

async def update_check(request):
    r = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "headunit-update-now.service"], capture_output=True, text=True)
    usage("update_check_request", ok=r.returncode == 0)
    if r.returncode != 0:
        return web.json_response({"ok": False, "message": "Couldn't start the update check: " + (r.stderr.strip()[-160:] or "unknown error")})
    return web.json_response({"ok": True, "message": "Checking for updates…"})

async def display_status(request):
    st = read_json(USER_DIR / "display-status.json", {})
    st["night"] = read_json(STATE_FILE, {}).get("illum", False)
    st["settings"] = {**DEFAULT_SETTINGS["display"], **settings().get("display", {})}
    return web.json_response(st)

async def logs_summary(request):
    files = sorted(LOG_DIR.glob("*.jsonl"))
    events = 0
    for f in files[-30:]:
        with open(f, "rb") as fh:
            events += sum(1 for _ in fh)
    return web.json_response({"days": len(files), "events": events, "bytes": sum(f.stat().st_size for f in files)})

async def export_watch(app):
    """Copy usage logs to a USB stick labeled HUEXPORT (mounted read-write by the udev rule)."""
    done = set()
    while True:
        for d in MEDIA_ROOT.glob("HUEXPORT*") if MEDIA_ROOT.exists() else []:
            if os.path.ismount(d) and str(d) not in done:
                dest = d / "headunit-logs" / (C("VEHICLE_ID", "car") + time.strftime("-%Y%m%d-%H%M"))
                try:
                    dest.mkdir(parents=True, exist_ok=True)
                    for f in LOG_DIR.glob("*.jsonl"): shutil.copy2(f, dest / f.name)
                    for f in (USER_DIR / "settings.json", MAPS_DIR / "manifest.json"):
                        if f.exists(): shutil.copy2(f, dest / f.name)
                    subprocess.run(["sync"]); usage("logs_exported", to=str(dest))
                except Exception as e:
                    usage("logs_export_failed", err=str(e)[:200])
                done.add(str(d))
        done = {x for x in done if os.path.ismount(x)}
        await asyncio.sleep(5)

# ---------------------------------------------------------------- app
@web.middleware
async def nocache(request, handler):
    resp = await handler(request)
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

async def index(request):
    raise web.HTTPFound("/apps/nav/index.html")

def hudiy_hotspot_guard():
    """Hudiy turns its own Wi-Fi hotspot on by default, which stops the Pi joining home Wi-Fi / the phone
    hotspot (needed for updates). With PI_WIFI=client, switch it back off in Hudiy's config; Hudiy reads
    it the next time it starts. hu-hudiy-config.py does the edit (and keeps the Drive menu)."""
    if C("PI_WIFI", "client") != "client":
        return
    cfg = read_json(Path.home() / ".hudiy" / "share" / "config" / "main_configuration.json", None)
    def on(o):
        if isinstance(o, dict):
            return any((k == "hotspot" and isinstance(v, dict) and v.get("enabled")) or on(v) for k, v in o.items())
        return False
    if cfg and on(cfg):
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "hu-hudiy-config.py")], capture_output=True, text=True)
        usage("hudiy_hotspot_off", ok=r.returncode == 0)

async def on_start(app):
    app["gps"] = asyncio.create_task(gps.run())
    app["media"] = asyncio.create_task(media_watch(app))
    app["export"] = asyncio.create_task(export_watch(app))
    app["daynight"] = asyncio.create_task(day_night_watch())
    usage("bridge_start", vehicle=C("VEHICLE_ID", ""))
    asyncio.get_running_loop().run_in_executor(None, hudiy_hotspot_guard)
    if C("PHONE_LINK", "1") == "1":
        try: start_phone_link()
        except Exception as e: log.warning("phone link off: %s", e)

# ---------------------------------------------------------------- phone (CarPlay / Android Auto through Hudiy)
phone = None
def start_phone_link():
    """Listen to Hudiy for the phone's next turn, now playing and calls, and keep the offline map in front
    when a phone connects (Settings > Driving > When a phone connects). See bin/hu_phone.py."""
    global phone
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hu_hudiy_api as H
    from hu_phone import PhoneState
    link = None
    def dispatch(action):
        if link: link.action(action)
    phone = PhoneState(dispatch, lambda: "carplay" if settings().get("phoneScreen") == "carplay" else "map", log=log)
    link = H.HudiyLink("headunit-phone", port=H.api_port(), on_message=phone.handle,
                       subscriptions=[H.SUB_PROJECTION, H.SUB_MEDIA, H.SUB_NAVIGATION, H.SUB_PHONE, H.SUB_CURRENT_MENU_ACTION])

async def phone_api(request):
    what = request.match_info.get("what", "")
    if phone is None:
        return web.json_response({"connected": False, "error": "hudiy_link_off"})
    if request.method == "GET":
        if what in ("icon", "cover"):
            data, typ = phone.image(what)
            if not data: raise web.HTTPNotFound()
            return web.Response(body=data, content_type=typ, headers={"Cache-Control": "max-age=3600"})
        return web.json_response(phone.status())
    if what == "show":
        usage("phone_show", source=phone.show_phone()); return web.json_response({"ok": True})
    if what in ("toggle", "next", "previous"):
        return web.json_response({"ok": phone.media_key(what)})
    raise web.HTTPNotFound()

async def on_stop(app):
    await radio.stop()

def make_app():
    app = web.Application(middlewares=[nocache], client_max_size=2 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/phone", phone_api)
    app.router.add_get("/api/sun", sun_api)
    app.router.add_route("*", "/api/phone/{what}", phone_api)
    app.router.add_get("/api/gps/stream", gps_stream)
    app.router.add_post("/api/route", route)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/music/{action}", music)
    app.router.add_post("/api/music/control", music_control)
    app.router.add_route("*", "/api/radio/{action}", radio_api)
    app.router.add_post("/api/say", say)
    app.router.add_route("*", "/api/settings", settings_api)
    app.router.add_route("*", "/api/favorites", favorites_api)
    app.router.add_post("/api/log", log_api)
    app.router.add_post("/api/maps/update", maps_update)
    app.router.add_get("/api/maps/update-status", maps_update_status)
    app.router.add_get("/api/logs/summary", logs_summary)
    app.router.add_get("/api/display/status", display_status)
    app.router.add_get("/api/update/status", update_status)
    app.router.add_route("*", "/api/system", system_api)
    app.router.add_post("/api/dev/{action}", dev_api)
    app.router.add_post("/api/update/check", update_check)
    app.router.add_static("/apps", APP_DIR, follow_symlinks=False)
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/maps", MAPS_DIR, follow_symlinks=True)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app

if __name__ == "__main__":
    web.run_app(make_app(), host=C("BRIDGE_HOST", "127.0.0.1"), port=PORT, access_log=None)
