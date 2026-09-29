import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import imagegen as G  # noqa: E402
from mplpb_image import keys as K  # noqa: E402
from mplpb_image.corpus import catalog, ingest_image, init_seed, import_image  # noqa: E402
from mplpb_image.validate import validate  # noqa: E402


def failures(root, **kw):
    kw.setdefault("trusted", K.trusted_ids())
    return {f["code"]: f["msg"] for f in validate(root, **kw) if not f["ok"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self._env = os.environ.get("MPLPB_IMAGE_HOME")
        os.environ["MPLPB_IMAGE_HOME"] = str(self.home)
        K._cache.clear()
        self.root = Path(self.tmp.name) / "site"
        init_seed(self.root)
        self.n = 0

    def tearDown(self):
        if self._env is None:
            os.environ.pop("MPLPB_IMAGE_HOME", None)
        else:
            os.environ["MPLPB_IMAGE_HOME"] = self._env
        K._cache.clear()
        self.tmp.cleanup()

    def gen(self, prompt="a celadon bowl in late light", raw=None, **kw):
        self.n += 1
        raw = raw or G.png(32, 32, G.smooth_scene(1000 + self.n, 32, 32))
        kw.setdefault("model", "gpt-image-1")
        kw.setdefault("size", "1024x1024")
        return ingest_image(self.root, raw, kw.pop("ext", "png"), prompt=prompt, **kw)

    def found(self, raw=None, title="found picture", seed=None):
        self.n += 1
        raw = raw or G.png(32, 32, G.noisy_scene(seed or 5000 + self.n, 32, 32))
        return import_image(self.root, raw, title=title)

    def edit_catalog(self, fn):
        path = self.root / "_net" / "catalog.json"
        data = json.loads(path.read_text())
        fn(data["pages"])
        path.write_text(json.dumps(data))

    def assertValid(self):
        self.assertEqual(failures(self.root), {})
