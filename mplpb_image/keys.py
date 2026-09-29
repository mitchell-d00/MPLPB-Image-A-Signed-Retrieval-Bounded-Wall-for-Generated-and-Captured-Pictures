"""Node keys, signatures, and the local trust store.

A seed carries public keys only (_net/signers.json). Secret keys live on the
machine, under MPLPB_IMAGE_HOME (default ~/.mplpb-image), never inside a
seed: a copied seed must not copy an identity (Networked MPLPB FM-N14).

Two different questions, answered separately:

  valid    — does the signature verify against the key the seed names?
             Anyone can check this with the seed alone.
  trusted  — is that key one this machine has chosen to trust?
             Only the local trust store answers this.

Someone with write access to a seed but not to a trusted key can re-sign
everything with a key of their own. The seed will then be valid and
untrusted, and a strict check or the external profile will refuse it.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path

from . import ed25519

KEY_FILE = "node.key"
TRUST_FILE = "trusted.json"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def node_id_for(public_hex: str) -> str:
    return "N-" + hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()[:16].upper()


def home() -> Path:
    return Path(os.environ.get("MPLPB_IMAGE_HOME") or Path.home() / ".mplpb-image")


class NodeKey:
    def __init__(self, secret: bytes):
        self.secret = secret
        self.public_hex = ed25519.public_key(secret).hex()
        self.node_id = node_id_for(self.public_hex)

    def sign(self, obj) -> str:
        return ed25519.sign(self.secret, canonical(obj)).hex()


_cache: dict = {}


def load_or_create(path: Path | None = None) -> NodeKey:
    """This machine's signing key. Created 0600 on first use."""
    d = Path(path) if path else home()
    kf = d / KEY_FILE
    cached = _cache.get(str(kf))
    if cached and kf.is_file():
        return cached
    if kf.is_file():
        secret = bytes.fromhex(kf.read_text(encoding="ascii").strip())
    else:
        d.mkdir(parents=True, exist_ok=True)
        secret = ed25519.new_secret()
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secret.hex() + "\n")
    key = NodeKey(secret)
    trust(key.public_hex, "this machine", path=d)
    _cache[str(kf)] = key
    return key


def key_file_permissions_ok(path: Path | None = None) -> bool:
    kf = (Path(path) if path else home()) / KEY_FILE
    if not kf.is_file() or os.name != "posix":
        return True
    return stat.S_IMODE(kf.stat().st_mode) & 0o077 == 0


def verify(public_hex: str, obj, sig_hex: str) -> bool:
    try:
        return ed25519.verify(bytes.fromhex(public_hex), canonical(obj), bytes.fromhex(sig_hex))
    except (ValueError, TypeError):
        return False


# --- trust store -------------------------------------------------------------

def trust_store(path: Path | None = None) -> dict:
    tf = (Path(path) if path else home()) / TRUST_FILE
    if not tf.is_file():
        return {}
    try:
        return json.loads(tf.read_text(encoding="utf-8")).get("keys") or {}
    except (ValueError, OSError):
        return {}


def trust(public_hex: str, name: str, path: Path | None = None) -> str:
    bytes.fromhex(public_hex)
    if len(public_hex) != 64:
        raise ValueError("an Ed25519 public key is 64 hex characters")
    d = Path(path) if path else home()
    d.mkdir(parents=True, exist_ok=True)
    keys = trust_store(d)
    nid = node_id_for(public_hex)
    if nid not in keys:
        keys[nid] = {"public_key": public_hex, "name": name,
                     "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (d / TRUST_FILE).write_text(json.dumps({"keys": keys}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return nid


def trusted_ids(path: Path | None = None) -> set:
    """Node IDs whose stored public key really hashes to the ID."""
    return {nid for nid, v in trust_store(path).items() if node_id_for(v.get("public_key", "00" * 32)) == nid}


def load_existing(path: Path | None = None) -> NodeKey | None:
    """This machine's key if one exists; never creates one."""
    kf = (Path(path) if path else home()) / KEY_FILE
    if not kf.is_file():
        return None
    try:
        return NodeKey(bytes.fromhex(kf.read_text(encoding="ascii").strip()))
    except ValueError:
        return None
