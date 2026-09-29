"""Stdlib-only test images: PNG encoder, a minimal EXIF JPEG shell, and synthetic scenes."""

import math
import random
import struct
import zlib


def png(w, h, pixel, text=None, filt=4, extra_chunks=()):
    """Encode RGB PNG. pixel(x, y) -> (r, g, b). Uses Paeth or given filter on every row."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = bytearray()
    prev = bytearray(w * 3)
    for y in range(h):
        line = bytearray()
        for x in range(w):
            line += bytes(pixel(x, y))
        out = bytearray()
        for i in range(len(line)):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if filt == 1:
                pred = a
            elif filt == 2:
                pred = b
            elif filt == 3:
                pred = (a + b) >> 1
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            else:
                pred = 0
            out.append((line[i] - pred) & 255)
        rows += bytes([filt]) + out
        prev = line
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    for k, v in (text or {}).items():
        body += chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1"))
    for t, d in extra_chunks:
        body += chunk(t, d)
    body += chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def tiff_exif(make="Canon", model="EOS R6", software=None, exposure=True, gps=True):
    """Little-endian TIFF with IFD0 (+Exif IFD, +GPS pointer)."""
    strings, entries0 = [], []
    def ascii_entry(tag, s):
        entries0.append((tag, 2, len(s) + 1, s.encode() + b"\x00"))
    if make:
        ascii_entry(0x010F, make)
    if model:
        ascii_entry(0x0110, model)
    if software:
        ascii_entry(0x0131, software)
    n0 = len(entries0) + (1 if exposure else 0) + (1 if gps else 0)
    ifd0_off = 8
    data_off = ifd0_off + 2 + 12 * n0 + 4
    blob = bytearray()
    ents = []
    for tag, typ, cnt, val in entries0:
        if cnt <= 4:  # TIFF: values of four bytes or fewer are stored inline
            ents.append(struct.pack("<HHI", tag, typ, cnt) + val.ljust(4, b"\x00"))
        else:
            ents.append(struct.pack("<HHI", tag, typ, cnt) + struct.pack("<I", data_off + len(blob)))
            blob += val
    exif_off = data_off + len(blob)
    exif_ifd = b""
    if exposure:
        ents.append(struct.pack("<HHII", 0x8769, 4, 1, exif_off))
        exif_ifd = struct.pack("<H", 2) + struct.pack("<HHII", 0x829A, 5, 1, 0) + struct.pack("<HHII", 0x8827, 3, 1, 400) + b"\x00" * 4
    if gps:
        ents.append(struct.pack("<HHII", 0x8825, 4, 1, exif_off + len(exif_ifd)))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + struct.pack("<H", n0) + b"".join(ents) + b"\x00" * 4 + bytes(blob) + exif_ifd + struct.pack("<H", 0) + b"\x00" * 4


def jpeg_shell(w=4000, h=3000, exif=None, xmp=None, quality_scale=1.0, app11=None):
    """Header-only JPEG: parseable metadata, not decodable pixels."""
    std = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
           14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
           49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
    def seg(m, d):
        return bytes([0xFF, m]) + struct.pack(">H", len(d) + 2) + d
    out = b"\xff\xd8"
    if exif is not None:
        out += seg(0xE1, b"Exif\x00\x00" + exif)
    if xmp is not None:
        out += seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp)
    if app11 is not None:
        out += seg(0xEB, app11)
    out += seg(0xDB, b"\x00" + bytes(max(1, min(255, round(v * quality_scale))) for v in std))
    out += seg(0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return out + b"\xff\xd9"


def smooth_scene(seed, w=48, h=48):
    """Clean gradients and soft shapes — the look of a render."""
    rnd = random.Random(seed)
    cx, cy, rad = rnd.uniform(10, 38), rnd.uniform(10, 38), rnd.uniform(8, 18)
    c1 = [rnd.randrange(40, 220) for _ in range(3)]
    c2 = [rnd.randrange(40, 220) for _ in range(3)]
    def px(x, y):
        t = y / h
        base = [int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)]
        if (x - cx) ** 2 + (y - cy) ** 2 < rad ** 2:
            base = [min(255, v + 60) for v in base]
        return base
    return px


def noisy_scene(seed, w=48, h=48):
    """Sensor-like noise over texture — the look of a photograph."""
    rnd = random.Random(seed)
    f = rnd.uniform(0.2, 0.6)
    c = [rnd.randrange(60, 200) for _ in range(3)]
    def px(x, y):
        tex = 30 * math.sin(f * x) * math.cos(f * 0.7 * y)
        return [max(0, min(255, int(c[i] + tex + rnd.gauss(0, 14)))) for i in range(3)]
    return px


def pattern_scene(seed, w=64, h=64):
    """Deterministic in continuous coordinates, so a resized copy is the same picture."""
    rnd = random.Random(seed)
    waves = [(rnd.uniform(2, 9), rnd.uniform(2, 9), rnd.uniform(0, 6.3)) for _ in range(3)]
    blobs = [(rnd.random(), rnd.random(), rnd.uniform(0.08, 0.25)) for _ in range(4)]
    def px(x, y):
        u, v = x / w, y / h
        s = sum(math.sin(a * u * 6.283 + c) * math.cos(b * v * 6.283) for a, b, c in waves)
        for bx, by, r in blobs:
            if (u - bx) ** 2 + (v - by) ** 2 < r * r:
                s += 1.5
        base = int(128 + 40 * s)
        return [max(0, min(255, base + d)) for d in (0, 20, -20)]
    return px


def downscaled(px, w, h, factor):
    """Area-average px (w x h) by an integer factor, as a real resize would."""
    cache = [[px(x, y) for x in range(w)] for y in range(h)]
    def out(x, y):
        cells = [cache[y * factor + j][x * factor + i] for j in range(factor) for i in range(factor)]
        return [sum(c[k] for c in cells) // len(cells) for k in range(3)]
    return out
