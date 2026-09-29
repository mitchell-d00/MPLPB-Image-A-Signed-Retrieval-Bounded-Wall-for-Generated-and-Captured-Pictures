"""Retirement, ratification, and attestation.

Three write-backs, each signed by this machine's key and logged:

retire   Moves a current page and its bytes, unchanged, to
         _log/superseded/, off the wall. May name a current successor.

ratify   For a GENERATED picture only. A named person reviewed it and
         approves it as an illustration. Depth goes to 0; the kind stays
         generated. Ratification never makes a picture a photograph.

attest   For an UNKNOWN picture only. A named person states, in their own
         words, that it is a capture (a photograph or scan of something
         real) and puts their name to it. This is the only way a picture
         becomes `captured`. A picture the evidence says is generated
         cannot be attested: the generation evidence wins.

         One exception, taken only on request (override_declared=True):
         when the sole evidence of generation is what the file declares
         about itself (kind_basis declared-metadata, not ratified), a named
         person may attest over it. Declared metadata is typed by whoever
         last wrote the file, so it may be wrong; the cheap error must be
         correctable, or it is not cheap. The overridden signals are kept
         in the signed record and the signed log, and the page shows the
         conflict. A match to a known generation (exact or perceptual) is
         never overridable.

rescan   Re-derives every page's evidence from its bytes with the current
         evidence reader, and re-signs what changed. Used after the reader
         is corrected. A generated page whose only basis was declared
         metadata that the reader no longer finds becomes unknown, and so
         does any page whose generation came only from matching it.

Each operation writes the same who and when into the signed record and
into the signed lineage log. Validation checks one against the other.
"""

from __future__ import annotations

import os

from . import evidence as E
from .corpus import (
    SUPERSEDED,
    WRITE_LOCK,
    append_lineage,
    catalog,
    corpus_root,
    evidence_summary,
    find,
    now_stamp,
    refresh,
    save_catalog,
    sign_record,
    write_page,
)
from .keys import load_or_create
from .profiles import get_profile


class LifecycleError(ValueError):
    """The operation is not allowed on this page in its current state."""


def _commit(root, pages, rec, event, key):
    sign_record(root, rec, key)
    write_page(root, rec)
    save_catalog(root, pages)
    append_lineage(root, event, key)
    refresh(root, key)
    return rec


def supersede(root, document_id: str, reason: str, successor: str | None = None, key=None) -> dict | None:
    """Retire a current page. Returns None if the ID is not in the catalog."""
    root = corpus_root(root)
    key = key or load_or_create()
    reason = (reason or "").strip() or "retired by operator"
    with WRITE_LOCK:
        pages = catalog(root)
        rec = find(pages, document_id)
        if rec is None:
            return None
        if rec.get("status") != "current":
            raise LifecycleError(f"{document_id} is already {rec.get('status')}")
        if successor is not None:
            nxt = find(pages, successor)
            if successor == document_id or nxt is None or nxt.get("status") != "current":
                raise LifecycleError(f"successor {successor} must be a different current page")
        old_page, old_file = root / rec["path"], root / rec["file"]
        rec["path"] = f"{SUPERSEDED}/{os.path.basename(rec['path'])}"
        rec["file"] = f"{SUPERSEDED}/{os.path.basename(rec['file'])}"
        (root / SUPERSEDED).mkdir(parents=True, exist_ok=True)
        if old_file.is_file():
            os.replace(old_file, root / rec["file"])  # a move, never a re-encode
        rec.update(status="retired", retired_reason=reason, retired_at=now_stamp(), superseded_by=successor)
        if old_page.is_file():
            old_page.unlink()
        return _commit(root, pages, rec, {"op": "retire", "document_id": document_id, "reason": reason,
                                          "successor": successor, "at": rec["retired_at"]}, key)


def _named(who: str, profile: str, flag: str, verb: str) -> str:
    who = (who or "").strip()
    if not who:
        raise LifecycleError(f"{verb} must name who")
    if not get_profile(profile)[flag]:
        raise LifecycleError(f"profile {profile} does not allow {verb}")
    return who


def ratify(root, document_id: str, who: str, note: str = "", profile: str = "lab", key=None) -> dict | None:
    """Approve a generated picture as an illustration. Depth 0; still generated."""
    root = corpus_root(root)
    key = key or load_or_create()
    who = _named(who, profile, "allow_ratify", "ratification")
    with WRITE_LOCK:
        pages = catalog(root)
        rec = find(pages, document_id)
        if rec is None:
            return None
        if rec.get("status") != "current":
            raise LifecycleError(f"{document_id} is {rec.get('status')}; only current pages can be ratified")
        if rec.get("kind") != "generated":
            raise LifecycleError(f"{document_id} is {rec.get('kind')}; ratify is for generated pictures "
                                 "(use attest for a capture)")
        if rec.get("origin") != "machine":
            raise LifecycleError(f"{document_id} is already {rec.get('origin')}")
        at = now_stamp()
        rec.update(ratified_by=who, ratified_at=at, ratify_note=note, origin="ratified",
                   origin_depth=0, ratify_profile=profile)
        return _commit(root, pages, rec, {"op": "ratify", "document_id": document_id, "who": who, "at": at,
                                          "note": note, "profile": profile}, key)


