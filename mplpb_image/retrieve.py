"""Retrieval-bounded front end over an image seed.

ask() has no path that describes a picture from anywhere but a catalog page.
What it returns is the stored prompt or import title, labelled as such,
with the picture's kind; never a caption.
If nothing matches, it says so and lists what scopes exist.
"""

from __future__ import annotations

import re

from .cite import cite
from .corpus import ID_RE, catalog, find, label

_FIELDS = ("document_id", "prompt", "prompt_hint", "revised_prompt", "scope", "model", "title", "kind")


def _tokens(text: str) -> set:
    out = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if len(t) > 2:
            out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


def search(root, query: str, status: str = "current") -> list:
    terms = _tokens(query)
    if not terms:
        return []
    hits = []
    for p in catalog(root):
        if status != "any" and p.get("status") != status:
            continue
        words = _tokens(" ".join(str(p.get(k) or "") for k in _FIELDS))
        score = len(terms & words)
        if score:
            hits.append((score, p))
    hits.sort(key=lambda x: -x[0])
    return [p for _, p in hits]


def ask(root, query: str) -> dict:
    """Answer only from retrieved pages. Never invent a caption."""
    q = query.strip()
    if ID_RE.match(q.upper()):
        rec = find(catalog(root), q.upper())
        hits = [rec] if rec else []
    else:
        hits = search(root, q)
    if not hits:
        scopes = sorted({p.get("scope") or "wall" for p in catalog(root) if p.get("status") == "current"})
        return {
            "kind": "not_in_corpus",
            "text": "Not in this corpus. Nothing under this root matched, so there is nothing to cite.",
            "scopes_here": scopes,
            "hits": [],
        }
    top = hits[0]
    if top.get("prompt_hint"):
        text, text_is = top["prompt_hint"], "the stored prompt that produced this picture, not a description of a real scene"
    elif top.get("title"):
        text, text_is = top["title"], "the title given when this picture was imported, not a verified description"
    else:
        text, text_is = top["document_id"], "no stored description"
    return {
        "kind": "local",
        "text": text,
        "text_is": text_is,
        "picture_kind": top.get("kind"),
        "picture_label": label(top),
        "origin": top.get("origin"),
        "origin_depth": top.get("origin_depth"),
        "status": top.get("status"),
        "citation": cite(root, top["document_id"]),
        "page": top,
        "hits": [h["document_id"] for h in hits[:5]],
        "substrate": "local",
    }
