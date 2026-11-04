"""Map download progress bar: hu-maps-progress.py turns the extractor's output and the range proxy's
counters into percent / size / speed / time left, passes log lines through, and swallows bar redraws."""
import json, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp()); status, stats = T / "status.json", T / "stats.json"
GB = 1024 ** 3
def put_stats(served, net, inflight=0): stats.write_text(json.dumps({"served": served, "net": net, "inflight": inflight}))
def read(): return json.loads(status.read_text())

put_stats(5_000_000, 5_000_000)                       # directories already fetched before the chunk phase
p = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-maps-progress.py"), "--status", str(status), "--stats", str(stats),
                      "--name", "Whole_US", "--try", "2", "--tries", "5"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def send(s): p.stdin.write(s); p.stdin.flush()

send("2026/11/04 18:00:00 extract.go:450: fetching 900000 tiles, 1200 chunks, 300 requests\n")
time.sleep(0.5); s = read()
assert s["phase"] == "preparing" and s["percent"] is None and s["try"] == 2, s

# chunk phase starts (blank 0% bar), then the internet delivers 1.5 GB over the next ~12 s
send("\rfetching chunks   0% |          | ( 0 B/15 GB) [0s:0s]")
t0 = time.time()
for i in range(1, 7):
    time.sleep(2.1)
    net = 5_000_000 + int(i * 0.25 * GB)
    served = 5_000_000 + int(i * 0.25 * GB) - 100_000_000     # one 100 MB piece still in flight
    put_stats(served, net, inflight=100_000_000)
    pct = int((served - 5_000_000) / (15 * GB) * 100)
    send(f"\rfetching chunks  {pct:2d}% |█         | ({(served - 5e6) / GB:.1f} GB/15 GB, 120 MB/s) [{i}s:1m]")
time.sleep(4.5); s = read()
want = 1.5 * GB / (15 * GB)                                    # 10 %
assert s["phase"] == "downloading", s
assert abs(s["percent"] - want * 100) < 1.0, s
assert s["total"] == 15 * GB and abs(s["done"] - 1.5 * GB) < 0.15 * GB, s
rate = 0.25 * GB / 2.1
assert s["rate"] and 0.6 * rate < s["rate"] < 1.5 * rate, (s["rate"], rate)
assert s["eta"] and abs(s["eta"] - 13.5 * GB / rate) / (13.5 * GB / rate) < 0.5, s
assert "about" in s["message"] and "left" in s["message"], s["message"]
print("1) downloading:", s["message"])

send("\rfetching chunks 100% |██████████| (15/15 GB, 120 MB/s)\n2026/11/04 18:30:00 extract.go:606: Completed in 30m with 4 download threads\n")
p.stdin.close(); out = p.stdout.read(); p.wait(5)
assert p.returncode == 0
assert "fetching chunks" not in out and "fetching 900000 tiles" in out and "Completed in" in out, out
s = read(); assert s["percent"] == 100.0, s
print("2) log lines pass through to the journal, bar redraws don't; ends at 100%")

# Garbage input and a missing stats file never break the pipe
status.unlink(); stats.unlink()
r = subprocess.run([sys.executable, str(ROOT / "bin/hu-maps-progress.py"), "--status", str(status), "--stats", str(stats)],
                   input="\rfetching chunks  12% |█ | (weird\n\x00\xff junk\n", capture_output=True, text=True, timeout=10)
assert r.returncode == 0 and "junk" in r.stdout, r
print("3) odd output and no proxy counters: still passes through, exit 0")
print("maps progress: all tests passed")
