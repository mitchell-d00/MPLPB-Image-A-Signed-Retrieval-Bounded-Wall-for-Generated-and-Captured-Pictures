"""Deployment profiles for an image seed.

The corpus does not change between profiles; what a reader may treat a
picture as does. There are four ways a picture can be served:

  photograph             an attested capture (kind=captured, origin=human)
  approved illustration  a generated picture a named person ratified
  generated, labelled    any other generated picture; shown with its label
  unverified image       kind unknown; shown only where the profile allows

Only the first two count as serving a picture as source, and a generated
picture is never served as a photograph under any profile, ratified or
not. A retired page is never source. Where a profile requires a trusted
signer, the record's signature must verify against a key this machine
trusts, or nothing about the picture is served as source.
"""

from __future__ import annotations

PROFILES = {
    "studio": {"max_served_depth": 4, "allow_generate": True, "allow_ratify": False, "allow_attest": False,
               "show_unknown": True, "require_trusted_signer": False},
    "lab": {"max_served_depth": 2, "allow_generate": True, "allow_ratify": True, "allow_attest": True,
            "show_unknown": True, "require_trusted_signer": False},
    "internal": {"max_served_depth": 1, "allow_generate": True, "allow_ratify": True, "allow_attest": True,
                 "show_unknown": True, "require_trusted_signer": True},
    "external": {"max_served_depth": 0, "allow_generate": False, "allow_ratify": False, "allow_attest": False,
                 "show_unknown": False, "require_trusted_signer": True},
}


def get_profile(name: str) -> dict:
    if name not in PROFILES:
        raise KeyError(f"unknown profile: {name} (known: {', '.join(PROFILES)})")
    return dict(PROFILES[name], name=name)


def serve_check(profile_name: str, rec: dict, signature_valid: bool | None = None,
                signer_trusted: bool | None = None) -> dict:
    """How, if at all, would this profile serve this picture?"""
    p = get_profile(profile_name)
    reasons = []
    kind, origin, status = rec.get("kind"), rec.get("origin"), rec.get("status", "current")
    try:
        depth = int(rec.get("origin_depth"))
    except (TypeError, ValueError):
        depth = None
        reasons.append("origin depth missing or unreadable")

    if kind == "captured" and origin == "human" and rec.get("attested_by") and rec.get("attested_at"):
        serve_as = "photograph"
    elif kind == "generated" and origin == "ratified" and rec.get("ratified_by") and rec.get("ratified_at"):
        serve_as = "approved illustration"
    elif kind == "generated":
        serve_as = "generated, labelled"
        reasons.append("unratified generated picture: shown as generated, never as source")
    elif kind == "unknown":
        serve_as = "unverified image"
        reasons.append("unknown origin: not source anywhere")
    else:
        serve_as = "withheld"
        reasons.append(f"kind {kind!r} with origin {origin!r} is not a valid combination")

    if status != "current":
        reasons.append(f"page is {status}")
    if depth is not None and depth > p["max_served_depth"]:
        reasons.append(f"origin depth {depth} exceeds {profile_name} maximum of {p['max_served_depth']}")
    if signature_valid is False:
        reasons.append("record signature does not verify")
    if p["require_trusted_signer"]:
        if signature_valid is None or signer_trusted is None:
            reasons.append(f"{profile_name} requires a checked, trusted signature; none was checked")
        elif not signer_trusted:
            reasons.append("signed by a key this machine does not trust")

    as_source = not reasons
    may_show = serve_as != "withheld" and signature_valid is not False and (
        serve_as != "unverified image" or p["show_unknown"]) and (status == "current" or profile_name != "external")
    if not may_show:
        serve_as = "withheld"
    return {
        "profile": profile_name,
        "document_id": rec.get("document_id"),
        "serve_as": serve_as,
        "would_serve_as_source": as_source,
        "may_serve_as_photograph": as_source and serve_as == "photograph",
        "may_show": may_show,
        "reasons": reasons,
    }
