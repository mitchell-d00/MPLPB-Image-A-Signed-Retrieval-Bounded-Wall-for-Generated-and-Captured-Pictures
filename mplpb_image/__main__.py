"""CLI for MPLPB Image."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import classify as C
from . import evidence as E
from . import keys as K
from . import learn as L
from . import pixels as P
from .cite import cite
from .corpus import IngestError, TamperError, catalog, find, import_image, init_seed, sha256_bytes
from .lifecycle import LifecycleError, attest, ratify, rescan, supersede
from .profiles import PROFILES, serve_check
from .retrieve import ask, search
from .validate import record_trust, report, validate


def _seed(root: str) -> Path:
    p = Path(root)
    if not (p / "_net" / "catalog.json").is_file():
        raise SystemExit(f"mplpb-image: {root} is not an image seed (run `init` first)")
    return p


def _read_image(path: str) -> bytes:
    f = Path(path)
    if not f.is_file():
        raise SystemExit(f"mplpb-image: no such file: {path}")
    if f.stat().st_size > 64 * 1024 * 1024:
        raise SystemExit("mplpb-image: file over 64 MB")
    return f.read_bytes()


def check_file(root: Path, raw: bytes) -> dict:
    """Everything the seed can say about a picture, without storing it."""
    ev, px = E.read(raw), P.analyse(raw)
    v = C.verdict(catalog(root), sha256_bytes(raw), ev, px)
    adv = L.advise(root, raw, ev, px) if v["kind"] == "unknown" else None
    return {"verdict": v, "advisory": adv, "format": ev.get("format"),
            "size": [ev.get("width"), ev.get("height")], "c2pa": ev.get("c2pa"),
            "parse_errors": ev.get("parse_errors")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mplpb-image", description="Pictures kept as MPLPB pages, with what made them.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a seed (leaves an existing one alone)")
    s.add_argument("root")
    s.add_argument("--title", default="MPLPB Image Wall")

    s = sub.add_parser("validate", help="run the twelve checks")
    s.add_argument("root")
    s.add_argument("--strict", action="store_true", help="also fail if any signer is not trusted on this machine")

    s = sub.add_parser("catalog", help="print the catalog")
    s.add_argument("root")

    s = sub.add_parser("cite", help="citation line for one page")
    s.add_argument("root")
    s.add_argument("document_id")

    for name, helptext in (("ask", "answer from the catalog or refuse"), ("search", "list matching current pages")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("root")
        s.add_argument("query")

    s = sub.add_parser("check", help="what the seed can say about an outside picture; stores nothing")
    s.add_argument("root")
    s.add_argument("file")

    s = sub.add_parser("import", help="bring an outside picture in; its kind comes from evidence only")
    s.add_argument("root")
    s.add_argument("file")
    s.add_argument("--title", default="")

    s = sub.add_parser("retire", help="move a page and its bytes off the wall")
    s.add_argument("root")
    s.add_argument("document_id")
    s.add_argument("--reason", default="retired by operator")
    s.add_argument("--successor", default=None)

    s = sub.add_parser("ratify", help="approve a GENERATED picture as an illustration (never a photograph)")
    s.add_argument("root")
    s.add_argument("document_id")
    s.add_argument("--who", required=True)
    s.add_argument("--note", default="")
    s.add_argument("--profile", default="lab", choices=list(PROFILES))

    s = sub.add_parser("attest", help="state that an UNKNOWN picture is a real capture, under your name")
    s.add_argument("root")
    s.add_argument("document_id")
    s.add_argument("--who", required=True)
    s.add_argument("--statement", required=True, help="what it is and how you know, in your own words")
    s.add_argument("--profile", default="lab", choices=list(PROFILES))
    s.add_argument("--override-declared", action="store_true",
                   help="attest even though the file's own metadata declares generation (recorded as a conflict; "
                        "never allowed over a match to a known generation)")

    s = sub.add_parser("rescan", help="re-derive stored evidence from the bytes after a reader fix, and re-sign")
    s.add_argument("root")

    s = sub.add_parser("learn", help="train the advisory learner on attested pages")
    s.add_argument("root")

    s = sub.add_parser("profile", help="list profiles, or check how one would serve a page")
    s.add_argument("action", choices=["list", "check"])
    s.add_argument("root", nargs="?")
    s.add_argument("document_id", nargs="?")
    s.add_argument("--name", default="external", choices=list(PROFILES))

    s = sub.add_parser("key", help="this machine's signing key and trust store")
    s.add_argument("action", choices=["show", "list", "trust"])
    s.add_argument("public_key", nargs="?")
    s.add_argument("--name", default="")

    args = p.parse_args(argv)
    out = lambda obj: print(json.dumps(obj, indent=2, default=str))  # noqa: E731

    try:
        if args.cmd == "init":
            print(init_seed(Path(args.root), title=args.title))
            return 0
        if args.cmd == "key":
            if args.action == "show":
                k = K.load_or_create()
                out({"node_id": k.node_id, "public_key": k.public_hex, "key_dir": str(K.home()),
                     "permissions_ok": K.key_file_permissions_ok()})
            elif args.action == "list":
                out(K.trust_store())
            else:
                if not args.public_key or not args.name.strip():
                    p.error("key trust needs PUBLIC_KEY and --name")
                print(K.trust(args.public_key, args.name.strip()))
            return 0
        if args.cmd == "profile" and args.action == "list":
            out(PROFILES)
            return 0
        if args.cmd == "profile":
            if not args.root or not args.document_id:
                p.error("profile check needs ROOT and DOCUMENT_ID")
            root = _seed(args.root)
            rec = find(catalog(root), args.document_id)
            if rec is None:
                print(f"{args.document_id} not found", file=sys.stderr)
                return 1
            valid, trusted = record_trust(root, rec)
            result = serve_check(args.name, rec, valid, trusted)
            out(result)
            return 0 if result["would_serve_as_source"] else 3

        root = _seed(args.root)
        if args.cmd == "validate":
            findings = validate(root, trusted=K.trusted_ids(), strict=args.strict)
            print(report(findings))
            return 0 if all(f["ok"] for f in findings) else 1
        if args.cmd == "catalog":
            out(catalog(root))
            return 0
        if args.cmd == "cite":
            line = cite(root, args.document_id)
            print(line)
            return 1 if line.endswith("not in catalog]") else 0
        if args.cmd == "ask":
            result = ask(root, args.query)
            out(result)
            return 0 if result["kind"] == "local" else 4
        if args.cmd == "search":
            out([h["document_id"] for h in search(root, args.query)])
            return 0
        if args.cmd == "check":
            out(check_file(root, _read_image(args.file)))
            return 0
        if args.cmd == "import":
            raw = _read_image(args.file)
            rec, v = import_image(root, raw, title=args.title, source=args.file, advisory=L.advise(root, raw))
            out({"document_id": rec["document_id"], "kind": rec["kind"], "basis": rec["kind_basis"],
                 "origin": rec["origin"], "depth": rec["origin_depth"], "conflicts": v["conflicts"],
                 "advisory": rec.get("advisory"), "note": v["note"]})
            return 0
        if args.cmd == "learn":
            m = L.train_seed(root)
            out({"model_sha256": m["sha256"], "counts": m["counts"], "cv": m["cv"],
                 "warnings": m["warnings"], "skipped": m["skipped"]})
            return 0
        if args.cmd == "rescan":
            changed = rescan(root)
            for did, what in changed:
                print(f"{did}  {what}")
            print(f"{len(changed)} page(s) rewritten" if changed else "nothing to change")
            return 0
        if args.cmd == "retire":
            rec = supersede(root, args.document_id, args.reason, args.successor)
            if rec is None:
                print(f"{args.document_id} not found", file=sys.stderr)
                return 1
            print(f"{rec['document_id']} retired → {rec['path']}")
            return 0
        if args.cmd in ("ratify", "attest"):
            if args.cmd == "ratify":
                rec = ratify(root, args.document_id, args.who, args.note, args.profile)
            else:
                rec = attest(root, args.document_id, args.who, args.statement, args.profile,
                             override_declared=args.override_declared)
            if rec is None:
                print(f"{args.document_id} not found", file=sys.stderr)
                return 1
            who = rec.get("ratified_by") or rec.get("attested_by")
            at = rec.get("ratified_at") or rec.get("attested_at")
            print(f"{rec['document_id']} {'ratified' if args.cmd == 'ratify' else 'attested'} by {who} at {at}; kind={rec['kind']}")
            return 0
    except (LifecycleError, IngestError, TamperError, L.TrainingError) as exc:
        print(f"mplpb-image: {exc}", file=sys.stderr)
        return 1
    return 2


def _entry() -> int:
    try:
        return main()
    except BrokenPipeError:
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(_entry())
