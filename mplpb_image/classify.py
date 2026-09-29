"""Real versus generated, decided by evidence and never by guess.

Trust is asymmetric on purpose:

  - Evidence that an image is GENERATED is accepted when it is strong:
    the studio signed its ingest, the bytes are identical to a known
    generation, the picture perceptually matches one, or the file itself
    declares it (IPTC source type, C2PA AI assertion, a generator's tag).
    Being wrong in this direction under-trusts a photograph. That is the
    cheap error.

  - Evidence that an image is CAPTURED is never accepted from the file.
    EXIF can be typed by anyone, and a missing generator tag means only
    that the tag is missing. An image becomes `captured` in exactly two
    ways: a named person attests it (signed, logged), or it is a
    byte-identical copy of a picture someone already attested.

    A file's own declaration of generation can be overridden only by an
    explicit, logged attestation (lifecycle.attest, override_declared).
    A byte-identical copy of such a capture inherits it; nothing else does.

Everything else is `unknown`, and unknown is served as unknown. The
learner (learn.py) may attach a score to an unknown picture. It may not
change its kind: a guess written onto a card as a fact is the laundering
this whole design exists to stop.
"""

from __future__ import annotations

from . import evidence as E
from . import pixels as P

MATCH_BITS = 8  # dHash Hamming distance treated as "the same picture"
# Perceptual matching is skipped for near-flat pictures (pixels.informative):
# their hashes collide, and a clear-sky photograph must not "match" a
# generated gradient. Exact byte matches always count.


def match(pages: list, file_sha: str, dh: str | None) -> list:
    """Known pages this picture is, closest first: (distance, record, how)."""
    out = []
    for p in pages:
        if p.get("file_sha256") == file_sha:
            out.append((0, p, "exact"))
        elif P.informative(dh) and P.informative(p.get("dhash")):
            d = P.hamming(dh, p["dhash"])
            if d <= MATCH_BITS:
                out.append((d, p, "perceptual"))
    out.sort(key=lambda t: (t[0], t[2] != "exact"))
    return out


def verdict(pages: list, file_sha: str, ev: dict, px: dict) -> dict:
    sig = E.signals(ev)
    hits = match(pages, file_sha, px.get("dhash"))
    matched = [{"document_id": r["document_id"], "how": how, "distance": d, "kind": r.get("kind"),
                "status": r.get("status")} for d, r, how in hits]
    declared = [s for s, to, st in sig if to == "generated" and st == "declared"]
    capture_hints = [s for s, to, st in sig if to == "captured"]

    kind, basis, depth_from = "unknown", "none", None
    for d, r, how in hits:
        if r.get("kind") == "generated":
            kind, basis, depth_from = "generated", f"{how}-match:{r['document_id']}", r
            break
    if kind == "unknown":
        exact_captured = next((r for d, r, how in hits if how == "exact" and r.get("kind") == "captured"), None)
        if exact_captured and (not declared or exact_captured.get("overrode_declared")):
            kind, basis = "captured", f"exact-copy-of:{exact_captured['document_id']}"
    if kind == "unknown" and declared:
        kind, basis = "generated", "declared-metadata"

    conflicts = []
    if declared and capture_hints:
        conflicts.append("file declares generation and also carries camera data")
    if any(m["kind"] == "captured" for m in matched) and kind == "generated":
        conflicts.append("matches both an attested capture and a generation")
    if any(m["how"] == "perceptual" and m["kind"] == "captured" for m in matched) and kind == "unknown":
        conflicts.append("resembles an attested capture but is not a byte-identical copy")

    return {
        "kind": kind,
        "basis": basis,
        "inherit_depth": (depth_from or {}).get("origin_depth"),
        "matched": matched,
        "declared": declared,
        "capture_hints": capture_hints,
        "conflicts": conflicts,
        "pixel_analysis": px.get("decoder") or f"unavailable: {px.get('error', 'no decoder')}",
        "note": _note(kind, basis, capture_hints),
    }


def _note(kind: str, basis: str, hints: list) -> str:
    if kind == "generated":
        return "Generated. Show it labelled as generated; never as a photograph."
    if kind == "captured":
        return "Byte-identical copy of an attested capture; the attestation carries over."
    if hints:
        return ("Unknown. The file carries camera-like metadata, which anyone can write. "
                "It becomes a capture only if a named person attests it.")
    return "Unknown. No evidence either way. Absence of a generator tag is not evidence of a camera."
