#!/usr/bin/env python3
"""MPLPB Image studio — a local page plus an Images API proxy.

The studio may generate. It may not relabel: every picture it receives goes
through ingest_image, which writes the bytes unchanged and a machine page
signed with this machine's key. The key is loaded from MPLPB_IMAGE_HOME
(default ~/.mplpb-image), never from the seed, and never leaves the process.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SITE = Path(os.environ.get("MPLPB_IMAGE_SITE", str(REPO / "site")))
sys.path.insert(0, str(REPO))
from mplpb_image.corpus import NET, IngestError, ingest_image, init_seed, load_json  # noqa: E402
from mplpb_image.keys import load_or_create  # noqa: E402
from mplpb_image.profiles import get_profile  # noqa: E402

NODE_KEY = load_or_create()
init_seed(SITE, key=NODE_KEY)
OUTPUTS = (SITE / "wall").resolve()
PROFILE = get_profile(os.environ.get("MPLPB_IMAGE_PROFILE", "studio"))
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

HOST = os.environ.get("ART_STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("ART_STUDIO_PORT", "8765"))
DEFAULT_API = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
SESSION_KEY = os.environ.get("OPENAI_API_KEY", "")
KEY_LOCK = threading.Lock()
MAX_BODY = 1_000_000  # 1 MB is plenty for a prompt
MAX_IMAGE_DOWNLOAD = 64 * 1024 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


LOOPBACK_ONLY = _is_loopback(HOST)
# Host headers we accept when bound to loopback. Blocks DNS-rebinding tricks
# where an attacker's domain resolves to 127.0.0.1.
ALLOWED_HOSTS = {
    f"127.0.0.1:{PORT}",
    f"localhost:{PORT}",
    f"[::1]:{PORT}",
    f"{HOST}:{PORT}",
}

# Per-model request rules. Anything not listed falls back to "gpt-image".
MODEL_RULES = {
    "gpt-image": {
        "sizes": {"1024x1024", "1536x1024", "1024x1536", "auto"},
        "default_size": "1024x1024",
        "qualities": {"low", "medium", "high", "auto"},
        "max_n": 4,
        "output_format": True,
        "background": True,
    },
    "dall-e-3": {
        "sizes": {"1024x1024", "1792x1024", "1024x1792"},
        "default_size": "1024x1024",
        "qualities": {"standard", "hd"},
        "max_n": 1,
        "output_format": False,
        "background": False,
    },
}


def rules_for(model: str) -> dict:
    return MODEL_RULES["dall-e-3"] if model.startswith("dall-e-3") else MODEL_RULES["gpt-image"]


def set_key(value: str) -> None:
    global SESSION_KEY
    with KEY_LOCK:
        SESSION_KEY = value.strip()


def get_key() -> str:
    with KEY_LOCK:
        return SESSION_KEY


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = "MPLPBImage/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"))

    # --- request guards -------------------------------------------------

    def _host_ok(self) -> bool:
        if not LOOPBACK_ONLY:
            return True  # LAN mode: user opted out, warned at startup
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # same-origin GETs, curl, etc.
        host = self.headers.get("Host") or ""
        return origin.lower() == f"http://{host}".lower()

    def _guard(self, is_post: bool) -> bool:
        if not self._host_ok():
            self._json(403, {"error": "Host not allowed."})
            return False
        if is_post:
            if not self._origin_ok():
                self._json(403, {"error": "Cross-origin request blocked."})
                return False
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                # Forces browsers into a CORS preflight, which this server never approves.
                self._json(415, {"error": "Content-Type must be application/json."})
                return False
        return True

    # --- routes ---------------------------------------------------------

    def do_GET(self) -> None:
        if not self._guard(is_post=False):
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(
                200,
                {
                    "has_key": bool(get_key()),
                    "base_url": DEFAULT_API,
                    "outputs": str(OUTPUTS),
                    "profile": PROFILE["name"],
                    "may_generate": PROFILE["allow_generate"],
                    "corpus_id": load_json(SITE / NET / "corpus.json", {}).get("corpus_id"),
                    "signer": NODE_KEY.node_id,
                },
            )
            return
        if path.startswith("/outputs/"):
            name = Path(path).name
            target = OUTPUTS / name
            ctype = IMAGE_TYPES.get(target.suffix.lower())
            if ctype is None or not target.is_file() or target.resolve().parent != OUTPUTS:
                self._json(404, {"error": "not found"})
                return
            self._send(200, target.read_bytes(), ctype)
            return
        self._json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        # Never grant CORS. Preflighted cross-origin POSTs stop here.
        self._json(405, {"error": "CORS not supported."})

    def do_POST(self) -> None:
        if not self._guard(is_post=True):
            return
        length = _to_int(self.headers.get("Content-Length"), -1)
        if length < 0 or length > MAX_BODY:
            self._json(413 if length > MAX_BODY else 411, {"error": "Bad or missing Content-Length."})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "Body must be a JSON object."})
            return

        path = self.path.split("?", 1)[0]
        if path == "/api/key":
            set_key(str(payload.get("key", "")))
            self._json(200, {"ok": True, "has_key": bool(get_key())})
            return
        if path == "/api/generate":
            self._generate(payload)
            return
        self._json(404, {"error": "not found"})

    # --- generation -----------------------------------------------------

    def _build_body(self, payload: dict) -> tuple[dict | None, str, str]:
        """Return (body, file_ext, error)."""
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return None, "", "Prompt is empty."

        model = str(payload.get("model") or "gpt-image-1")
        rules = rules_for(model)

        size = str(payload.get("size") or rules["default_size"])
        if size not in rules["sizes"]:
            return None, "", f"{model} doesn't support size {size}. Use one of: {', '.join(sorted(rules['sizes']))}."

        n = max(1, min(_to_int(payload.get("n"), 1), rules["max_n"]))
        body: dict = {"model": model, "prompt": prompt, "n": n, "size": size}

        quality = str(payload.get("quality") or "").strip()
        if quality:
            if quality not in rules["qualities"]:
                return None, "", f"{model} doesn't support quality {quality}. Use one of: {', '.join(sorted(rules['qualities']))}."
            body["quality"] = quality

        ext = "png"
        if rules["output_format"]:
            fmt = str(payload.get("output_format") or "png")
            if fmt not in ("png", "jpeg", "webp"):
                fmt = "png"
            body["output_format"] = fmt
            ext = "jpg" if fmt == "jpeg" else fmt

            background = str(payload.get("background") or "").strip()
            if background and background != "auto" and rules["background"]:
                if background not in ("transparent", "opaque"):
                    return None, "", "Background must be auto, transparent, or opaque."
                if background == "transparent" and fmt == "jpeg":
                    return None, "", "Transparent backgrounds need PNG or WebP, not JPEG."
                body["background"] = background
        else:
            # dall-e-3: ask for base64 so we don't depend on short-lived URLs.
            body["response_format"] = "b64_json"

        return body, ext, ""

    def _generate(self, payload: dict) -> None:
        if not PROFILE["allow_generate"]:
            self._json(403, {"error": f"Profile {PROFILE['name']} does not allow generation."})
            return
        key = get_key()
        if not key:
            self._json(401, {"error": "No API key. Set OPENAI_API_KEY or paste one in the UI."})
            return

        body, ext, err = self._build_body(payload)
        if body is None:
            self._json(400, {"error": err})
            return

        req = urllib.request.Request(
            DEFAULT_API.rstrip("/") + "/images/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                msg = parsed.get("error", {}).get("message") or detail
            except Exception:
                msg = detail or str(exc)
            self._json(exc.code, {"error": msg})
            return
        except Exception as exc:
            self._json(502, {"error": str(exc)})
            return

        images = []
        for item in data.get("data") or []:
            b64 = item.get("b64_json")
            url = item.get("url")
            raw_bytes = b""
            if b64:
                try:
                    raw_bytes = base64.b64decode(b64, validate=True)
                except (ValueError, TypeError) as exc:
                    images.append({"error": f"bad image data from API: {exc}"})
                    continue
            elif url:
                if not str(url).lower().startswith("https://"):
                    images.append({"error": "refusing a non-https image URL"})
                    continue
                try:
                    with urllib.request.urlopen(url, timeout=120) as img_resp:
                        raw_bytes = img_resp.read(MAX_IMAGE_DOWNLOAD + 1)
                    if len(raw_bytes) > MAX_IMAGE_DOWNLOAD:
                        images.append({"error": "image download over the size limit"})
                        continue
                except Exception as exc:
                    images.append({"error": f"download failed: {exc}"})
                    continue
            if not raw_bytes:
                images.append({"error": "the API returned an empty image"})
                continue
            try:
                rec = ingest_image(
                    SITE,
                    raw_bytes,
                    ext,
                    prompt=body["prompt"],
                    model=body["model"],
                    size=body.get("size") or "",
                    quality=body.get("quality") or "",
                    style=str(payload.get("style") or ""),
                    usage=data.get("usage"),
                    api_id=str(data.get("id") or ""),
                    revised_prompt=str(item.get("revised_prompt") or ""),
                    key=NODE_KEY,
                )
            except IngestError as exc:
                images.append({"error": f"not stored: {exc}"})
                continue
            images.append(
                {
                    "src": f"/outputs/{Path(rec['file']).name}",
                    "name": Path(rec["file"]).name,
                    "document_id": rec["document_id"],
                    "cite": f"{rec['document_id']} kind={rec['kind']} origin={rec['origin']} d{rec['origin_depth']} signer={rec['signer']}",
                    "revised_prompt": item.get("revised_prompt"),
                }
            )
        self._json(
            200,
            {
                "images": images,
                "usage": data.get("usage"),
                "id": data.get("id"),
                "model": body["model"],
            },
        )


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    shown_host = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    url = f"http://{shown_host}:{PORT}/"
    print(f"MPLPB Image studio →  {url}")
    print(f"Seed               →  {SITE}")
    print(f"Wall bytes         →  {OUTPUTS}")
    print(f"Signing key        →  {NODE_KEY.node_id}")
    print(f"Profile            →  {PROFILE['name']}" + ("" if PROFILE["allow_generate"] else " (generation disabled)"))
    if not LOOPBACK_ONLY:
        print(
            f"\n  WARNING: bound to {HOST}, not loopback. Anyone who can reach this port\n"
            "  can generate images with your API key and replace the stored key.\n",
            file=sys.stderr,
        )
    print("Ctrl+C to quit.")
    if os.environ.get("ART_STUDIO_NO_BROWSER") != "1":
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
        httpd.server_close()


if __name__ == "__main__":
    main()
