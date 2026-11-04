#!/usr/bin/env python3
"""
Progress bar data for Settings > Maps while bin/hu-update-maps.sh downloads the map.

    pmtiles extract ... 2>&1 | hu-maps-progress.py --status update-status.json --stats proxy-stats.json \
                                                   --name "Texas" --try 1 --tries 5

Reads the extractor's output (log lines pass through to the journal; its "fetching chunks NN% ...(x/y GB"
bar is parsed instead of printed) and the range proxy's byte counters, and every 2 seconds writes
update-status.json with:
    phase     preparing | downloading      percent  0-100 (one decimal)
    done, total   bytes (total is the extractor's own estimate)
    rate      bytes/second actually coming from the internet over the last few minutes
    eta       seconds left (null until there's enough to go on)
    message   the same in English, for the journal and older screens
Never fails the download: if anything here goes wrong, the output still passes through.
"""
import argparse, json, os, re, select, sys, time
from collections import deque
from pathlib import Path

BAR = re.compile(r"fetching chunks\s+(\d+)%.*?\(\s*([\d.]+)\s*([kMGTPE]?B)?\s*/\s*([\d.]+)\s*([kMGTPE]?B)")
UNIT = {"B": 1, "kB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4, "PB": 1024 ** 5, "EB": 1024 ** 6}
WINDOW = 180          # seconds of download speed the time-left estimate averages over

def say(line):
    try: print(line, flush=True)
    except (BrokenPipeError, OSError): pass                 # nobody reading the log: keep going anyway

def human(n):
    for u in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB": return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024

def human_time(s):
    m = max(1, round(s / 60))
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"

class Progress:
    def __init__(self, a):
        self.a = a; self.pct = None; self.total = None; self.base = None
        self.samples = deque(); self.last_write = 0

    def stats(self):
        try: return json.loads(Path(self.a.stats).read_text())
        except (OSError, ValueError, TypeError): return {}

    def bar(self, seg):
        m = BAR.search(seg)
        if not m: return False
        pct, done_v, done_u, tot_v, tot_u = m.groups()
        if self.pct is None:                               # chunk download starts: count from here
            self.base = self.stats().get("served", 0)
        self.pct = int(pct); self.total = float(tot_v) * UNIT[tot_u]
        return True

    def snapshot(self, now):
        st = self.stats()
        net = st.get("net")
        if net is not None:
            self.samples.append((now, net))
            while self.samples and now - self.samples[0][0] > WINDOW: self.samples.popleft()
        rate = None
        if len(self.samples) >= 2 and self.samples[-1][0] - self.samples[0][0] >= 10:
            (t0, n0), (t1, n1) = self.samples[0], self.samples[-1]
            rate = (n1 - n0) / (t1 - t0)
        out = {"t": int(now), "try": self.a.try_, "tries": self.a.tries, "rate": round(rate) if rate else None}
        if self.pct is None:
            out.update(phase="preparing", percent=None, eta=None,
                       message=f"Downloading {self.a.name} map: getting ready")
            return out
        frac = self.pct / 100
        if self.total and self.base is not None and "served" in st:
            exact = (st["served"] - self.base + st.get("inflight", 0)) / self.total
            # the extractor's percent is exact but whole numbers only; the proxy's bytes fill in between
            frac = min(max(exact, self.pct / 100), (self.pct + 0.99) / 100) if self.pct < 100 else 1.0
        done = frac * self.total if self.total else None
        eta = None
        if rate and rate > 1024 and self.total:
            eta = round((1 - frac) * self.total / rate)
        msg = f"Downloading {self.a.name} map: {frac * 100:.0f}%"
        if done: msg += f" ({human(done)} of {human(self.total)})"
        if eta is not None: msg += f", about {human_time(eta)} left"
        out.update(phase="downloading", percent=round(frac * 100, 1), done=round(done) if done else None,
                   total=round(self.total) if self.total else None, eta=eta, message=msg)
        return out

    def write(self, force=False):
        now = time.time()
        if not force and now - self.last_write < 2: return
        self.last_write = now
        try:
            p = Path(self.a.status); tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot(now)) + "\n"); tmp.replace(p)
        except Exception as e:                              # never break the download over a status file
            print("progress: couldn't write status:", e, file=sys.stderr)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True); ap.add_argument("--stats", default="")
    ap.add_argument("--name", default=""); ap.add_argument("--try", dest="try_", type=int, default=1)
    ap.add_argument("--tries", type=int, default=1)
    a = ap.parse_args(); a.name = a.name.replace("_", " ")
    p = Progress(a); fd = sys.stdin.fileno(); buf = b""
    p.write(force=True)
    while True:
        ready, _, _ = select.select([fd], [], [], 2)
        if ready:
            data = os.read(fd, 65536)
            if not data: break
            buf += data
            *segs, buf = re.split(rb"[\r\n]", buf)
            for s in segs:
                seg = s.decode("utf-8", "replace").strip()
                if not seg: continue
                try: is_bar = p.bar(seg) or re.search(r"\d+%\s*\|", seg)
                except Exception: is_bar = False
                if not is_bar: say(seg)       # log lines -> journal; bars are too chatty
            # a bar redraw starts with \r and has no line end, so the newest one is still in buf
            try: p.bar(buf.decode("utf-8", "replace"))
            except Exception: pass
        p.write()
    tail = buf.decode("utf-8", "replace").strip()
    if tail and not p.bar(tail) and not re.search(r"\d+%\s*\|", tail): say(tail)
    p.write(force=True)

if __name__ == "__main__":
    main()
