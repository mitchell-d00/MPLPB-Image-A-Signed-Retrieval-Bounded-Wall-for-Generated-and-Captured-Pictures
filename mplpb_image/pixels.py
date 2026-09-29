"""Pixels, bounded.

Two jobs: a perceptual hash (dHash, 64 bits) so a resized or re-encoded copy
of a known picture can be matched back to its page, and a handful of
content statistics for the advisory learner.

PNG is decoded with the standard library (zlib), with hard limits so a
hostile file cannot exhaust memory: a pixel-count cap from the header and a
decompressed-size cap enforced while inflating. JPEG and WebP need a real
decoder; if Pillow is installed it is used for them (and for PNG, because
it is faster). Without Pillow, JPEG and WebP get no perceptual hash and no
content features, and every result says so.

A dHash survives resizing and recompression. It does not survive cropping,
flipping, heavy edits, or a deliberate adversary. A match means "this is
very probably that picture"; no match means nothing.
"""

from __future__ import annotations

import io
import struct
import zlib

MAX_PIXELS = 40_000_000
THUMB = 128

try:  # optional
    from PIL import Image as _PIL  # type: ignore
    _PIL.MAX_IMAGE_PIXELS = MAX_PIXELS
except Exception:  # pragma: no cover - depends on environment
    _PIL = None


def pillow_available() -> bool:
    return _PIL is not None


class PixelError(ValueError):
    pass


