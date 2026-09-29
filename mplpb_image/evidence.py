"""What the bytes say about themselves.

Reads container metadata from PNG, JPEG and WebP without decoding pixels
and without changing a byte: EXIF camera fields, XMP / IPTC digital source
type, C2PA manifest presence, ICC profiles, PNG text chunks, JPEG
quantization and sampling.

Every signal is a claim made by whoever last wrote the file. Nothing here
is verified. Signals are sorted into two strengths:

  declared  — the file says outright that it is machine-generated
              (IPTC trainedAlgorithmicMedia, a C2PA AI assertion, a known
              generator's software tag, Stable Diffusion parameters).
  hint      — the file looks like one thing or the other (camera make and
              exposure, or an API-standard size with no camera data).

The classifier accepts a declared generation claim at face value, because
the cost of wrongly calling a photograph generated is caution. It never
accepts any signal here as proof of capture. See classify.py.

C2PA manifests are detected, not validated: this module does not check
their signatures. A present manifest is reported as `c2pa.present`, with
`verified: false`, always.

Generator names are looked for only in fields whose job is to name the
software that wrote the file (GENERATOR_FIELDS below): EXIF Software, Make
and Model, XMP CreatorTool, a C2PA claim generator, and the PNG text keys
generators use. Captions, comments, titles, descriptions and author fields
are free text written by people. A photograph captioned "Mi imagen del
puerto" or "the OpenAI office" is not declaring anything, and because a
declared generation blocks ordinary attestation (lifecycle.py), matching
free text would turn a caption into a lasting false label.
"""

from __future__ import annotations

import re
import struct
import zlib

MAX_SCAN = 64 * 1024 * 1024        # bytes of file examined
MAX_TEXT = 1 * 1024 * 1024         # bytes decompressed from any one text chunk
MAX_IFD_ENTRIES = 512

GENERATOR_PATTERNS = [
    (r"dall[\s·\-\.]?e", "DALL-E"),
    (r"gpt[\s\-]?image", "OpenAI gpt-image"),
    (r"\bopenai\b", "OpenAI"),
    (r"midjourney", "Midjourney"),
    (r"stable[\s\-]?diffusion", "Stable Diffusion"),
    (r"\bsdxl\b", "Stable Diffusion"),
    (r"comfyui", "ComfyUI"),
    (r"automatic1111|a1111", "AUTOMATIC1111"),
    (r"invokeai", "InvokeAI"),
    (r"adobe\s+firefly", "Adobe Firefly"),
    (r"google[\s\-]?imagen|\bimagen[\s\-]?\d", "Google Imagen"),
    (r"\bflux\.1\b|\bflux[\s\-]?1?[\s\-\.]?(?:dev|schnell|pro|kontext)\b|black[\s\-]?forest[\s\-]?labs", "FLUX"),
    (r"ideogram", "Ideogram"),
    (r"leonardo\.ai", "Leonardo"),
    (r"novelai", "NovelAI"),
    (r"bing\s+image\s+creator", "Bing Image Creator"),
    (r"\bmpl?pb[\s\-]?image\b", "MPLPB Image"),
]

# PNG text keys (lower-cased) that name the writing software or carry a
# generator's own parameters. Every other key is treated as free text and is
# never searched for generator names.
GENERATOR_FIELDS = {"software", "source", "generator", "creator tool", "creatortool",
                    "parameters", "prompt", "workflow"}

GENERATED_SOURCE_TYPES = {
    "trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia",
    "algorithmicmedia", "compositesynthetic",
}
CAPTURED_SOURCE_TYPES = {"digitalcapture", "negativefilm", "positivefilm", "print", "compositecapture"}

API_SIZES = {(1024, 1024), (1536, 1024), (1024, 1536), (1792, 1024), (1024, 1792),
             (512, 512), (256, 256), (2048, 2048)}

_STD_LUMA_Q = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
               14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
               49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


