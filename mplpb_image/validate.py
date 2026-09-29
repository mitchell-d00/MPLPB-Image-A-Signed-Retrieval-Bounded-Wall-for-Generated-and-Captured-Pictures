"""Structural and provenance checks for an image seed. Exit 1 on any failure.

I.1–I.8 are structure: the tree is what the catalog says. I.9–I.12 are
provenance: the catalog is what its signers said, the evidence is what the
bytes say, no secret key is in the seed, and the learner was trained only
on attested labels.

Validity is not trust. I.9 checks signatures against the public keys the
seed itself names. Whether those keys are trusted is this machine's
decision; validate reports it, and fails on it only when asked to be strict.
"""

from __future__ import annotations

import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import evidence as E
from . import keys as K
from . import learn as L
from . import pixels as P
from .corpus import (
    ID_RE,
    IMAGE_EXTS,
    KINDS,
    MANIFEST_EXCLUDE,
    NET,
    SUPERSEDED,
    WALL,
    corpus_root,
    event_hash,
    evidence_summary,
    find,
    lineage,
    load_json,
    manifest_fingerprint,
    sha256_file,
    signed_view,
    signers,
)

CHECKS = [
    ("I.1", "index.html exists and carries a document ID"),
    ("I.2", "the wall lists every current generation and no retired one"),
    ("I.3", "every catalog entry has its page, the page carries the required metadata, and the page agrees with the catalog"),
    ("I.4", "document IDs are well-formed and unique across the catalog"),
    ("I.5", "kind, basis, origin and depth agree: generated is machine (depth >= 1) or ratified (depth 0, logged); captured is human at depth 0 by logged attestation or exact copy of one, and a capture whose file declares generation carries a logged override; unknown is never depth 0; no picture page escapes the catalog"),
    ("I.6", "the bytes each page names exist and match their recorded hash; no image sits in the tree without a page"),
    ("I.7", "every link resolves to a file inside the corpus root"),
    ("I.8", "the manifest lists exactly the files in the tree with their current hashes, and the fingerprint matches it"),
    ("I.9", "every record, lineage event, the fingerprint and the learner are signed, and each signature verifies against the key the seed names; the lineage hash chain is unbroken"),
    ("I.10", "no secret key material anywhere in the seed; the seed names public keys only"),
    ("I.11", "the evidence and perceptual hash stored for each picture are what its bytes actually yield"),
    ("I.12", "the learner, if present, was trained only on attested pages whose bytes still match"),
]

REQUIRED_META = ("document-id", "category", "updated", "scope", "status", "kind", "kind-basis",
                 "origin", "origin-depth", "signer", "signature")
MATCH_BASES = ("exact-match", "perceptual-match")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict = {}
        self.links: list = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").startswith("mplpb:"):
            self.meta[a["name"][6:]] = a.get("content") or ""
        for key in ("href", "src"):
            if a.get(key) is not None:
                self.links.append(a[key])


def _parse(path: Path) -> _Page:
    p = _Page()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    return p


