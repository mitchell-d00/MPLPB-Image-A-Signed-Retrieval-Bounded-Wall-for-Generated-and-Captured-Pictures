"""End to end: fake Images API → studio server → seed. No network, no key."""

import base64
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mplpb_image.corpus import catalog  # noqa: E402
from mplpb_image.validate import validate  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"  # sniffable, not decodable


class FakeImagesAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = [{"b64_json": base64.b64encode(PNG + bytes([i])).decode(), "revised_prompt": body["prompt"] + " (revised)"}
                for i in range(body.get("n", 1))]
        out = json.dumps({"id": "fake-1", "data": data, "usage": {"total_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "site"
        self.api = _serve(FakeImagesAPI)
        self.env = dict(os.environ)
        os.environ.update(
            MPLPB_IMAGE_SITE=str(self.site),
            OPENAI_BASE_URL=f"http://127.0.0.1:{self.api.server_port}",
            OPENAI_API_KEY="test-key",
            ART_STUDIO_PORT="0",
            MPLPB_IMAGE_HOME=str(Path(self.tmp.name) / "home"),
        )
        from mplpb_image import keys as K
        K._cache.clear()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def start(self, profile="studio"):
        os.environ["MPLPB_IMAGE_PROFILE"] = profile
        sys.path.insert(0, str(REPO / "studio"))
        import server
        server = importlib.reload(server)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_port
        server.ALLOWED_HOSTS.add(f"127.0.0.1:{port}")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return port

    def post(self, port, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_generate_writes_pages_and_keeps_earlier_ones(self):
        port = self.start()
        s1, d1 = self.post(port, "/api/generate", {"prompt": "celadon bowl", "n": 2, "style": "wabi-sabi"})
        s2, d2 = self.post(port, "/api/generate", {"prompt": "night market", "n": 1})
        self.assertEqual((s1, s2), (200, 200))
        pages = catalog(self.site)
        self.assertEqual(len(pages), 3)
        self.assertTrue(all(p["origin"] == "machine" and p["origin_depth"] == 1 for p in pages))
        self.assertEqual(pages[0]["scope"], "wabi-sabi")
        self.assertEqual(pages[0]["revised_prompt"], "celadon bowl (revised)")
        self.assertEqual((self.site / pages[0]["file"]).read_bytes(), PNG + b"\x00")
        self.assertEqual([f for f in validate(self.site) if not f["ok"]], [])
        self.assertTrue(all(p["kind"] == "generated" and p["sig"] for p in pages))
        self.assertIn("signer=N-", d2["images"][0]["cite"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{d2['images'][0]['src']}") as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        self.assertFalse(list(self.site.rglob("node.key")))

    def test_outputs_serves_images_only(self):
        port = self.start()
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/outputs/_index.html")

    def test_external_profile_cannot_generate(self):
        port = self.start("external")
        status, data = self.post(port, "/api/generate", {"prompt": "anything"})
        self.assertEqual(status, 403)
        self.assertEqual(catalog(self.site), [])

    def test_cross_origin_post_blocked(self):
        port = self.start()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/generate", data=b'{"prompt":"x"}',
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