def attest(root, document_id: str, who: str, statement: str, profile: str = "lab", key=None,
           override_declared: bool = False) -> dict | None:
    """A named person states this unknown picture is a real capture.

    override_declared: also allow it when the only generation evidence is
    the file's own declaration (see the module docstring).
    """
    root = corpus_root(root)
    key = key or load_or_create()
    who = _named(who, profile, "allow_attest", "attestation")
    statement = (statement or "").strip()
    if len(statement) < 10:
        raise LifecycleError("attestation needs a statement in the attester's own words "
                             "(what this is, and how they know)")
    with WRITE_LOCK:
        pages = catalog(root)
        rec = find(pages, document_id)
        if rec is None:
            return None
        if rec.get("status") != "current":
            raise LifecycleError(f"{document_id} is {rec.get('status')}; only current pages can be attested")
        overridden = []
        if rec.get("kind") == "generated":
            overridable = rec.get("kind_basis") == "declared-metadata" and rec.get("origin") == "machine"
            if not overridable:
                raise LifecycleError(f"{document_id} has generation evidence ({rec.get('kind_basis')}); "
                                     "it cannot be attested as a capture")
            if not override_declared:
                raise LifecycleError(f"{document_id} is generated only because its metadata says so; "
                                     "attesting it as a capture needs an explicit override "
                                     "(--override-declared), which is recorded")
            overridden = declared_signals(rec.get("evidence") or {})
        if rec.get("kind") == "captured":
            raise LifecycleError(f"{document_id} is already an attested capture")
        at = now_stamp()
        rec.update(kind="captured", kind_basis="attested", origin="human", origin_depth=0,
                   attested_by=who, attested_at=at, attest_statement=statement, attest_profile=profile,
                   advisory=None)
        event = {"op": "attest", "document_id": document_id, "who": who, "at": at,
                 "statement": statement, "profile": profile}
        if overridden:
            rec["overrode_declared"] = overridden
            rec["conflicts"] = list(rec.get("conflicts") or []) + [
                "attested as a capture over the file's own declaration of generation"]
            event["overrode_declared"] = overridden
        return _commit(root, pages, rec, event, key)


def declared_signals(summary: dict) -> list:
    """The declared-generation signals in a stored evidence summary."""
    return [s for s in summary.get("signals") or [] if s.endswith("[generated, declared]")]


def rescan(root, key=None) -> list:
    """Re-derive stored evidence from the bytes; demote what no longer holds.

    Returns [(document_id, what_changed)] for every page rewritten.
    """
    root = corpus_root(root)
    key = key or load_or_create()
    changed = []
    with WRITE_LOCK:
        pages = catalog(root)
        touched = {}
        for rec in pages:
            blob = root / (rec.get("file") or "")
            if not blob.is_file():
                continue
            fresh = evidence_summary(E.read(blob.read_bytes()))
            if fresh != rec.get("evidence"):
                rec["evidence"] = fresh
                touched[rec["document_id"]] = "evidence re-derived"
        demoted = set()
        while True:
            more = False
            for rec in pages:
                did = rec.get("document_id")
                if rec.get("kind") != "generated" or rec.get("origin") != "machine" or did in demoted:
                    continue
                basis = rec.get("kind_basis")
                src = find(pages, rec.get("matched_from")) if rec.get("matched_from") else None
                lost = (basis == "declared-metadata" and not declared_signals(rec.get("evidence") or {})) or (
                    basis in ("exact-match", "perceptual-match") and (src is None or src.get("kind") != "generated"))
                if lost:
                    rec.update(kind="unknown", kind_basis="none", origin="unknown",
                               origin_depth=max(1, int(rec.get("origin_depth") or 1)), matched_from=None)
                    rec["conflicts"] = list(rec.get("conflicts") or []) + [
                        f"generation basis {basis} withdrawn on rescan; now unknown"]
                    demoted.add(did)
                    touched[did] = f"generated ({basis}) -> unknown"
                    more = True
            if not more:
                break
        for rec in pages:
            did = rec.get("document_id")
            if did not in touched:
                continue
            sign_record(root, rec, key)
            write_page(root, rec)
            changed.append((did, touched[did]))
        if changed:
            save_catalog(root, pages)
            at = now_stamp()
            for did, what in changed:
                append_lineage(root, {"op": "rescan", "document_id": did, "change": what, "at": at}, key)
            refresh(root, key)
    return changed
