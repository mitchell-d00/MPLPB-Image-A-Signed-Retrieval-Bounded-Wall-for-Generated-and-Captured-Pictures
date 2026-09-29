import contextlib
import io
import json
from pathlib import Path

from base import Base, G
from mplpb_image.__main__ import main as cli


class CliTests(Base):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = cli([str(a) for a in args])
            except SystemExit as exc:
                code = exc.code
        return code, buf.getvalue()

    def file(self, name, raw):
        p = Path(self.tmp.name) / name
        p.write_bytes(raw)
        return p

    def test_full_round_trip(self):
        r = self.root
        gen = self.gen("harbour boats")
        gid = gen["document_id"]
        self.assertEqual(self.run_cli("validate", r, "--strict")[0], 0)

        code, out = self.run_cli("check", r, self.file("photo.jpg", G.jpeg_shell(exif=G.tiff_exif())))
        self.assertEqual(json.loads(out)["verdict"]["kind"], "unknown")
        self.assertEqual(len(list((r / "wall").glob("*.jpg"))), 0)  # check stores nothing

        code, out = self.run_cli("import", r, self.file("p.png", G.png(32, 32, G.noisy_scene(4, 32, 32))), "--title", "my wall")
        pid = json.loads(out)["document_id"]
        self.assertEqual(json.loads(out)["kind"], "unknown")
        self.assertEqual(self.run_cli("attest", r, pid, "--who", "M", "--statement", "short")[0], 1)
        self.assertEqual(self.run_cli("attest", r, gid, "--who", "M", "--statement", "I took this photograph.")[0], 1)
        self.assertEqual(self.run_cli("attest", r, pid, "--who", "M", "--statement", "I took this photograph.")[0], 0)
        self.assertEqual(self.run_cli("profile", "check", r, pid, "--name", "external")[0], 0)
        self.assertEqual(self.run_cli("profile", "check", r, gid, "--name", "external")[0], 3)
        self.assertEqual(self.run_cli("ratify", r, gid, "--who", "M")[0], 0)
        code, out = self.run_cli("profile", "check", r, gid, "--name", "external")
        self.assertEqual(json.loads(out)["serve_as"], "approved illustration")
        self.assertEqual(self.run_cli("learn", r)[0], 1)  # too few labels, refused cleanly
        self.assertEqual(self.run_cli("ask", r, "wall")[0], 0)
        self.assertEqual(self.run_cli("ask", r, "moon landing")[0], 4)
        self.assertEqual(self.run_cli("validate", r, "--strict")[0], 0)

    def test_key_commands(self):
        code, out = self.run_cli("key", "show")
        info = json.loads(out)
        self.assertTrue(info["permissions_ok"])
        other = "ab" * 32
        self.assertEqual(self.run_cli("key", "trust", other, "--name", "colleague")[0], 0)
        self.assertEqual(len(json.loads(self.run_cli("key", "list")[1])), 2)
        self.assertNotEqual(self.run_cli("key", "trust", other)[0], 0)  # needs a name

    def test_not_a_seed(self):
        self.assertNotEqual(self.run_cli("validate", Path(self.tmp.name) / "nope")[0], 0)
