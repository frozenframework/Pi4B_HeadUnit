#!/usr/bin/env python3
"""
Resumable download helper for the map updater (bin/hu-update-maps.sh).

`pmtiles extract` reads the map in a few dozen large byte ranges (~170 MB each for the whole US) and
gives up on the first network error, with no resume: a dropped connection at 87% of 15 GB means starting
over. This tiny local proxy sits in between:
  - every range the extractor asks for is downloaded to a cache file first, retrying (and continuing
    from the last byte received) on any network error, then handed over;
  - ranges already in the cache are served straight from disk, so running the extractor again after a
    failure (or after a power cut) only downloads what's still missing.

    hu-range-proxy.py --upstream https://build.protomaps.com --cache DIR --port 8766
Then: pmtiles extract http://127.0.0.1:8766/<build>.pmtiles ...

With --stats FILE it also writes its counters there every second, for the progress bar in Settings:
  served    bytes handed to the extractor          net       bytes downloaded from the internet
  inflight  bytes of pieces still being downloaded
"""
import argparse, asyncio, hashlib, json, logging, os, re, time
from pathlib import Path
from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger("range-proxy")
RANGE = re.compile(r"^bytes=(\d+)-(\d+)$")

async def fetch_range(app, name, start, end, dest: Path):
    """Download [start, end] of upstream/name into dest, resuming on errors. Returns when complete."""
    want = end - start + 1
    part = dest.with_suffix(".part")
    try:
        return await _fetch(app, name, start, end, dest, want, part)
    finally:
        app["inflight"].pop(dest.name, None)

async def _fetch(app, name, start, end, dest, want, part):
    for attempt in range(1, app["tries"] + 1):
        have = part.stat().st_size if part.exists() else 0
        if have >= want:
            break
        try:
            hdr = {"Range": f"bytes={start + have}-{end}"}
            async with app["session"].get(f"{app['upstream']}/{name}", headers=hdr) as r:
                total = r.headers.get("Content-Range", "").rpartition("/")[2]
                if total.isdigit():                                 # a range running past the end of the file
                    app["sizes"][name] = total
                    want = min(want, int(total) - start)
                if r.status == 416 and have >= want:               # everything up to the end is already here
                    break
                if r.status not in (200, 206):
                    raise IOError(f"HTTP {r.status}")
                if r.status == 200 and have:                       # server ignored Range: start over
                    have = 0; part.unlink(missing_ok=True)
                with open(part, "ab") as f:
                    got = f.tell()
                    async for chunk in r.content.iter_chunked(1 << 20):
                        f.write(chunk); got += len(chunk)
                        app["net"] += len(chunk); app["inflight"][dest.name] = got
        except Exception as e:                                    # dropped stream, reset, timeout...
            done = part.stat().st_size if part.exists() else 0
            log.warning("range %d-%d: %s at %d/%d bytes (try %d), continuing", start, end, e, done, want, attempt)
            await asyncio.sleep(min(30, app["backoff"] * attempt))
    if not part.exists() or part.stat().st_size < want:
        raise IOError(f"range {start}-{end} still incomplete after {app['tries']} tries")
    if part.stat().st_size > want:                                # server sent more than asked
        with open(part, "r+b") as f: f.truncate(want)
    os.replace(part, dest)

async def handle(request):
    app = request.app
    name = request.match_info["name"]
    if not re.fullmatch(r"[\w.-]+", name):
        raise web.HTTPNotFound()
    m = RANGE.match(request.headers.get("Range", ""))
    if not m:                                                     # only ranged reads are needed
        raise web.HTTPRequestRangeNotSatisfiable()
    start, end = int(m.group(1)), int(m.group(2))
    key = hashlib.sha1(f"{name}:{start}-{end}".encode()).hexdigest()
    dest = app["cache"] / f"{key}.bin"
    lock = app["locks"].setdefault(key, asyncio.Lock())
    async with lock:
        if not dest.exists():
            await fetch_range(app, name, start, end, dest)
    size = dest.stat().st_size
    total = app["sizes"].get(name, "*")
    resp = web.StreamResponse(status=206, headers={"Content-Range": f"bytes {start}-{start + size - 1}/{total}",
                                                   "Content-Length": str(size), "Accept-Ranges": "bytes"})
    await resp.prepare(request)
    with open(dest, "rb") as f:
        while chunk := f.read(1 << 20):
            await resp.write(chunk)
    await resp.write_eof()
    app["served"] += size
    return resp

async def head(request):
    app = request.app; name = request.match_info["name"]
    async with app["session"].head(f"{app['upstream']}/{name}") as r:
        if r.headers.get("Content-Length"): app["sizes"][name] = r.headers["Content-Length"]
        return web.Response(status=r.status, headers={"Content-Length": r.headers.get("Content-Length", "0"), "Accept-Ranges": "bytes"})

async def write_stats(app):
    """Counters for the progress bar, rewritten every second while the proxy runs."""
    path = app["stats"]
    while True:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"served": app["served"], "net": app["net"],
                                       "inflight": sum(app["inflight"].values()), "t": time.time()}))
            tmp.replace(path)
        except OSError:
            pass
        await asyncio.sleep(1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--port", type=int, default=8766); ap.add_argument("--tries", type=int, default=12)
    ap.add_argument("--backoff", type=float, default=2.0); ap.add_argument("--read-timeout", type=float, default=60)
    ap.add_argument("--stats", help="write served/downloaded byte counters to this JSON file every second")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    app = web.Application()
    app.update(upstream=a.upstream.rstrip("/"), cache=Path(a.cache), tries=a.tries, backoff=a.backoff, read_timeout=a.read_timeout, locks={}, sizes={},
               served=0, net=0, inflight={}, stats=Path(a.stats) if a.stats else None)
    app["cache"].mkdir(parents=True, exist_ok=True)
    async def start(app):
        app["session"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=30, sock_read=app["read_timeout"]))
        if app["stats"]: app["stats_task"] = asyncio.create_task(write_stats(app))
    async def stop(app):
        if app.get("stats_task"): app["stats_task"].cancel()
        await app["session"].close()
    app.on_startup.append(start); app.on_cleanup.append(stop)
    app.router.add_get("/{name}", handle, allow_head=False); app.router.add_route("HEAD", "/{name}", head)
    web.run_app(app, host="127.0.0.1", port=a.port, access_log=None, print=None)

if __name__ == "__main__":
    main()
