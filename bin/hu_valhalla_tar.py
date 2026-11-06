#!/usr/bin/env python3
"""
Valhalla's routing tiles in one tar, with index.bin first - so the router starts in about a second.

Without index.bin, Valhalla has to walk every tile header through the whole tar (tens of thousands of
small reads spread over many GB of SD card) before the first route can be worked out; that's the long
wait for the map's first route after the head unit starts. index.bin (the same format as Valhalla's own
valhalla_build_extract) lists every tile's offset, id and size, so it's read in one go instead.

    has_index(tar)                    is index.bin the first file?
    pack(tiles_dir, tar)              the map builder: tiles -> tar with index.bin
    add_index(tar, progress)          an existing tar without it: rewritten next to itself, then swapped in
                                      (needs as much free space as the tar; nothing changes if it stops)

    python3 hu_valhalla_tar.py /var/lib/headunit/routing/valhalla_tiles.tar     (on the head unit)
"""
import json, os, shutil, struct, sys, tarfile, time
from pathlib import Path

INDEX = "index.bin"
ENTRY = struct.Struct("<QLL")            # data offset in the tar, tile id (level | index << 3), size
BLOCK = tarfile.BLOCKSIZE

def tile_id(name):
    """'2/000/753/634.gph' -> 2 | 753634 << 3 (Valhalla's GraphId of the tile)"""
    level, rest = name[:-4].replace("\\", "/").split("/", 1)
    return int(level) | (int(rest.replace("/", "")) << 3)

def has_index(path):
    try:
        with open(path, "rb") as f:
            head = f.read(BLOCK)
        return len(head) == BLOCK and head[:100].split(b"\0", 1)[0] == INDEX.encode()
    except OSError:
        return False

def _write(out_path, members, progress=None, finish=True):
    """members: [(name, size, open_fn)] -> tar with index.bin first. Returns the tile count (or, with
    finish=False, the finished .part file for the caller to move into place once its inputs are closed:
    Windows can't replace a file that's still open)."""
    n = len(members); entries = []
    part = Path(str(out_path) + ".part")
    with tarfile.open(part, "w", format=tarfile.GNU_FORMAT) as t:
        info = tarfile.TarInfo(INDEX); info.size = ENTRY.size * n; info.mtime = int(time.time())
        t.addfile(info, _Zeros(info.size))
        index_data = t.offset - _padded(info.size)
        for i, (name, size, open_fn) in enumerate(members):
            ti = tarfile.TarInfo(name); ti.size = size; ti.mtime = int(time.time())
            with open_fn() as src:
                t.addfile(ti, src)
            entries.append((t.offset - _padded(size), tile_id(name), size))
            if progress and (i % 500 == 0 or i == n - 1): progress(i + 1, n)
    with open(part, "r+b") as f:                       # the real index, where the placeholder is
        f.seek(index_data)
        f.write(b"".join(ENTRY.pack(*e) for e in entries))
    if not finish: return part
    os.replace(part, out_path)                         # only a complete file gets the real name
    return n

def _padded(size):
    return (size + BLOCK - 1) // BLOCK * BLOCK

class _Zeros:
    def __init__(self, n): self.n = n
    def read(self, k=-1):
        k = self.n if k < 0 else min(k, self.n); self.n -= k; return b"\0" * k

def pack(tiles_dir, out_path, progress=None):
    tiles_dir = Path(tiles_dir)
    files = sorted(tiles_dir.rglob("*.gph"))
    members = [(f.relative_to(tiles_dir).as_posix(), f.stat().st_size, (lambda f=f: open(f, "rb"))) for f in files]
    return _write(out_path, members, progress)

def add_index(path, progress=None, min_free=200 << 20):
    """Rewrite an older tar (no index.bin) with one. False when there isn't room next to it."""
    path = Path(path)
    if has_index(path): return True
    free = shutil.disk_usage(path.parent).free
    if free < path.stat().st_size + min_free:
        return False
    src = tarfile.open(path, "r:")                     # random access: reads headers, skips the data
    tiles = [m for m in src.getmembers() if m.isfile() and m.name.endswith(".gph")]
    raw = open(path, "rb")
    class Part:
        def __init__(self, m): self.m = m
        def __enter__(self): raw.seek(self.m.offset_data); return _Limited(raw, self.m.size)
        def __exit__(self, *a): pass
    try:
        part = _write(path, [(m.name, m.size, (lambda m=m: Part(m))) for m in tiles], progress, finish=False)
    finally:
        raw.close(); src.close()
    os.replace(part, path)
    return True

class _Limited:
    def __init__(self, f, n): self.f, self.n = f, n
    def read(self, k=-1):
        k = self.n if k < 0 else min(k, self.n)
        b = self.f.read(k); self.n -= len(b); return b

def check(path):
    """[(offset, id, size)] from index.bin (for tests and diagnosis)."""
    with tarfile.open(path, "r:") as t:
        m = t.next(); data = t.extractfile(m).read()
    return [ENTRY.unpack_from(data, i) for i in range(0, len(data), ENTRY.size)]

if __name__ == "__main__":
    tar = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/headunit/routing/valhalla_tiles.tar")
    status = Path(os.environ.get("HU_INDEX_STATUS", tar.parent / "index-status.json"))
    def say(phase, done=0, total=0):
        try: status.write_text(json.dumps({"phase": phase, "done": done, "total": total, "at": time.time()}))
        except OSError: pass
    if has_index(tar):
        print("already indexed"); say("done"); sys.exit(0)
    say("indexing")
    t0 = time.time()
    ok = add_index(tar, lambda d, n: say("indexing", d, n))
    if not ok:
        print("not enough free space next to", tar); say("no_space"); sys.exit(2)
    print(f"indexed {tar} in {time.time() - t0:.0f} s"); say("done")
