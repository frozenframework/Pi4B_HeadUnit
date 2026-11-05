#!/usr/bin/env python3
"""
Send routing + search (the HEADUNIT_MAPS folder from maps/build-routing.py) to a head unit over Wi-Fi,
instead of a USB stick. Works on macOS, Linux and Windows 10/11 (uses the ssh that comes with them).

    python3 tools/push-maps.py HEADUNIT_MAPS --host miata-headunit.local

Before the first push:
  - the head unit and this computer are on the same Wi-Fi (home network or your phone's hotspot)
  - on the head unit: Settings > Developer > SSH login over Wi-Fi: On
  - optional, saves typing the password for every step:
        python3 tools/push-maps.py --setup-key --host miata-headunit.local

What it does:
  1. sends each file into /var/lib/headunit/incoming/HEADUNIT_MAPS on the Pi, with a progress line
     (percent, speed, time left). If Wi-Fi drops, it reconnects and continues from the last byte, and
     running it again later also continues where it stopped: nothing is sent twice.
  2. runs the head unit's importer (bin/hu-import-maps.sh), which moves the files into place. Routing
     and search work right away, no restart needed.
"""
import argparse, json, os, shlex, subprocess, sys, time
from pathlib import Path

FILES = ["valhalla_tiles.tar", "search.sqlite", "basemap.pmtiles", "manifest.json"]   # manifest last
INBOX = "/var/lib/headunit/incoming/HEADUNIT_MAPS"
CHUNK = 1 << 20

def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB": return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024

def human_time(s):
    s = int(s)
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60} min {s % 60:02d} s"

class Pi:
    def __init__(self, user, host, port):
        self.target = f"{user}@{host}"
        self.base = ["ssh", "-p", str(port), "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
                     "-o", "ConnectTimeout=20"]
        if os.name != "nt":   # one password prompt for the whole run (Windows' ssh has no connection sharing)
            sock = f"/tmp/hu-push-{os.getuid()}-%C"      # short path: macOS limits socket paths to 104 chars
            self.base += ["-o", "ControlMaster=auto", "-o", f"ControlPath={sock}", "-o", "ControlPersist=120"]

    def run(self, cmd, **kw):
        return subprocess.run(self.base + [self.target, cmd], text=True, capture_output=True, **kw)

    def size(self, path):
        r = self.run(f"stat -c %s {shlex.quote(path)} 2>/dev/null || echo -1")
        if r.returncode != 0:
            raise ConnectionError(r.stderr.strip() or "ssh failed")
        return int(r.stdout.strip().splitlines()[-1])

    def append(self, path, src, offset, total, label):
        """Stream src[offset:] onto the end of path on the Pi. Returns bytes sent (may stop early)."""
        p = subprocess.Popen(self.base + [self.target, f"cat >> {shlex.quote(path)}"], stdin=subprocess.PIPE)
        sent, t0, last = 0, time.time(), 0
        try:
            with open(src, "rb") as f:
                f.seek(offset)
                while chunk := f.read(CHUNK):
                    p.stdin.write(chunk); sent += len(chunk)
                    now = time.time()
                    if now - last > 1 or offset + sent == total:
                        last = now; rate = sent / max(now - t0, 1e-3); done = offset + sent
                        left = (total - done) / rate if rate > 0 else 0
                        sys.stdout.write(f"\r  {label}: {done * 100 / total:5.1f}%  {human(done)} of {human(total)}"
                                         f"  {human(rate)}/s  {human_time(left)} left   ")
                        sys.stdout.flush()
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        p.wait()
        print()
        return sent

def push_file(pi, local, tries):
    total = local.stat().st_size
    remote = f"{INBOX}/{local.name}"
    part = remote + ".part"
    if pi.size(remote) == total:
        print(f"  {local.name}: already on the head unit"); return
    for attempt in range(1, tries + 1):
        try:
            have = pi.size(part)
            if have > total:                                       # different file than last time: start over
                pi.run(f"rm -f {shlex.quote(part)}"); have = 0
            have = max(have, 0)
            if have < total:
                if have: print(f"  {local.name}: continuing from {human(have)}")
                pi.append(part, local, have, total, local.name)
            if pi.size(part) == total:
                r = pi.run(f"mv -f {shlex.quote(part)} {shlex.quote(remote)}")
                if r.returncode == 0: return
        except ConnectionError as e:
            print(f"  connection problem: {e}")
        wait = min(60, 5 * attempt)
        print(f"  {local.name}: transfer interrupted, reconnecting in {wait} s (try {attempt} of {tries})")
        time.sleep(wait)
    sys.exit(f"{local.name} didn't finish. Run the same command again: it continues where it stopped.")

def setup_key(pi):
    ssh_dir = Path.home() / ".ssh"; key = ssh_dir / "id_ed25519"
    if not (key.with_suffix(".pub")).exists():
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "headunit-push", "-f", str(key)], check=True)
    pub = key.with_suffix(".pub").read_text().strip()
    cmd = ("umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
           f"grep -qxF {shlex.quote(pub)} ~/.ssh/authorized_keys || echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys")
    r = subprocess.run(pi.base + [pi.target, cmd])
    print("key installed: no more password prompts" if r.returncode == 0 else "couldn't install the key")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default="HEADUNIT_MAPS", help="the HEADUNIT_MAPS folder (default: ./HEADUNIT_MAPS)")
    ap.add_argument("--host", default="miata-headunit.local", help="head unit name or IP (f250-headunit.local, frontier-headunit.local, 192.168.x.x)")
    ap.add_argument("--user", default="headunit"); ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--tries", type=int, default=20, help="reconnect attempts per file")
    ap.add_argument("--setup-key", action="store_true", help="install this computer's SSH key on the head unit, then stop")
    a = ap.parse_args()
    pi = Pi(a.user, a.host, a.port)
    if a.setup_key:
        setup_key(pi); return
    folder = Path(a.folder)
    man = folder / "manifest.json"
    if not man.exists():
        sys.exit(f"{folder} has no manifest.json: point this at the HEADUNIT_MAPS folder made by maps/build-routing.py")
    m = json.loads(man.read_text())
    files = [folder / n for n in FILES if (folder / n).exists()]
    total = sum(f.stat().st_size for f in files)
    print(f"Sending {m.get('name', m.get('region'))} (built {m.get('built', '?')}), {human(total)}, to {a.host}")
    r = pi.run(f"mkdir -p {INBOX} && df -B1 --output=avail /var/lib/headunit | tail -1")
    if r.returncode != 0:
        sys.exit(f"Couldn't log in to {a.host}: {r.stderr.strip()}\n"
                 "Check: same Wi-Fi, and Settings > Developer > SSH login over Wi-Fi is On.")
    free = int(r.stdout.split()[-1])
    already = sum(max(pi.size(f"{INBOX}/{f.name}{s}"), 0) for f in files for s in ("", ".part"))
    if free + already < total + (1 << 30):
        sys.exit(f"Not enough space on the head unit: {human(free)} free, about {human(total - already)} more needed")
    for f in files:
        push_file(pi, f, a.tries)
    print("Installing on the head unit...")
    r = pi.run(f"sudo -n /opt/headunit/bin/hu-import-maps.sh {INBOX}")
    print(r.stdout.strip())
    if r.returncode != 0:
        sys.exit(f"Import failed: {r.stderr.strip()}")
    print("Done: routing and search are ready. Open Navigation on the head unit and search for a place.")

if __name__ == "__main__":
    main()
