"""Citation line for a picture page. Long on purpose."""

from __future__ import annotations

from pathlib import Path

from .corpus import NET, catalog, find, load_json


def cite(root, document_id: str) -> str:
    cid = load_json(Path(root) / NET / "corpus.json", {}).get("corpus_id", "?")
    p = find(catalog(root), document_id)
    if p is None:
        return f"[{cid}   {document_id}   not in catalog]"
    extra = ""
    if p.get("ratified_by"):
        extra += f"   ratified_by={p['ratified_by']} {p.get('ratified_at')}"
    if p.get("attested_by"):
        extra += f"   attested_by={p['attested_by']} {p.get('attested_at')}"
    return (
        f"[{cid}   {p['document_id']}   {p.get('path')}   {p.get('updated')}   "
        f"kind={p.get('kind')} ({p.get('kind_basis')})   origin={p.get('origin')} d{p.get('origin_depth')}   "
        f"model={p.get('model') or '-'}   status={p.get('status')}   signer={p.get('signer')}{extra}]"
    )
