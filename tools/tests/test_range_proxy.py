"""Resumable map download: the local range proxy survives an upstream that drops connections mid-range
and errors out, serves exact bytes, and a second run reuses the cache instead of downloading again."""
import asyncio, os, random, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from aiohttp import web
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
data = random.Random(1).randbytes(6_000_000)
stats = {"requests": 0, "bytes": 0}

async def upstream(request):
    stats["requests"] += 1
    a, b = map(int, request.headers["Range"].split("=")[1].split("-"))
    if stats["requests"] % 5 == 0:
        return web.Response(status=500)                           # flaky server
    resp = web.StreamResponse(status=206, headers={"Content-Range": f"bytes {a}-{b}/{len(data)}"})
    await resp.prepare(request)
    body = data[a:b + 1]
    cut = len(body) // 2 if stats["requests"] % 3 == 0 else len(body)   # drop the connection halfway
    await resp.write(body[:cut]); stats["bytes"] += cut
    if cut < len(body):
        request.transport.close(); return resp
    await resp.write_eof(); return resp

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

async def run_upstream(port):
    app = web.Application(); app.router.add_get("/{name}", upstream)
    r = web.AppRunner(app); await r.setup(); await web.TCPSite(r, "127.0.0.1", port).start()
    while True: await asyncio.sleep(3600)

up = free_port(); px = free_port()
import threading
threading.Thread(target=lambda: asyncio.run(run_upstream(up)), daemon=True).start()
def proxy():
    return subprocess.Popen([sys.executable, str(ROOT / "bin/hu-range-proxy.py"), "--upstream", f"http://127.0.0.1:{up}",
                             "--cache", str(T / "cache"), "--port", str(px), "--tries", "20", "--backoff", "0.05", "--read-timeout", "3"], stderr=open(T / "proxy.log", "a"))
p = proxy(); time.sleep(1.5)
ranges = [(0, 16383)] + [(16384 + i * 749_000, 16383 + (i + 1) * 749_000) for i in range(8)]
def get(a, b):
    req = urllib.request.Request(f"http://127.0.0.1:{px}/20261104.pmtiles", headers={"Range": f"bytes={a}-{b}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        assert r.status == 206; return r.read()
for a, b in ranges:
    assert get(a, b) == data[a:b + 1], (a, b)
first = stats["bytes"]
assert stats["requests"] > len(ranges) + 2, stats   # some retries really happened (500s and cut-offs)
print("1) exact bytes despite dropped connections and HTTP 500s; upstream requests:", stats["requests"], "for", len(ranges), "ranges")
p.terminate(); p.wait()
p = proxy(); time.sleep(1.5)                                     # a new run (e.g. after a power cut)
for a, b in ranges:
    assert get(a, b) == data[a:b + 1]
assert stats["bytes"] == first, "second run downloaded again"
print("2) second run served everything from the cache, nothing downloaded again")
try:
    urllib.request.urlopen(f"http://127.0.0.1:{px}/../etc", timeout=5); raise SystemExit("bad name accepted")
except urllib.error.HTTPError as e:
    assert e.code in (403, 404)
p.terminate()
print("range proxy: all tests passed")