def validate(root, trusted: set | None = None, strict: bool = False) -> list:
    """trusted: node IDs this machine trusts (None = not checked). strict: fail on untrusted signers."""
    root = corpus_root(root)
    findings = []

    def result(code, problems):
        findings.append({"code": code, "ok": not problems, "msg": "; ".join(problems[:8]) + (" …" if len(problems) > 8 else "")})

    pages = load_json(root / NET / "catalog.json", {"pages": []}).get("pages") or []
    html_files = sorted(p for p in root.rglob("*.html") if p.is_file())
    parsed = {p.relative_to(root).as_posix(): _parse(p) for p in html_files}

    # I.1
    idx = parsed.get("index.html")
    result("I.1", [] if idx and idx.meta.get("document-id") else ["missing index.html or its document ID"])

    # I.2
    problems = []
    wall = parsed.get(f"{WALL}/_index.html")
    if wall is None:
        problems.append("missing wall/_index.html")
    else:
        linked = {posixpath.normpath(posixpath.join(WALL, unquote(urlsplit(h).path))) for h in wall.links}
        for p in pages:
            listed = p.get("path") in linked
            if p.get("status") == "current" and not listed:
                problems.append(f"{p.get('document_id')} current but not on the wall")
            if p.get("status") != "current" and listed:
                problems.append(f"{p.get('document_id')} is {p.get('status')} but still on the wall")
    result("I.2", problems)

    # I.3
    problems = []
    for p in pages:
        did = p.get("document_id", "?")
        rel = p.get("path") or ""
        page = parsed.get(rel)
        if page is None:
            problems.append(f"{did} page missing at {rel or '(no path)'}")
            continue
        missing = [k for k in REQUIRED_META if k not in page.meta]
        if missing:
            problems.append(f"{did} missing " + ", ".join(missing))
        for key, field in (("document-id", "document_id"), ("status", "status"), ("kind", "kind"),
                           ("kind-basis", "kind_basis"), ("origin", "origin"), ("origin-depth", "origin_depth"),
                           ("signature", "sig")):
            if key in page.meta and page.meta[key] != str(p.get(field)):
                problems.append(f"{did} page says {key}={page.meta[key]}, catalog says {p.get(field)}")
        home = WALL if p.get("status") == "current" else SUPERSEDED
        if posixpath.dirname(rel) != home:
            problems.append(f"{did} is {p.get('status')} but its page is not under {home}/")
    result("I.3", problems)

    # I.4
    problems = []
    seen = set()
    for p in pages:
        did = p.get("document_id")
        if not isinstance(did, str) or not ID_RE.match(did):
            problems.append(f"malformed id {did!r}")
        if did in seen:
            problems.append(f"duplicate id {did}")
        seen.add(did)
    result("I.4", problems)

    # I.5
    problems = []
    events = lineage(root)
    logged = {(e.get("op"), e.get("document_id"), e.get("who"), e.get("at")) for e in events}
    ingested = {e.get("document_id") for e in events if e.get("op") == "ingest"}
    overrides = {(e.get("document_id"), e.get("who"), e.get("at")): e.get("overrode_declared")
                 for e in events if e.get("op") == "attest" and e.get("overrode_declared")}
    for p in pages:
        did, kind, basis, origin = p.get("document_id"), p.get("kind"), p.get("kind_basis"), p.get("origin")
        try:
            depth = int(p.get("origin_depth"))
        except (TypeError, ValueError):
            problems.append(f"{did} origin depth unreadable: {p.get('origin_depth')!r}")
            continue
        if kind not in KINDS:
            problems.append(f"{did} kind {kind!r} is not one of {', '.join(KINDS)}")
            continue
        src = find(pages, p.get("matched_from")) if p.get("matched_from") else None
        if kind == "generated":
            if origin == "machine":
                if depth < 1:
                    problems.append(f"{did} machine at depth {depth}")
            elif origin == "ratified":
                if depth != 0:
                    problems.append(f"{did} ratified but depth {depth}")
                elif ("ratify", did, p.get("ratified_by"), p.get("ratified_at")) not in logged:
                    problems.append(f"{did} ratification has no matching lineage record")
            else:
                problems.append(f"{did} generated picture with origin {origin!r} (laundering)")
            if basis == "signed-ingest" and did not in ingested:
                problems.append(f"{did} claims signed ingest but the log has no ingest for it")
            elif basis in MATCH_BASES and (not src or src.get("kind") != "generated"):
                problems.append(f"{did} matched to {p.get('matched_from')}, which is not a generated page here")
            elif basis == "exact-match" and src and src.get("file_sha256") != p.get("file_sha256"):
                problems.append(f"{did} claims an exact match whose bytes differ")
            elif basis == "declared-metadata" and not E.declares_generated_summary(p.get("evidence") or {}):
                problems.append(f"{did} claims declared generation but its evidence declares none")
            elif basis not in ("signed-ingest", "declared-metadata") + MATCH_BASES:
                problems.append(f"{did} generated with unknown basis {basis!r}")
        elif kind == "captured":
            if origin != "human" or depth != 0:
                problems.append(f"{did} captured must be human at depth 0, is {origin} d{depth}")
            declares = E.declares_generated_summary(p.get("evidence") or {})
            if basis == "attested":
                if ("attest", did, p.get("attested_by"), p.get("attested_at")) not in logged:
                    problems.append(f"{did} attestation has no matching lineage record")
                elif declares and not p.get("overrode_declared"):
                    problems.append(f"{did} is attested but its file declares generation and no override is recorded")
                elif p.get("overrode_declared") and \
                        overrides.get((did, p.get("attested_by"), p.get("attested_at"))) != p.get("overrode_declared"):
                    problems.append(f"{did} records an override of declared generation that the log does not")
            elif basis == "exact-copy-of":
                if not src or src.get("kind") != "captured" or src.get("kind_basis") != "attested" \
                        or src.get("file_sha256") != p.get("file_sha256"):
                    problems.append(f"{did} claims to copy an attested capture that does not match")
                elif declares and not src.get("overrode_declared"):
                    problems.append(f"{did} copies a capture whose file declares generation, with no override recorded")
            else:
                problems.append(f"{did} captured by {basis!r}: only attestation makes a capture")
        else:  # unknown
            if origin != "unknown" or depth < 1 or basis != "none":
                problems.append(f"{did} unknown must be origin unknown, depth >= 1, basis none")
    catalogued = {p.get("path") for p in pages}
    for rel, page in parsed.items():
        if page.meta.get("category") == "Picture" and rel not in catalogued:
            problems.append(f"{rel} is a picture page outside the catalog")
    result("I.5", problems)

    # I.6
    problems = []
    named = set()
    for p in pages:
        rel = p.get("file")
        if not rel:
            problems.append(f"{p.get('document_id')} names no bytes")
            continue
        named.add(rel)
        blob = root / rel
        if not blob.is_file():
            problems.append(f"{p.get('document_id')} bytes missing at {rel}")
        elif p.get("file_sha256") and sha256_file(blob) != p["file_sha256"]:
            problems.append(f"{p.get('document_id')} bytes changed since ingest")
        if posixpath.dirname(rel) != posixpath.dirname(p.get("path") or ""):
            problems.append(f"{p.get('document_id')} bytes and page are in different folders")
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lstrip(".").lower() in IMAGE_EXTS:
            rel = f.relative_to(root).as_posix()
            if rel not in named:
                problems.append(f"{rel} has no page")
    result("I.6", problems)

    # I.7
    problems = []
    for rel, page in parsed.items():
        for link in page.links:
            parts = urlsplit(link)
            if not parts.path and not parts.scheme and not parts.netloc:
                continue  # fragment-only or empty
            if parts.scheme or parts.netloc or parts.path.startswith("/"):
                problems.append(f"{rel} → {link} leaves the corpus")
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), unquote(parts.path)))
            if target == ".." or target.startswith("../"):
                problems.append(f"{rel} → {link} escapes the root")
            elif not (root / target).is_file():
                problems.append(f"{rel} → {link} is broken")
    result("I.7", problems)

    # I.8
    problems = []
    man = load_json(root / NET / "manifest.json", None)
    if not isinstance(man, dict):
        problems.append("missing manifest")
    else:
        listed = man.get("files") or {}
        actual = {
            f.relative_to(root).as_posix()
            for f in root.rglob("*")
            if f.is_file() and f.relative_to(root).as_posix() not in MANIFEST_EXCLUDE
        }
        for rel in sorted(actual - set(listed)):
            problems.append(f"{rel} not in manifest")
        for rel in sorted(set(listed) - actual):
            problems.append(f"{rel} in manifest but absent")
        for rel in sorted(actual & set(listed)):
            if sha256_file(root / rel) != listed[rel]:
                problems.append(f"{rel} changed since manifest")
        if man.get("count") != len(listed):
            problems.append("manifest count disagrees with its file list")
        fp = load_json(root / NET / "fingerprint.json", {}).get("sha256")
        if fp != manifest_fingerprint(man):
            problems.append("fingerprint does not match manifest")
    result("I.8", problems)

    # I.9
    problems = []
    keys = signers(root)
    used = set()
    for nid, pub in keys.items():
        if not isinstance(pub, str) or len(pub) != 64 or K.node_id_for(pub) != nid:
            problems.append(f"signers.json: {nid} does not match its public key")

    def check_sig(what, obj, signer, sig):
        used.add(signer)
        pub = keys.get(signer)
        if not signer or not sig:
            problems.append(f"{what} is unsigned")
        elif not pub:
            problems.append(f"{what} signed by {signer}, which the seed does not name")
        elif not K.verify(pub, obj, sig):
            problems.append(f"{what} signature does not verify")

    for p in pages:
        check_sig(p.get("document_id"), signed_view(p), p.get("signer"), p.get("sig"))
    prev = None
    for i, e in enumerate(events):
        if e.get("prev") != prev:
            problems.append(f"lineage event {i} breaks the hash chain")
        body = {k: v for k, v in e.items() if k != "sig"}
        check_sig(f"lineage event {i} ({e.get('op')})", body, e.get("signer"), e.get("sig"))
        prev = event_hash(e)
    fpj = load_json(root / NET / "fingerprint.json", {})
    check_sig("fingerprint", {k: v for k, v in fpj.items() if k != "sig"}, fpj.get("signer"), fpj.get("sig"))
    model = load_json(root / NET / "learner.json", None)
    if model is not None:
        check_sig("learner", {k: v for k, v in model.items() if k != "sig"}, model.get("signer"), model.get("sig"))
    untrusted = sorted(s for s in used if s and s not in trusted) if trusted is not None else []
    if strict and untrusted:
        problems.append("signed by keys this machine does not trust: " + ", ".join(untrusted))
    result("I.9", problems)
    trust_note = {"signers": sorted(s for s in used if s), "untrusted": untrusted, "trust_checked": trusted is not None}

    # I.10
    problems = []
    local = K.load_existing()
    secret_hex = local.secret.hex().encode() if local else None
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if f.name in (K.KEY_FILE, K.TRUST_FILE) or f.suffix.lower() in (".key", ".pem"):
            problems.append(f"{rel} looks like key material")
            continue
        if secret_hex and f.stat().st_size < 64 * 1024 * 1024 and secret_hex in f.read_bytes():
            problems.append(f"{rel} contains this machine's secret key")
    for rel in (f"{NET}/signers.json", f"{NET}/corpus.json"):
        data = load_json(root / rel, {})
        if "secret" in json_keys(data):
            problems.append(f"{rel} has a 'secret' field")
    result("I.10", problems)

    # I.11
    problems = []
    for p in pages:
        blob = root / (p.get("file") or "")
        if not blob.is_file():
            continue  # I.6 reports it
        raw = blob.read_bytes()
        if evidence_summary(E.read(raw)) != p.get("evidence"):
            problems.append(f"{p.get('document_id')} stored evidence differs from what its bytes say")
        dh = P.analyse(raw).get("dhash")
        if dh and p.get("dhash") and P.hamming(dh, p["dhash"]) > 2:
            problems.append(f"{p.get('document_id')} stored perceptual hash differs from its bytes")
        elif dh and not p.get("dhash"):
            problems.append(f"{p.get('document_id')} has no perceptual hash though its bytes decode")
    result("I.11", problems)

    # I.12
    problems = []
    if model is not None:
        if model.get("sha256") != L.model_hash(model):
            problems.append("learner hash does not match its contents")
        for t in model.get("training") or []:
            rec = find(pages, t.get("document_id"))
            if rec is None:
                problems.append(f"learner trained on {t.get('document_id')}, not in the catalog")
            elif L.training_label(rec) != t.get("label"):
                problems.append(f"learner label for {t.get('document_id')} is not an attested label")
            elif rec.get("file_sha256") != t.get("file_sha256"):
                problems.append(f"learner trained on different bytes for {t.get('document_id')}")
    result("I.12", problems)

    findings.append({"code": "trust", "ok": True, "msg": "", "detail": trust_note})
    return findings