# --- stdlib PNG ------------------------------------------------------------------

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_rgb(raw: bytes):
    """Return (width, height, rows) with rows as lists of (r, g, b) byte triples, flattened."""
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PixelError("not a PNG")
    pos, idat, plte, hdr = 8, [], None, None
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", data[:13])
        elif ctype == b"PLTE":
            plte = data
        elif ctype == b"IDAT":
            idat.append(data)
        elif ctype == b"IEND":
            break
    if hdr is None:
        raise PixelError("no IHDR")
    w, h, bd, ct, _, _, interlace = hdr
    if w == 0 or h == 0 or w * h > MAX_PIXELS:
        raise PixelError(f"refusing {w}x{h}: over the {MAX_PIXELS}-pixel limit")
    if interlace or bd not in (8, 16) or ct not in _CHANNELS or (ct == 3 and (bd != 8 or not plte)):
        raise PixelError("PNG variant not supported without Pillow (interlaced, low bit depth, or palette)")
    ch = _CHANNELS[ct]
    bpp = ch * bd // 8
    stride = w * bpp
    expected = (stride + 1) * h
    d = zlib.decompressobj()
    data = d.decompress(b"".join(idat), expected + 1)
    if len(data) > expected or d.unconsumed_tail:
        raise PixelError("image data larger than its header declares")
    if len(data) < expected:
        raise PixelError("truncated image data")
    out = bytearray(w * h * 3)
    prev = bytearray(stride)
    o = 0
    for y in range(h):
        f = data[y * (stride + 1)]
        line = bytearray(data[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise PixelError(f"bad filter type {f}")
        step = bd // 8
        for x in range(w):
            base = x * bpp
            if ct == 3:
                k = line[base] * 3
                out[o:o + 3] = plte[k:k + 3] if k + 3 <= len(plte) else b"\x00\x00\x00"
            elif ch <= 2:
                v = line[base]
                out[o:o + 3] = bytes((v, v, v))
            else:
                out[o] = line[base]
                out[o + 1] = line[base + step]
                out[o + 2] = line[base + 2 * step]
            o += 3
        prev = line
    return w, h, out


def _box(w, h, rgb, tw, th) -> list:
    """Exact area average of an RGB buffer down to tw x th, as (r, g, b) tuples.

    Both decode paths use this, so a picture hashes the same with or without
    Pillow whenever the decode itself is lossless (PNG).
    """
    xb = [(tx * w // tw, max(tx * w // tw + 1, (tx + 1) * w // tw)) for tx in range(tw)]
    mv = memoryview(bytes(rgb))
    out = []
    for ty in range(th):
        y0, y1 = ty * h // th, max(ty * h // th + 1, (ty + 1) * h // th)
        acc = [[0, 0, 0] for _ in range(tw)]
        for y in range(y0, y1):
            row = mv[y * w * 3:(y + 1) * w * 3]
            for tx, (x0, x1) in enumerate(xb):
                seg = row[x0 * 3:x1 * 3]
                a = acc[tx]
                a[0] += sum(seg[0::3]); a[1] += sum(seg[1::3]); a[2] += sum(seg[2::3])
        for tx, (x0, x1) in enumerate(xb):
            n = (x1 - x0) * (y1 - y0)
            a = acc[tx]
            out.append((a[0] // n, a[1] // n, a[2] // n))
    return out


def thumbnail(raw: bytes, size: int = THUMB):
    """(size*size RGB tuples, decoder name) or raise PixelError."""
    if _PIL is not None:
        try:
            with _PIL.open(io.BytesIO(raw)) as im:
                if im.format == "JPEG":
                    im.draft("RGB", (size * 2, size * 2))  # deterministic DCT scaling
                im = im.convert("RGB")
                return _box(im.width, im.height, im.tobytes(), size, size), "pillow"
        except _PIL.DecompressionBombError as exc:
            raise PixelError(str(exc))
        except Exception as exc:
            raise PixelError(f"undecodable: {exc}")
    w, h, rgb = _png_rgb(raw)
    return _box(w, h, rgb, size, size), "stdlib-png"


def _luma(px) -> list:
    return [(299 * r + 587 * g + 114 * b) // 1000 for r, g, b in px]


def dhash(thumb: list, size: int = THUMB) -> str:
    """64-bit difference hash from a square thumbnail, as 16 hex digits."""
    lum = _luma(thumb)
    small = []
    for ty in range(8):
        for tx in range(9):
            y0, y1 = ty * size // 8, (ty + 1) * size // 8
            x0, x1 = tx * size // 9, (tx + 1) * size // 9
            vals = [lum[y * size + x] for y in range(y0, y1, 2) for x in range(x0, x1, 2)]
            small.append(sum(vals) / len(vals))
    bits = 0
    for ty in range(8):
        for tx in range(8):
            bits = (bits << 1) | (small[ty * 9 + tx] > small[ty * 9 + tx + 1])
    return f"{bits:016x}"


def informative(h: str | None) -> bool:
    """A dHash from a near-flat picture (sky, gradient, blank) has almost no
    set bits and collides with every other flat picture. Such hashes are
    not used for matching."""
    if not h:
        return False
    ones = bin(int(h, 16)).count("1")
    return 8 <= ones <= 56


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def content_features(thumb: list, size: int = THUMB) -> dict:
    lum = _luma(thumb)
    n = size * size
    lap, grad = [], []
    for y in range(1, size - 1):
        for x in range(1, size - 1):
            c = lum[y * size + x]
            l_, r_ = lum[y * size + x - 1], lum[y * size + x + 1]
            u, d = lum[(y - 1) * size + x], lum[(y + 1) * size + x]
            lap.append(abs(4 * c - l_ - r_ - u - d))
            grad.append(abs(r_ - l_) + abs(d - u))
    mean = sum(lum) / n
    lum_std = (sum((v - mean) ** 2 for v in lum) / n) ** 0.5
    lap_mean = sum(lap) / len(lap)
    lap_std = (sum((v - lap_mean) ** 2 for v in lap) / len(lap)) ** 0.5
    grad_mean = sum(grad) / len(grad)
    clip = sum(1 for p in thumb for v in p if v <= 2 or v >= 253) / (3 * n)
    sat = sum(max(p) - min(p) for p in thumb) / (255 * n)
    uniq = len({(r >> 3, g >> 3, b >> 3) for r, g, b in thumb}) / n
    return {
        "lum_std": lum_std / 255,
        "lap_mean": lap_mean / 255,
        "lap_cv": lap_std / (lap_mean + 1e-6),
        "lap_over_grad": lap_mean / (grad_mean + 1e-6),
        "clip_frac": clip,
        "sat_mean": sat,
        "uniq_ratio": uniq,
    }


def analyse(raw: bytes) -> dict:
    """{'dhash', 'features', 'decoder'} or {'error'} — never raises."""
    try:
        thumb, decoder = thumbnail(raw)
    except (PixelError, zlib.error, struct.error, MemoryError) as exc:
        return {"dhash": None, "features": None, "decoder": None, "error": str(exc)}
    return {"dhash": dhash(thumb), "features": content_features(thumb), "decoder": decoder}
