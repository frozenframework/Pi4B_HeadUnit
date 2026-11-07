"""
Fast image builds: only the start of the Raspberry Pi OS image changes (the partition table and the FAT
boot partition the head-unit files go into), so instead of decompressing all ~6.4 GB and compressing it
again (10-15 minutes per image), this

  1. decompresses just the first few xz blocks (they hold the boot partition),
  2. lets make-image.py write the head-unit files into that piece,
  3. compresses that piece again (same block size and check), and
  4. writes a new .xz: the new blocks + the official image's other blocks copied byte for byte, with a
     new index and footer - one ordinary xz stream any tool (xz, Raspberry Pi Imager, Etcher) reads.

The official images are compressed in many blocks (xz -T), which is what makes this possible; a
single-block file returns None from layout() and make-image.py does the full rebuild instead.
xz format: https://tukaani.org/xz/xz-file-format.txt
"""
import io, struct, subprocess, zlib

MAGIC, FOOTER_MAGIC = b"\xfd7zXZ\x00", b"YZ"

def _varint(b, i):
    n = shift = 0
    while True:
        c = b[i]; i += 1
        n |= (c & 0x7F) << shift; shift += 7
        if not c & 0x80: return n, i

def _enc(n):
    out = bytearray()
    while True:
        c = n & 0x7F; n >>= 7
        out.append(c | (0x80 if n else 0))
        if not n: return bytes(out)

def _pad4(n): return (n + 3) & ~3

def layout(f):
    """(stream flags, [(offset, unpadded, uncompressed)], index offset) of a single-stream .xz, or None."""
    f.seek(0); head = f.read(12)
    if head[:6] != MAGIC: return None
    flags = head[6:8]
    f.seek(0, 2); size = f.tell()
    f.seek(size - 12); foot = f.read(12)
    if foot[10:12] != FOOTER_MAGIC or foot[8:10] != flags: return None   # (stream padding / several streams: not handled)
    idx_size = (struct.unpack("<I", foot[4:8])[0] + 1) * 4
    idx_at = size - 12 - idx_size
    f.seek(idx_at); idx = f.read(idx_size)
    if idx[0] != 0 or zlib.crc32(idx[:-4]) != struct.unpack("<I", idx[-4:])[0]: return None
    n, i = _varint(idx, 1); blocks = []; off = 12
    for _ in range(n):
        unp, i = _varint(idx, i); unc, i = _varint(idx, i)
        blocks.append((off, unp, unc)); off += _pad4(unp)
    if off != idx_at or len(blocks) < 2: return None
    return flags, blocks, idx_at

def assemble(out, flags, head_blocks_bytes, head_records, base, tail_blocks):
    """Write a complete .xz: header, the new blocks, the base file's remaining blocks, index, footer."""
    out.write(MAGIC + flags + struct.pack("<I", zlib.crc32(flags)))
    out.write(head_blocks_bytes)
    records = list(head_records)
    for off, unp, unc in tail_blocks:
        base.seek(off); left = _pad4(unp)
        while left:
            chunk = base.read(min(left, 1 << 22)); out.write(chunk); left -= len(chunk)
        records.append((unp, unc))
    idx = bytearray(b"\x00" + _enc(len(records)))
    for unp, unc in records: idx += _enc(unp) + _enc(unc)
    idx += b"\x00" * (_pad4(len(idx)) - len(idx))
    idx += struct.pack("<I", zlib.crc32(bytes(idx)))
    out.write(idx)
    back = struct.pack("<I", len(idx) // 4 - 1)
    out.write(struct.pack("<I", zlib.crc32(back + flags)) + back + flags + FOOTER_MAGIC)

CHECKS = {0: "none", 1: "crc32", 4: "crc64", 10: "sha256"}

def head_length(blocks, need):
    """How many leading blocks (and their uncompressed length) cover the first `need` bytes."""
    total = 0
    for k, (_, _, unc) in enumerate(blocks, 1):
        total += unc
        if total >= need: return k, total
    return None

def compress_head(raw_path, flags, block_size, preset="6"):
    """xz -T0 the patched piece with the base's check and block size; returns (blocks bytes, records)."""
    check = CHECKS.get(flags[1] & 0x0F)
    if not check: raise ValueError("unsupported xz check")
    xz = subprocess.run(["xz", "-T0", f"-{preset}", f"--check={check}", f"--block-size={block_size}", "-c", str(raw_path)],
                        capture_output=True, check=True).stdout
    f = io.BytesIO(xz); lay = layout(f) if xz[6:8] == flags else None
    if lay is None:                                            # one block: layout() wants >= 2; read it directly
        f.seek(len(xz) - 12); foot = f.read(12)
        idx_size = (struct.unpack("<I", foot[4:8])[0] + 1) * 4; idx = xz[len(xz) - 12 - idx_size:len(xz) - 12]
        n, i = _varint(idx, 1); recs = []
        for _ in range(n):
            unp, i = _varint(idx, i); unc, i = _varint(idx, i); recs.append((unp, unc))
        return xz[12:len(xz) - 12 - idx_size], recs
    _, blocks, idx_at = lay
    return xz[12:idx_at], [(unp, unc) for _, unp, unc in blocks]