def json_keys(obj) -> set:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k).lower())
            out |= json_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= json_keys(v)
    return out


def report(findings) -> str:
    lines = []
    bad = 0
    for f in findings:
        if f["code"] == "trust":
            d = f["detail"]
            if not d["trust_checked"]:
                lines.append("NOTE trust not checked")
            elif d["untrusted"]:
                lines.append("NOTE signed by untrusted keys: " + ", ".join(d["untrusted"]))
            else:
                lines.append("OK   trust  every signer is trusted here: " + ", ".join(d["signers"]))
            continue
        mark = "OK  " if f["ok"] else "FAIL"
        bad += not f["ok"]
        lines.append(f"{mark} {f['code']}" + (f"  {f['msg']}" if f["msg"] else ""))
    lines.append(f"{bad} failure(s)" if bad else "OK  no image-seed defects")
    return "\n".join(lines)


def record_trust(root, rec: dict, trusted: set | None = None) -> tuple:
    """(signature_valid, signer_trusted) for one record."""
    pub = signers(root).get(rec.get("signer") or "")
    valid = bool(pub and rec.get("sig") and K.verify(pub, signed_view(rec), rec["sig"]))
    trusted = K.trusted_ids() if trusted is None else trusted
    return valid, valid and rec.get("signer") in trusted
