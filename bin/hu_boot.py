"""
Start-up timeline: how long from Linux starting to the map on screen, per step, so a slow start can be
seen (Settings > Developer) and the slow step found.

  mark(name)      record "name reached at <seconds since Linux started>" for this boot
                  (desktop, bridge, hudiy, map); kept in the user's runtime folder (gone at power-off)
  marks()         {name: seconds} for this boot
  systemd()       the kernel / system part from systemd-analyze, when it has finished
"""
import json, os, re, subprocess, time
from pathlib import Path

def _file():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "headunit-boot.json"

def uptime():
    try: return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError): return time.monotonic()

def boot_id():
    try: return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError: return ""

def marks():
    try:
        d = json.loads(_file().read_text())
        return d.get("marks", {}) if d.get("boot") == boot_id() else {}
    except (OSError, ValueError):
        return {}

def mark(name, at=None, first_only=True):
    m = marks()
    if first_only and name in m: return m[name]
    m[name] = round(uptime() if at is None else at, 2)
    try:
        f = _file(); tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"boot": boot_id(), "marks": m})); tmp.replace(f)
    except OSError:
        pass
    return m[name]

def systemd():
    """{'kernel': s, 'userspace': s, 'graphical': s} from systemd-analyze (empty while still starting)."""
    try:
        out = subprocess.run(["systemd-analyze"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    def secs(s):
        t = 0.0
        for v, u in re.findall(r"([\d.]+)(min|ms|s)", s): t += float(v) * {"min": 60, "s": 1, "ms": 0.001}[u]
        return round(t, 2)
    res = {}
    m = re.search(r"in ((?:[\d.]+(?:min|ms|s) ?)+) \(kernel\)", out)
    if m: res["kernel"] = secs(m.group(1))
    m = re.search(r"\+ ((?:[\d.]+(?:min|ms|s) ?)+) \(initrd\)", out)
    if m: res["initrd"] = secs(m.group(1))
    m = re.search(r"\+ ((?:[\d.]+(?:min|ms|s) ?)+) \(userspace\)", out)
    if m: res["userspace"] = secs(m.group(1))
    m = re.search(r"graphical\.target reached after (.+?) in userspace", out)
    if m: res["graphical"] = secs(m.group(1))
    return res

def summary():
    """This boot's timeline, all in seconds since Linux started: system (graphical target), desktop,
    bridge, hudiy, map (some may be missing)."""
    m = dict(marks()); s = systemd()
    if "graphical" in s: m["system"] = round(s.get("kernel", 0) + s.get("initrd", 0) + s["graphical"], 2)
    return m