def sniff(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def _blank() -> dict:
    return {
        "format": None, "width": None, "height": None, "bit_depth": None, "color": None,
        "exif": {}, "xmp": False, "icc": False, "png_text_keys": [],
        "digital_source_type": None, "creator_tool": None,
        "c2pa": {"present": False, "verified": False, "claim_generator": None, "ai_assertion": False},
        "jpeg": {}, "generators": [], "sd_parameters": False,
        "parse_errors": [],
    }


# --- EXIF (TIFF) ---------------------------------------------------------------

_IFD0 = {0x010F: "make", 0x0110: "model", 0x0131: "software", 0x0132: "datetime", 0x013B: "artist"}
_EXIFIFD = {0x829A: "exposure_time", 0x829D: "f_number", 0x8827: "iso", 0x9003: "datetime_original",
            0x920A: "focal_length", 0xA434: "lens_model", 0xA433: "lens_make"}


def _parse_tiff(buf: bytes) -> dict:
    out: dict = {}
    if len(buf) < 8 or buf[:2] not in (b"II", b"MM"):
        return out
    e = "<" if buf[:2] == b"II" else ">"

    def ifd(off, names, want_ptrs=False):
        ptrs = {}
        if off <= 0 or off + 2 > len(buf):
            return ptrs
        (n,) = struct.unpack_from(e + "H", buf, off)
        for i in range(min(n, MAX_IFD_ENTRIES)):
            p = off + 2 + 12 * i
            if p + 12 > len(buf):
                break
            tag, typ, cnt = struct.unpack_from(e + "HHI", buf, p)
            val = buf[p + 8:p + 12]
            if want_ptrs and tag in (0x8769, 0x8825) and typ == 4:
                ptrs[tag] = struct.unpack(e + "I", val)[0]
            if tag not in names:
                continue
            name = names[tag]
            if typ == 2:  # ASCII
                if cnt <= 4:
                    s = val[:cnt]
                else:
                    (o,) = struct.unpack(e + "I", val)
                    s = buf[o:o + min(cnt, 256)] if o + 1 <= len(buf) else b""
                out[name] = s.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()[:120]
            else:
                out[name] = True  # present; value not needed
        return ptrs

    try:
        (first,) = struct.unpack_from(e + "I", buf, 4)
        ptrs = ifd(first, _IFD0, want_ptrs=True)
        if 0x8769 in ptrs:
            ifd(ptrs[0x8769], _EXIFIFD)
        if 0x8825 in ptrs:
            out["gps"] = True
    except struct.error:
        pass
    return out


# --- XMP and C2PA -----------------------------------------------------------------

_DST_URI = re.compile(rb"newscodes/digitalsourcetype/([A-Za-z]+)", re.I)
_DST_BARE = re.compile(rb"DigitalSourceType(?:>|=\")\s*([A-Za-z]+)(?:[<\"])", re.I)
_CT = re.compile(rb"CreatorTool(?:>|=\")([^<\"]{1,120})", re.I)


def _scan_xmp(xmp: bytes, ev: dict) -> None:
    ev["xmp"] = True
    m = _DST_URI.search(xmp) or _DST_BARE.search(xmp)
    if m:
        ev["digital_source_type"] = m.group(1).decode("ascii", "replace")
    m = _CT.search(xmp)
    if m:
        ev["creator_tool"] = m.group(1).decode("utf-8", "replace").strip()


def _scan_c2pa(blob: bytes, ev: dict) -> None:
    ev["c2pa"]["present"] = True
    m = re.search(rb"claim_generator[\x00-\x7f]{0,4}?([\x20-\x7e]{3,120})", blob)
    if m:
        ev["c2pa"]["claim_generator"] = m.group(1).decode("ascii", "replace")
    if re.search(rb"trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia", blob, re.I):
        ev["c2pa"]["ai_assertion"] = True


# --- containers -------------------------------------------------------------------

def _png(raw: bytes, ev: dict) -> None:
    pos = 8
    texts = []
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR" and len(data) >= 13:
            w, h, bd, ct = struct.unpack(">IIBB", data[:10])
            ev.update(width=w, height=h, bit_depth=bd, color=ct)
        elif ctype in (b"tEXt", b"zTXt", b"iTXt"):
            key, _, rest = data.partition(b"\x00")
            try:
                if ctype == b"zTXt":
                    rest = zlib.decompressobj().decompress(rest[1:], MAX_TEXT)
                elif ctype == b"iTXt":
                    flag = rest[:1]
                    rest = rest[2:].split(b"\x00", 2)[-1]
                    if flag == b"\x01":
                        rest = zlib.decompressobj().decompress(rest, MAX_TEXT)
            except zlib.error:
                ev["parse_errors"].append("bad compressed text chunk")
                continue
            k = key.decode("latin-1", "replace")[:79]
            ev["png_text_keys"].append(k)
            texts.append((k, rest[:MAX_TEXT]))
            if k.lower() == "xml:com.adobe.xmp":
                _scan_xmp(rest, ev)
        elif ctype == b"eXIf":
            ev["exif"].update(_parse_tiff(data))
        elif ctype == b"iCCP":
            ev["icc"] = True
        elif ctype == b"caBX":
            _scan_c2pa(data, ev)
        elif ctype == b"IEND":
            break
    for k, v in texts:
        low = k.lower()
        if low == "parameters" and re.search(rb"Steps:\s*\d+", v):
            ev["sd_parameters"] = True
        if low in ("prompt", "workflow") and v.lstrip()[:1] in (b"{", b"["):
            ev["sd_parameters"] = True
            ev["generators"].append("ComfyUI")
        if low in GENERATOR_FIELDS:
            _scan_generators(v[:4096].decode("latin-1", "replace"), ev)


def _jpeg(raw: bytes, ev: dict) -> None:
    pos = 2
    jumbf = []
    jq = ev["jpeg"]
    while pos + 4 <= len(raw):
        if raw[pos] != 0xFF:
            ev["parse_errors"].append("lost marker sync")
            break
        marker = raw[pos + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01 or marker == 0xFF:
            pos += 1 if marker == 0xFF else 2
            continue
        if marker == 0xD9:
            break
        (seglen,) = struct.unpack(">H", raw[pos + 2:pos + 4])
        seg = raw[pos + 4:pos + 2 + seglen]
        if marker == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            ev["exif"].update(_parse_tiff(seg[6:]))
        elif marker == 0xE1 and seg.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            _scan_xmp(seg, ev)
        elif marker == 0xE2 and seg.startswith(b"ICC_PROFILE\x00"):
            ev["icc"] = True
        elif marker == 0xEB:  # APP11: JUMBF, where C2PA lives in JPEG
            jumbf.append(seg)
        elif marker == 0xDB:
            _dqt(seg, jq)
        elif marker in (0xC0, 0xC1, 0xC2) and len(seg) >= 6:
            h, w = struct.unpack(">HH", seg[1:5])
            ev.update(width=w, height=h, bit_depth=seg[0])
            jq["progressive"] = marker == 0xC2
            ncomp = seg[5]
            ev["color"] = ncomp
            if ncomp >= 3 and len(seg) >= 6 + 3 * ncomp:
                hv = seg[7]
                jq["subsampling"] = f"{hv >> 4}x{hv & 15}"
        # 0xFE (COM) is a free-text comment: deliberately not searched.
        elif marker == 0xDA:
            break
        pos += 2 + seglen
    if jumbf:
        blob = b"".join(jumbf)
        if b"c2pa" in blob:  # a JUMBF box alone is not C2PA; its label must say so
            _scan_c2pa(blob, ev)


def _dqt(seg: bytes, jq: dict) -> None:
    i = 0
    while i < len(seg):
        pq, tq = seg[i] >> 4, seg[i] & 15
        size = 128 if pq else 64
        table = seg[i + 1:i + 1 + size]
        i += 1 + size
        if tq == 0 and len(table) == size:
            vals = list(table) if not pq else [struct.unpack(">H", table[j:j + 2])[0] for j in range(0, 128, 2)]
            # Estimate IJG quality from the luminance table (zigzag order ignored: ratio of sums).
            s = sum(vals) * 100.0 / sum(_STD_LUMA_Q)
            q = (200 - s) / 2 if s <= 100 else 5000 / s
            jq["quality_estimate"] = max(1, min(100, round(q)))


def _webp(raw: bytes, ev: dict) -> None:
    pos = 12
    while pos + 8 <= len(raw):
        ctype = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        data = raw[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
        if ctype == b"VP8X" and len(data) >= 10:
            ev["width"] = 1 + int.from_bytes(data[4:7], "little")
            ev["height"] = 1 + int.from_bytes(data[7:10], "little")
        elif ctype == b"VP8L" and len(data) >= 5 and ev["width"] is None:
            b = int.from_bytes(data[1:5], "little")
            ev["width"], ev["height"] = (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        elif ctype == b"VP8 " and len(data) >= 10 and ev["width"] is None:
            ev["width"] = struct.unpack("<H", data[6:8])[0] & 0x3FFF
            ev["height"] = struct.unpack("<H", data[8:10])[0] & 0x3FFF
        elif ctype == b"EXIF":
            ev["exif"].update(_parse_tiff(data[6:] if data.startswith(b"Exif\x00\x00") else data))
        elif ctype == b"XMP ":
            _scan_xmp(data, ev)
        elif ctype == b"ICCP":
            ev["icc"] = True
        elif ctype in (b"C2PA", b"c2pa"):
            _scan_c2pa(data, ev)


def _scan_generators(text: str, ev: dict) -> None:
    for pat, name in GENERATOR_PATTERNS:
        if re.search(pat, text, re.I) and name not in ev["generators"]:
            ev["generators"].append(name)


# --- public ---------------------------------------------------------------------

def read(raw: bytes) -> dict:
    """Parse metadata. Never raises on malformed input; records parse_errors."""
    ev = _blank()
    raw = raw[:MAX_SCAN]
    fmt = sniff(raw)
    ev["format"] = fmt
    try:
        if fmt == "png":
            _png(raw, ev)
        elif fmt == "jpg":
            _jpeg(raw, ev)
        elif fmt == "webp":
            _webp(raw, ev)
        else:
            ev["parse_errors"].append("not a PNG, JPEG or WebP")
    except (struct.error, IndexError, ValueError) as exc:
        ev["parse_errors"].append(f"truncated or malformed: {type(exc).__name__}")
    for field in ("software", "make", "model"):  # not artist: a person's name is free text
        if ev["exif"].get(field):
            _scan_generators(str(ev["exif"][field]), ev)
    for s in (ev["creator_tool"], ev["c2pa"]["claim_generator"]):
        if s:
            _scan_generators(s, ev)
    return ev


def signals(ev: dict) -> list:
    """(name, points_to, strength) triples, most decisive first."""
    out = []
    dst = (ev.get("digital_source_type") or "").lower()
    if dst in GENERATED_SOURCE_TYPES:
        out.append((f"IPTC digital source type: {ev['digital_source_type']}", "generated", "declared"))
    if ev["c2pa"]["ai_assertion"]:
        out.append(("C2PA manifest asserts AI generation (signature not verified)", "generated", "declared"))
    for g in ev.get("generators") or []:
        out.append((f"generator named in metadata: {g}", "generated", "declared"))
    if ev.get("sd_parameters"):
        out.append(("diffusion generation parameters embedded", "generated", "declared"))
    if dst in CAPTURED_SOURCE_TYPES:
        out.append((f"IPTC digital source type: {ev['digital_source_type']}", "captured", "hint"))
    exif = ev.get("exif") or {}
    if exif.get("make") or exif.get("model"):
        out.append((f"camera named in EXIF: {exif.get('make', '')} {exif.get('model', '')}".strip(), "captured", "hint"))
    if exif.get("exposure_time") or exif.get("f_number") or exif.get("iso"):
        out.append(("exposure settings in EXIF", "captured", "hint"))
    if exif.get("gps"):
        out.append(("GPS position in EXIF", "captured", "hint"))
    if ev["c2pa"]["present"] and not ev["c2pa"]["ai_assertion"]:
        out.append(("C2PA manifest present without an AI assertion (signature not verified)", "captured", "hint"))
    if (ev.get("width"), ev.get("height")) in API_SIZES and not exif:
        out.append(("image-API standard size with no camera data", "generated", "hint"))
    return out


def declares_generated(ev: dict) -> bool:
    return any(p == "generated" and s == "declared" for _, p, s in signals(ev))


def declares_generated_summary(summary: dict) -> bool:
    """Same test as declares_generated, on a stored evidence_summary."""
    return any(s.endswith("[generated, declared]") for s in summary.get("signals") or [])
