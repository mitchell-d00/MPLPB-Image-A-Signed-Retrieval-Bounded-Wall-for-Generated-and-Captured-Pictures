"""Seed corpus for an image wall.

A picture is a page: document ID, scope, kind (generated / captured /
unknown), origin, origin depth, status, evidence, a perceptual hash, and a
relative link to the bytes. The bytes are never re-encoded. The page is
the control object. The file is the artifact.

Every record, every lineage event, the manifest fingerprint and the
learner model are signed by this machine's key (keys.py). The seed holds
public keys only, in _net/signers.json.

Every write goes through one lock and ends by re-rendering the wall and
revision log from the catalog and rebuilding and signing the manifest, so
the tree on disk is always what the signed catalog says it is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from . import classify as C
from . import evidence as E
from . import pixels as P
from .keys import NodeKey, load_or_create

WRITE_LOCK = threading.RLock()

WALL = "wall"
SUPERSEDED = "_log/superseded"
NET = "_net"
IMAGE_EXTS = ("png", "jpg", "jpeg", "webp")
ID_RE = re.compile(r"^IMG-\d{8}-[0-9A-F]{6}$")
MANIFEST_EXCLUDE = {f"{NET}/manifest.json", f"{NET}/fingerprint.json"}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
KINDS = ("generated", "captured", "unknown")
SIGNED_FIELDS = (
    "document_id", "path", "file", "file_sha256", "dhash", "status", "kind", "kind_basis",
    "origin", "origin_depth", "model", "prompt_sha256", "evidence", "updated",
    "ratified_by", "ratified_at", "attested_by", "attested_at", "retired_at", "superseded_by",
    "matched_from", "imported_from",
)


# --- small utilities -------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def corpus_root(path) -> Path:
    return Path(path).resolve()


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def dump_json(path: Path, obj) -> None:
    _atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-8"))


def esc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --- catalog and lineage -----------------------------------------------------

def catalog(root) -> list:
    data = load_json(corpus_root(root) / NET / "catalog.json", {"pages": []})
    return data.get("pages") or []


def save_catalog(root: Path, pages: list) -> None:
    dump_json(root / NET / "catalog.json", {"pages": pages})


def find(pages: list, document_id: str) -> dict | None:
    return next((p for p in pages if p.get("document_id") == document_id), None)


def lineage(root) -> list:
    return load_json(corpus_root(root) / NET / "lineage.json", {"events": []}).get("events") or []


def event_hash(event: dict) -> str:
    return sha256_bytes(json.dumps(event, sort_keys=True).encode())


def append_lineage(root: Path, event: dict, key: NodeKey | None = None) -> dict:
    key = key or load_or_create()
    register_signer(root, key)
    events = lineage(root)
    event = {k: v for k, v in dict(event).items() if v is not None}
    event.setdefault("at", now_stamp())
    event["prev"] = event_hash(events[-1]) if events else None
    event["signer"] = key.node_id
    event["sig"] = key.sign(event)
    events.append(event)
    dump_json(root / NET / "lineage.json", {"events": events})
    return event


def signers(root) -> dict:
    return load_json(corpus_root(root) / NET / "signers.json", {"keys": {}}).get("keys") or {}


class TamperError(RuntimeError):
    """The seed disagrees with itself in a way a write must not paper over."""


def register_signer(root: Path, key: NodeKey) -> None:
    keys = signers(root)
    have = keys.get(key.node_id)
    if have == key.public_hex:
        return
    if have is not None:
        raise TamperError(f"signers.json names a different public key for {key.node_id}; "
                          "refusing to overwrite it. Run validate and investigate.")
    keys[key.node_id] = key.public_hex
    dump_json(root / NET / "signers.json", {"keys": keys})


# Signed only when present, so records written before these fields existed
# keep the signatures they already have.
OPTIONAL_SIGNED_FIELDS = ("overrode_declared",)


def signed_view(rec: dict) -> dict:
    view = {k: rec.get(k) for k in SIGNED_FIELDS} | {"signer": rec.get("signer")}
    for k in OPTIONAL_SIGNED_FIELDS:
        if rec.get(k) is not None:
            view[k] = rec[k]
    return view


def sign_record(root: Path, rec: dict, key: NodeKey | None = None) -> dict:
    key = key or load_or_create()
    register_signer(root, key)
    rec["signer"] = key.node_id
    rec["sig"] = key.sign(signed_view(rec))
    return rec


def _alloc_id(pages: list) -> str:
    taken = {p.get("document_id") for p in pages}
    while True:
        did = f"IMG-{time.strftime('%Y%m%d', time.gmtime())}-{secrets.token_hex(3).upper()}"
        if did not in taken:
            return did


# --- seed ------------------------------------------------------------------

def init_seed(root, title: str = "MPLPB Image Wall", scope: str = "Pictures kept as pages, with what made them",
              key: NodeKey | None = None) -> Path:
    """Create a seed, or leave an existing one alone. Idempotent."""
    root = corpus_root(root)
    key = key or load_or_create()
    with WRITE_LOCK:
        for d in (WALL, SUPERSEDED, NET, "spec"):
            (root / d).mkdir(parents=True, exist_ok=True)
        corpus_path = root / NET / "corpus.json"
        if corpus_path.is_file():
            corpus = load_json(corpus_path, {})
        else:
            corpus = {
                "corpus_id": "C-IMAGE-" + sha256_bytes(title.encode()).upper()[:12],
                "title": title,
                "kind": "image-seed",
                "declared_scope": scope,
                "scope_terms": ["image", "generation", "wall", "prompt", "style"],
                "relation": "origin",
                "seeded_from": None,
            }
            dump_json(corpus_path, corpus)
        if not (root / NET / "vocabulary.json").is_file():
            dump_json(root / NET / "vocabulary.json", {
                "terms": corpus["scope_terms"],
                "synonyms": {"picture": ["image"], "gallery": ["wall"]},
            })
        if not (root / NET / "catalog.json").is_file():
            save_catalog(root, [])
        if not (root / NET / "lineage.json").is_file():
            append_lineage(root, {"op": "origin", "corpus_id": corpus["corpus_id"]}, key)
        if not (root / "index.html").is_file():
            write_text(root / "index.html", index_html(corpus["title"], corpus["corpus_id"]))
        if not (root / "BOOT.md").is_file():
            write_text(root / "BOOT.md", boot_md(corpus["title"], corpus["corpus_id"], root.name))
        if not (root / "spec" / "validation.html").is_file():
            write_text(root / "spec" / "validation.html", validation_html())
        refresh(root, key)
    return root


def refresh(root: Path, key: NodeKey | None = None) -> str:
    """Re-render derived pages from the catalog, then rebuild and sign the manifest."""
    pages = catalog(root)
    write_text(root / WALL / "_index.html", wall_html(pages))
    write_text(root / "_log" / "revisions.html", revisions_html(lineage(root), pages))
    return rebuild_manifest(root, key)


def rebuild_manifest(root, key: NodeKey | None = None) -> str:
    root = corpus_root(root)
    key = key or load_or_create()
    register_signer(root, key)
    files = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith(".tmp-"):
            continue
        rel = p.relative_to(root).as_posix()
        if rel in MANIFEST_EXCLUDE:
            continue
        files[rel] = sha256_file(p)
    man = {"files": files, "count": len(files)}
    dump_json(root / NET / "manifest.json", man)
    fp = manifest_fingerprint(man)
    body = {"sha256": fp, "signer": key.node_id}
    dump_json(root / NET / "fingerprint.json", body | {"sig": key.sign(body)})
    return fp


def manifest_fingerprint(man: dict) -> str:
    return sha256_bytes(json.dumps(man, sort_keys=True).encode())


# --- rendering ---------------------------------------------------------------

def _meta(name: str, value) -> str:
    return f'  <meta name="mplpb:{name}" content="{esc(value)}" />\n'


def index_html(title: str, cid: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n'
        + _meta("document-id", "IMG-INDEX-001")
        + _meta("category", "Index")
        + _meta("updated", f"{now_stamp()} v1")
        + _meta("scope", "Main index for the image seed")
        + _meta("when-to-use", "start here; browse the wall")
        + _meta("status", "current")
        + _meta("origin", "human")
        + _meta("origin-depth", 0)
        + f'  <link rel="index" href="index.html" />\n  <title>{esc(title)}</title>\n</head>\n<body>\n'
        f"  <h1>{esc(title)}</h1>\n"
        f"  <p>Corpus <code>{esc(cid)}</code>.</p>\n"
        '  <ul>\n'
        '    <li><a href="wall/_index.html">Wall</a> — current generated pictures, each with a page</li>\n'
        '    <li><a href="_log/revisions.html">Revisions</a> — ingest, retire and ratify events</li>\n'
        '    <li><a href="spec/validation.html">Validation rules</a> — the eight checks</li>\n'
        '  </ul>\n'
        "  <p>Every picture here was made by a model unless its page names who ratified it.</p>\n"
        "</body>\n</html>\n"
    )


def boot_md(title: str, cid: str, dirname: str) -> str:
    return (
        f"# {title}\n\n"
        f"Corpus {cid}.\n\n"
        "Open `index.html`. Every picture under `wall/` has a sibling page that says\n"
        "who or what made it. From the directory that contains this seed:\n\n"
        f"    python3 -m mplpb_image validate {dirname}\n"
    )


def validation_html() -> str:
    from .validate import CHECKS  # local import: validate imports corpus
    items = "".join(f"    <li><b>{c}</b> {esc(d)}</li>\n" for c, d in CHECKS)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n'
        + _meta("document-id", "IMG-SPEC-VAL-001")
        + _meta("category", "Specification")
        + _meta("updated", f"{now_stamp()} v1")
        + _meta("scope", "Structural checks for an image seed")
        + _meta("when-to-use", "reading a validate report")
        + _meta("status", "current")
        + _meta("origin", "human")
        + _meta("origin-depth", 0)
        + '  <link rel="index" href="../index.html" />\n  <title>Validation rules</title>\n</head>\n<body>\n'
        "  <h1>Image seed checks</h1>\n  <ol>\n" + items + "  </ol>\n"
        '  <p><a href="../index.html">Index</a></p>\n</body>\n</html>\n'
    )


def wall_html(pages: list) -> str:
    current = [p for p in pages if p.get("status") == "current"]
    lis = "".join(
        f'    <li><a href="{esc(os.path.relpath(p["path"], WALL))}">{esc(p["document_id"])}</a>'
        f' — {esc(label(p))} — {esc(p.get("prompt_hint") or p.get("title") or "")}</li>\n'
        for p in reversed(current)
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n'
        + _meta("document-id", "IMG-WALL-INDEX")
        + _meta("category", "Wall")
        + _meta("scope", "Current generated pictures")
        + _meta("when-to-use", "list current generated images")
        + _meta("status", "current")
        + '  <link rel="index" href="../index.html" />\n  <link rel="up" href="../index.html" />\n'
        "  <title>Wall</title>\n</head>\n<body>\n  <h1>Wall</h1>\n"
        f"  <p>{len(current)} current. Retired pictures are in the "
        '<a href="../_log/revisions.html">revision log</a>, not here.</p>\n'
        '  <ul id="entries">\n' + (lis or "    <li>(empty)</li>\n") + "  </ul>\n</body>\n</html>\n"
    )


def revisions_html(events: list, pages: list) -> str:
    rows = []
    for e in events:
        did = e.get("document_id")
        rec = find(pages, did) if did else None
        who = f" by {e['who']}" if e.get("who") else ""
        why = f": {e['reason']}" if e.get("reason") else (f": {e['change']}" if e.get("change") else "")
        if e.get("overrode_declared"):
            why += " (over the file's declaration of generation)"
        succ = f" → {e['successor']}" if e.get("successor") else ""
        ref = f'<a href="../{esc(rec["path"])}">{esc(did)}</a>' if rec else esc(did or "")
        rows.append(f"    <li>{esc(e.get('at', ''))} {esc(e.get('op', ''))} {ref}{esc(who + why + succ)}</li>\n")
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n'
        + _meta("document-id", "IMG-REVISIONS")
        + _meta("category", "Log")
        + _meta("scope", "Every write to this seed, oldest first")
        + _meta("status", "current")
        + '  <link rel="index" href="../index.html" />\n  <title>Revisions</title>\n</head>\n<body>\n'
        "  <h1>Revisions</h1>\n  <ol>\n" + "".join(rows) + "  </ol>\n</body>\n</html>\n"
    )


def label(rec: dict) -> str:
    kind, origin = rec.get("kind"), rec.get("origin")
    if kind == "generated" and origin == "ratified":
        return (f"generated; approved as an illustration by {rec.get('ratified_by')} at "
                f"{rec.get('ratified_at')}; not a photograph")
    if kind == "generated":
        who = rec.get("model") or ", ".join((rec.get("evidence") or {}).get("generators") or []) or "a generator"
        return f"generated by {who}, depth {rec.get('origin_depth')}; not a photograph"
    if kind == "captured":
        return f"captured image, attested by {rec.get('attested_by')} at {rec.get('attested_at')}"
    return "unknown origin; not verified as a photograph or as generated"


def evidence_summary(ev: dict) -> dict:
    """The part of the evidence worth keeping. Deterministic from the bytes."""
    exif = ev.get("exif") or {}
    camera = " ".join(x for x in (exif.get("make"), exif.get("model")) if x) or None
    return {
        "format": ev.get("format"),
        "width": ev.get("width"),
        "height": ev.get("height"),
        "camera": camera,
        "software": exif.get("software"),
        "digital_source_type": ev.get("digital_source_type"),
        "generators": sorted(ev.get("generators") or []),
        "c2pa": dict(ev.get("c2pa") or {}),
        "signals": [f"{s} [{to}, {st}]" for s, to, st in E.signals(ev)],
    }


def page_html(rec: dict) -> str:
    page_dir = os.path.dirname(rec["path"])
    img = os.path.relpath(rec["file"], page_dir)
    up = os.path.relpath(f"{WALL}/_index.html", page_dir)
    idx = os.path.relpath("index.html", page_dir)
    ev = rec.get("evidence") or {}
    metas = (
        _meta("document-id", rec["document_id"])
        + _meta("category", "Picture")
        + _meta("updated", rec["updated"])
        + _meta("scope", rec["scope"])
        + _meta("when-to-use", rec.get("when_to_use") or "view this picture and what made it")
        + _meta("status", rec["status"])
        + _meta("kind", rec["kind"])
        + _meta("kind-basis", rec["kind_basis"])
        + _meta("origin", rec["origin"])
        + _meta("origin-depth", rec["origin_depth"])
        + _meta("model", rec.get("model") or "")
        + _meta("prompt-sha256", rec.get("prompt_sha256") or "")
        + _meta("file-sha256", rec.get("file_sha256") or "")
        + _meta("dhash", rec.get("dhash") or "")
        + _meta("signer", rec.get("signer") or "")
        + _meta("signature", rec.get("sig") or "")
    )
    for key in ("ratified_by", "ratified_at", "attested_by", "attested_at", "retired_at",
                "retired_reason", "superseded_by", "matched_from"):
        if rec.get(key):
            metas += _meta(key.replace("_", "-"), rec[key])
    banner = ""
    if rec.get("status") == "retired":
        banner = (f'  <p class="retired">RETIRED {esc(rec.get("retired_at") or "")}: {esc(rec.get("retired_reason") or "")}'
                  + (f' — superseded by {esc(rec["superseded_by"])}' if rec.get("superseded_by") else "") + "</p>\n")
    alt = {"generated": "Generated image", "captured": "Captured image", "unknown": "Image of unknown origin"}[rec["kind"]]
    body = ""
    if rec.get("prompt"):
        body += (f'  <h2>Prompt (stored; not a description of a real scene)</h2>\n'
                 f'  <pre id="prompt">{esc(rec["prompt"])}</pre>\n')
    if rec.get("revised_prompt"):
        body += f'  <h2>Revised prompt (returned by the API)</h2>\n  <pre id="revised-prompt">{esc(rec["revised_prompt"])}</pre>\n'
    if rec.get("overrode_declared"):
        body += (f'  <h2>Attested over the file\'s own declaration</h2>\n'
                 f'  <p class="conflict">{esc(rec.get("attested_by") or "")} attested this as a capture although '
                 f'the file declares it generated:</p>\n  <ul>\n'
                 + "".join(f"    <li>{esc(s)}</li>\n" for s in rec["overrode_declared"]) + "  </ul>\n")
    if rec.get("conflicts"):
        body += "  <h2>Conflicts</h2>\n  <ul>\n" + "".join(
            f"    <li>{esc(c)}</li>\n" for c in rec["conflicts"]) + "  </ul>\n"
    if ev.get("signals"):
        body += "  <h2>Evidence in the file (unverified claims)</h2>\n  <ul>\n" + "".join(
            f"    <li>{esc(s)}</li>\n" for s in ev["signals"]) + "  </ul>\n"
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n' + metas
        + f'  <link rel="index" href="{esc(idx)}" />\n  <link rel="up" href="{esc(up)}" />\n'
        f"  <title>{esc(rec['document_id'])}</title>\n</head>\n<body>\n"
        f"  <h1>{esc(rec['document_id'])}</h1>\n" + banner
        + f"  <p><strong>{esc(label(rec).capitalize())}.</strong></p>\n"
        f"  <p>kind={esc(rec['kind'])} ({esc(rec['kind_basis'])}) origin={esc(rec['origin'])} "
        f"depth={esc(rec['origin_depth'])} status={esc(rec['status'])} signer={esc(rec.get('signer') or '')}</p>\n"
        "  <figure>\n"
        f'    <img src="{esc(img)}" alt="{alt}. {esc(rec.get("prompt_hint") or rec.get("title") or "")}" />\n'
        "  </figure>\n" + body
        + f'  <p><a href="{esc(up)}">Wall</a> · <a href="{esc(idx)}">Index</a></p>\n'
        "</body>\n</html>\n"
    )


def write_page(root: Path, rec: dict) -> None:
    write_text(root / rec["path"], page_html(rec))


# --- writing pictures in ------------------------------------------------------------

class IngestError(ValueError):
    pass


def _check_bytes(raw: bytes) -> str:
    if not raw:
        raise IngestError("no bytes")
    if len(raw) > MAX_IMAGE_BYTES:
        raise IngestError(f"over the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit")
    fmt = E.sniff(raw)
    if fmt is None:
        raise IngestError("not a PNG, JPEG or WebP; refusing to store it as a picture")
    return fmt


def _write_new(root: Path, raw: bytes, fmt: str, pages: list, rec: dict, op: dict, key: NodeKey) -> dict:
    doc_id = _alloc_id(pages)
    file_rel = f"{WALL}/{doc_id}.{fmt}"
    _atomic_write(root / file_rel, raw)
    rec.update(document_id=doc_id, path=f"{WALL}/{doc_id}.html", file=file_rel,
               file_sha256=sha256_bytes(raw), updated=f"{now_stamp()} v1", status="current")
    for k in ("ratified_by", "ratified_at", "attested_by", "attested_at", "retired_at", "superseded_by",
              "matched_from", "imported_from", "model", "prompt_sha256"):
        rec.setdefault(k, None)
    sign_record(root, rec, key)
    write_page(root, rec)
    pages.append(rec)
    save_catalog(root, pages)
    append_lineage(root, dict(op, document_id=doc_id, file_sha256=rec["file_sha256"], kind=rec["kind"]), key)
    refresh(root, key)
    return rec


def ingest_image(root, raw: bytes, ext: str, prompt: str, model: str, size: str, quality: str = "",
                 style: str = "", usage=None, api_id: str = "", revised_prompt: str = "",
                 key: NodeKey | None = None) -> dict:
    """A picture this studio generated: bytes unchanged, kind=generated, signed."""
    root = corpus_root(root)
    key = key or load_or_create()
    fmt = _check_bytes(raw)
    ev, px = E.read(raw), P.analyse(raw)
    with WRITE_LOCK:
        init_seed(root, key=key)
        pages = catalog(root)
        rec = {
            "kind": "generated", "kind_basis": "signed-ingest",
            "origin": "machine", "origin_depth": 1,
            "scope": style or "generated image",
            "when_to_use": "inspect this output; retire or ratify it",
            "model": model, "size": size, "quality": quality,
            "prompt": prompt, "prompt_hint": re.sub(r"\s+", " ", prompt).strip()[:160],
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "revised_prompt": revised_prompt or "", "usage": usage, "api_id": api_id,
            "evidence": evidence_summary(ev), "dhash": px.get("dhash"),
        }
        return _write_new(root, raw, fmt, pages, rec, {"op": "ingest"}, key)


def import_image(root, raw: bytes, title: str = "", source: str = "", advisory: dict | None = None,
                 key: NodeKey | None = None) -> tuple:
    """A picture from outside. Its kind comes from evidence only (classify.py).

    Returns (record, verdict). Refuses bytes already on the wall.
    """
    root = corpus_root(root)
    key = key or load_or_create()
    fmt = _check_bytes(raw)
    ev, px = E.read(raw), P.analyse(raw)
    sha = sha256_bytes(raw)
    with WRITE_LOCK:
        init_seed(root, key=key)
        pages = catalog(root)
        same = next((p for p in pages if p.get("file_sha256") == sha and p.get("status") == "current"), None)
        if same:
            raise IngestError(f"these exact bytes are already here as {same['document_id']}")
        v = C.verdict(pages, sha, ev, px)
        matched_id = v["basis"].split(":", 1)[1] if ":" in v["basis"] else None
        if v["kind"] == "generated":
            origin, depth = "machine", max(1, int(v["inherit_depth"] or 1))
        elif v["kind"] == "captured":
            src = find(pages, matched_id)
            origin, depth = "human", 0
        else:
            origin, depth = "unknown", 1
        rec = {
            "kind": v["kind"], "kind_basis": v["basis"].split(":")[0] if v["kind"] != "unknown" else "none",
            "origin": origin, "origin_depth": depth,
            "scope": "imported image", "title": (title or "").strip()[:160],
            "when_to_use": "an image brought in from outside; check its kind before using it",
            "evidence": evidence_summary(ev), "dhash": px.get("dhash"),
            "matched_from": matched_id,
            "imported_from": os.path.basename(source or "")[:160] or None,
            "conflicts": v["conflicts"],
            "advisory": advisory if v["kind"] == "unknown" else None,
        }
        if v["kind"] == "captured":
            rec.update(attested_by=src.get("attested_by"), attested_at=src.get("attested_at"))
        rec = _write_new(root, raw, fmt, pages, rec, {"op": "import", "basis": v["basis"]}, key)
        return rec, v
