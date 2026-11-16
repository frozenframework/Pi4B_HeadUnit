#!/usr/bin/env python3
"""
Quicker start: read ahead, from the SD card, exactly what the last start needed.

Starting Hudiy, its built-in browser and the map means reading a few hundred MB of programs and data off
the SD card in thousands of small scattered pieces, each one waited for. This service (root, started as
early as the system allows) does that reading in one steady pass while the rest of the system is still
starting, so by the time Hudiy and the map ask for those pieces they are already in memory.

It learns by itself what to read: once the map is on screen it looks at which parts of which files are
in memory (the programs that are running, the files they have open, the head-unit screens, the map
data around where the car is) and saves that list (/var/lib/headunit/preload.json) for the next start.
  - the list is made again after every software update, and
  - every 10th start nothing is read ahead: that start is measured as it is and the list made fresh
    (so pieces that are no longer needed drop out, and the two kinds of start can be compared in
    Settings > Developer).
Only a hint to the system's file cache: nothing is changed on disk, and a missing or stale list just
means an ordinary start.

    hu-preload.py              the service: read ahead, wait for the map, learn
    hu-preload.py --learn      learn now (prints what it found)
    hu-preload.py --status     what this start did
"""
import ctypes, glob, json, mmap, os, sys, time
from pathlib import Path

LIST = Path(os.environ.get("HU_PRELOAD_LIST", "/var/lib/headunit/preload.json"))
STATUS = Path(os.environ.get("HU_PRELOAD_STATUS", "/run/headunit-preload.json"))
CONF = Path(os.environ.get("HU_CONF", "/etc/headunit/headunit.conf"))
ROOT = Path(os.environ.get("HU_ROOT", "/opt/headunit"))
DATA = Path(os.environ.get("HU_DATA", "/var/lib/headunit"))
PAGE = os.sysconf("SC_PAGE_SIZE")
MAX_BYTES = int(os.environ.get("HU_PRELOAD_MAX_MB", "700")) << 20      # most a list may ask for
WINDOW = 1 << 30                                                      # big files are looked at 1 GB at a time
CALIBRATE_EVERY = 10
SKIP_PREFIX = ("/proc/", "/sys/", "/dev/", "/run/", "/tmp/", "/var/log/", "/var/lib/headunit/preload", "/memfd:", "/SYSV", "/anon")

def conf(key, default=""):
    try:
        for line in CONF.read_text().splitlines():
            if line.startswith(key + "="): return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return default

def version():
    try: return (ROOT / "VERSION").read_text().strip()
    except OSError: return "?"

# ------------------------------------------------------------------ which parts of a file are in memory
_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
_libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte)]

def resident(path, gap=8):
    """[[offset, length], ...] of the file's pages that are in the page cache (runs closer than `gap`
    pages are joined: one longer read beats two short ones). Needs root for files it doesn't own -
    without it the kernel says "all of it" for every file, which would be useless here."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        size = os.fstat(fd).st_size
        out, off = [], 0
        while off < size:
            n = min(WINDOW, size - off)
            addr = _libc.mmap(None, n, mmap.PROT_READ, mmap.MAP_SHARED, fd, off)
            if addr in (None, ctypes.c_void_p(-1).value): break
            pages = (n + PAGE - 1) // PAGE
            vec = (ctypes.c_ubyte * pages)()
            ok = _libc.mincore(addr, n, vec) == 0
            _libc.munmap(addr, n)
            if not ok: break
            raw = bytes(vec).translate(_BIT0); i = 0           # 1 = in memory, 0 = not
            while True:
                i = raw.find(b"\x01", i)
                if i < 0: break
                j = raw.find(b"\x00", i)
                if j < 0: j = pages
                start, ln = off + i * PAGE, (j - i) * PAGE
                if out and start - (out[-1][0] + out[-1][1]) <= gap * PAGE: out[-1][1] = start + ln - out[-1][0]
                else: out.append([start, ln])
                i = j
            off += n
        if out: out[-1][1] = min(out[-1][1], size - out[-1][0])
        return out
    finally:
        os.close(fd)

_BIT0 = bytes(b & 1 for b in range(256))      # mincore: only the lowest bit of each byte means "in memory"

# ------------------------------------------------------------------ what to look at
def candidates(home):
    """{path: order}: files mapped or open in running programs (ordered by when the program started),
    then the head-unit screens, programs and map data."""
    out = {}
    def add(p, order):
        if not p.startswith("/") or p.startswith(SKIP_PREFIX) or p.endswith(" (deleted)"): return
        if p not in out or order < out[p]: out[p] = order
    for d in glob.glob("/proc/[0-9]*"):
        try:
            with open(d + "/stat") as f: start = int(f.read().rsplit(")", 1)[1].split()[19])
            with open(d + "/maps") as f:
                for line in f:
                    parts = line.split(None, 5)
                    if len(parts) == 6: add(parts[5].rstrip("\n"), start)
            for fd in os.listdir(d + "/fd"):
                try: add(os.readlink(f"{d}/fd/{fd}"), start)
                except OSError: pass
        except (OSError, ValueError, IndexError):
            continue
    late = 1 << 62
    extra = [str(p) for base, pat in ((ROOT.resolve(), "apps/**/*"), (ROOT.resolve(), "bin/**/*"), (DATA, "maps/basemap.pmtiles"),
                                      (DATA, "routing/valhalla_tiles.tar"))
             for p in base.glob(pat)]
    if home:
        for sub in (".hudiy/share", ".cache", ".local/share"):
            base = Path(home) / sub
            for root, dirs, files in os.walk(base):
                if root.count(os.sep) - str(base).count(os.sep) > 6: dirs[:] = []
                if sub != ".hudiy/share" and "QtWebEngine" not in root and not any("QtWebEngine" in x or "hudiy" in x.lower() for x in dirs + [root]):
                    continue
                extra += [os.path.join(root, f) for f in files]
    for p in extra: add(p, late)
    return out

def learn(home=None, quiet=True):
    files, total = [], 0
    for path, order in sorted(candidates(home).items(), key=lambda kv: (kv[1], kv[0])):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path) or st.st_size == 0: continue
        ranges = resident(path)
        size = sum(r[1] for r in ranges)
        if not size: continue
        if total + size > MAX_BYTES: continue                 # (smaller ones further down may still fit)
        total += size
        files.append([path, st.st_size, int(st.st_mtime), ranges])
    old = read_list()
    data = {"version": version(), "learned": int(time.time()), "bytes": total, "boots": old.get("boots", 0) if old.get("version") == version() else 0,
            "files": files}
    tmp = LIST.with_suffix(".tmp"); tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, separators=(",", ":"))); os.replace(tmp, LIST)
    if not quiet: print(f"learned {len(files)} files, {total / 2**20:.0f} MB to read ahead next start")
    return data

def read_list():
    try: return json.loads(LIST.read_text())
    except (OSError, ValueError): return {}

# ------------------------------------------------------------------ reading ahead
def preload(data):
    """Read every listed piece once, in order. Returns (bytes, files)."""
    buf = bytearray(1 << 20); done = n = 0
    for path, size, mtime, ranges in data.get("files", []):
        try:
            st = os.stat(path)
            if st.st_size != size or int(st.st_mtime) != mtime: continue      # changed since it was learned
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            for off, ln in ranges:
                end = off + ln
                while off < end:
                    got = os.preadv(fd, [memoryview(buf)[:min(len(buf), end - off)]], off)
                    if got <= 0: break
                    off += got; done += got
            n += 1
        except OSError:
            pass
        finally:
            os.close(fd)
    return done, n

def uptime():
    try: return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError): return 0.0

def write_status(**kw):
    try:
        tmp = STATUS.with_suffix(".tmp"); tmp.write_text(json.dumps(kw)); os.replace(tmp, STATUS); os.chmod(STATUS, 0o644)
    except OSError:
        pass

def service():
    if conf("PRELOAD", "1") != "1":
        write_status(mode="off"); return
    data = read_list(); t0 = uptime()
    if data.get("version") != version() or not data.get("files"):
        mode = "learning"                                    # first start, or first after an update
    elif (data.get("boots", 0) + 1) % CALIBRATE_EVERY == 0:
        mode = "measuring"                                   # every 10th: an ordinary start, list made fresh
    else:
        mode = "preload"
    status = {"mode": mode, "from": round(t0, 1)}
    if mode == "preload":
        write_status(**status, busy=True)
        done, n = preload(data)
        status.update(mb=round(done / 2**20), files=n, s=round(uptime() - t0, 1))
    if data.get("files"):                                    # count this start (kept even if learning below fails)
        data["boots"] = data.get("boots", 0) + 1
        try: LIST.write_text(json.dumps(data, separators=(",", ":")))
        except OSError: pass
    write_status(**status)
    # learn once the map is on screen (or, when it never comes up, a few minutes in)
    user = conf("HU_USER"); home = uid = None
    try:
        import pwd
        pw = pwd.getpwnam(user) if user else pwd.getpwuid(1000); home, uid = pw.pw_dir, pw.pw_uid
    except (KeyError, ImportError):
        pass
    marks = Path(os.environ.get("HU_BOOT_MARKS", f"/run/user/{uid}/headunit-boot.json"))
    deadline = time.time() + float(os.environ.get("HU_PRELOAD_WAIT", "240"))
    while time.time() < deadline:
        try:
            if "map" in json.loads(marks.read_text()).get("marks", {}): break
        except (OSError, ValueError):
            pass
        time.sleep(3)
    else:
        return                                               # the map never came up: nothing worth learning from
    time.sleep(float(os.environ.get("HU_PRELOAD_SETTLE", "15")))
    try: os.nice(15)
    except OSError: pass
    if mode != "preload":                                    # a list from an ordinary start only: what was really needed
        d = learn(home)
        status.update(learned_mb=round(d["bytes"] / 2**20), learned_files=len(d["files"]))
        write_status(**status)

if __name__ == "__main__":
    if "--learn" in sys.argv:
        import pwd
        try: home = pwd.getpwnam(conf("HU_USER")).pw_dir
        except KeyError: home = None
        learn(home, quiet=False)
    elif "--status" in sys.argv:
        print(STATUS.read_text() if STATUS.exists() else "{}")
    else:
        service()
