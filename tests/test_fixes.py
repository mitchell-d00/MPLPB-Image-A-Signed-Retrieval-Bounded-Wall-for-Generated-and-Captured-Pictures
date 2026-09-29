"""v1.0.1: generator names only from software fields; correctable declarations; rescan; Ed25519 vectors."""

import subprocess
import sys
import unittest
from pathlib import Path

from base import Base, G, failures
from mplpb_image import ed25519
from mplpb_image import evidence as E
from mplpb_image.corpus import catalog, find, lineage
from mplpb_image.lifecycle import LifecycleError, attest, rescan

ROOT = Path(__file__).resolve().parents[1]


def scene_png(text=None, seed=7):
    return G.png(96, 96, G.pattern_scene(seed, 96, 96), text=text)


class CaptionsAreNotDeclarations(Base):
    CAPTIONS = [
        {"Description": "Mi imagen del puerto"},
        {"Comment": "Measuring luminous flux at dusk"},
        {"Description": "Photo of the OpenAI office, San Francisco"},
        {"Author": "Stable diffusion research group photo"},
        {"Title": "Midjourney? No, my own camera"},
    ]

    def test_free_text_never_declares(self):
        for text in self.CAPTIONS:
            with self.subTest(text=text):
                ev = E.read(G.png(8, 8, G.smooth_scene(1, 8, 8), text=text))
                self.assertEqual(ev["generators"], [])
                self.assertFalse(E.declares_generated(ev))

    def test_captioned_photo_imports_unknown_and_can_be_attested(self):
        rec, v = self.found(raw=scene_png({"Description": "Mi imagen del puerto"}))
        self.assertEqual(v["kind"], "unknown")
        attest(self.root, rec["document_id"], "Ana Ruiz", "I took this from the harbour wall in May.")
        self.assertValid()

    def test_jpeg_comment_and_exif_artist_are_free_text(self):
        com = G.jpeg_shell(exif=G.tiff_exif(make=None, model=None, exposure=False, gps=False))
        com = com[:2] + b"\xff\xfe" + (len(b"shot for OpenAI") + 2).to_bytes(2, "big") + b"shot for OpenAI" + com[2:]
        self.assertEqual(E.read(com)["generators"], [])

    def test_real_generator_fields_still_declare(self):
        cases = [
            G.png(8, 8, G.smooth_scene(1, 8, 8), text={"Software": "FLUX.1-dev"}),
            G.png(8, 8, G.smooth_scene(1, 8, 8), text={"Software": "NovelAI", "Source": "Stable Diffusion XL"}),
            G.png(8, 8, G.smooth_scene(1, 8, 8), text={"Software": "Google Imagen 3"}),
            G.png(8, 8, G.smooth_scene(1, 8, 8), text={"parameters": "a cat\nSteps: 20, Sampler: DPM++"}),
            G.png(8, 8, G.smooth_scene(1, 8, 8), text={"prompt": '{"3": {"class_type": "KSampler"}}'}),
            G.jpeg_shell(exif=G.tiff_exif(make=None, model=None, software="DALL-E 3", exposure=False, gps=False)),
        ]
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assertTrue(E.declares_generated(E.read(raw)))

    def test_bare_jumbf_is_not_c2pa(self):
        ev = E.read(G.jpeg_shell(app11=b"JP\x00\x01jumb some other payload"))
        self.assertFalse(ev["c2pa"]["present"])


class DeclaredGenerationIsCorrectable(Base):
    def declared(self):
        rec, v = self.found(raw=scene_png({"Software": "Stable Diffusion"}))
        self.assertEqual((v["kind"], rec["kind_basis"]), ("generated", "declared-metadata"))
        return rec

    def test_attest_refused_without_override(self):
        rec = self.declared()
        with self.assertRaisesRegex(LifecycleError, "override"):
            attest(self.root, rec["document_id"], "Sam", "I shot this; the tag came from an editor preset.")

    def test_override_is_signed_logged_shown_and_valid(self):
        rec = self.declared()
        out = attest(self.root, rec["document_id"], "Sam", "I shot this; the tag came from an editor preset.",
                     override_declared=True)
        self.assertEqual((out["kind"], out["kind_basis"]), ("captured", "attested"))
        self.assertTrue(out["overrode_declared"])
        ev = [e for e in lineage(self.root) if e.get("op") == "attest"][-1]
        self.assertEqual(ev["overrode_declared"], out["overrode_declared"])
        self.assertIn("Attested over the file", (self.root / out["path"]).read_text())
        self.assertValid()

    def test_override_stripped_from_record_fails_validation(self):
        rec = self.declared()
        attest(self.root, rec["document_id"], "Sam", "I shot this; the tag came from an editor preset.",
               override_declared=True)
        self.edit_catalog(lambda pages: pages[0].pop("overrode_declared"))
        self.assertIn("I.5", failures(self.root))

    def test_override_never_beats_a_match(self):
        scene = G.pattern_scene(11, 96, 96)
        self.gen(raw=G.png(96, 96, scene))
        rec, v = self.found(raw=G.png(48, 48, G.downscaled(scene, 96, 96, 2), filt=1))
        self.assertEqual(rec["kind_basis"], "perceptual-match")
        with self.assertRaisesRegex(LifecycleError, "cannot be attested"):
            attest(self.root, rec["document_id"], "Sam", "I am sure this is mine, honestly.", override_declared=True)

    def test_exact_copy_inherits_override(self):
        raw = scene_png({"Software": "Stable Diffusion"})
        rec, _ = self.found(raw=raw)
        attest(self.root, rec["document_id"], "Sam", "I shot this; the tag came from an editor preset.",
               override_declared=True)
        from mplpb_image.lifecycle import supersede
        supersede(self.root, rec["document_id"], "re-import test")
        copy, v = self.found(raw=raw)
        self.assertEqual((v["kind"], copy["kind_basis"]), ("captured", "exact-copy-of"))
        self.assertValid()


class RescanMigratesOldSeeds(Base):
    def test_rescan_demotes_caption_false_positive_and_its_matches(self):
        scene = G.pattern_scene(21, 96, 96)
        old_fields, old_patterns = E.GENERATOR_FIELDS, E.GENERATOR_PATTERNS

        class Everything:
            def __contains__(self, _):
                return True
        try:  # reproduce v1.0.0: every text key searched, bare "imagen" matched
            E.GENERATOR_FIELDS = Everything()
            E.GENERATOR_PATTERNS = old_patterns + [(r"\bimagen\b", "Google Imagen")]
            photo, _ = self.found(raw=G.png(96, 96, scene, text={"Description": "Mi imagen del puerto"}))
        finally:
            E.GENERATOR_FIELDS, E.GENERATOR_PATTERNS = old_fields, old_patterns
        copy, _ = self.found(raw=G.png(48, 48, G.downscaled(scene, 96, 96, 2), filt=1))
        self.assertEqual((photo["kind_basis"], copy["kind_basis"]), ("declared-metadata", "perceptual-match"))

        self.assertIn("I.11", failures(self.root))  # stored evidence no longer matches the fixed reader
        changed = dict(rescan(self.root))
        self.assertIn("unknown", changed[photo["document_id"]])
        self.assertIn("unknown", changed[copy["document_id"]])
        self.assertValid()
        pages = catalog(self.root)
        self.assertEqual({find(pages, d)["kind"] for d in changed}, {"unknown"})
        attest(self.root, photo["document_id"], "Ana Ruiz", "I took this from the harbour wall in May.")
        self.assertValid()
        self.assertEqual(rescan(self.root), [])

    def test_rescan_on_clean_seed_is_a_no_op(self):
        self.gen()
        self.found()
        self.assertEqual(rescan(self.root), [])
        self.assertValid()


class Ed25519Vectors(unittest.TestCase):
    # RFC 8032 section 7.1, tests 1-3: (secret, public, message, signature)
    VECTORS = [
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
         "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
         "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ]

    def test_rfc8032_vectors(self):
        for sk, pk, msg, sig in self.VECTORS:
            sk, pk, msg, sig = map(bytes.fromhex, (sk, pk, msg, sig))
            self.assertEqual(ed25519.public_key(sk), pk)
            self.assertEqual(ed25519.sign(sk, msg), sig)
            self.assertTrue(ed25519.verify(pk, msg, sig))
            self.assertFalse(ed25519.verify(pk, msg + b"x", sig))
            bad = bytearray(sig)
            bad[63] |= 0x10  # push s past the group order
            self.assertFalse(ed25519.verify(pk, msg, bytes(bad)))


class CliTests(Base):
    def run_cli(self, *args):
        env = {"MPLPB_IMAGE_HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        return subprocess.run([sys.executable, "-m", "mplpb_image", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True)

    def test_rescan_and_override_flags(self):
        rec, _ = self.found(raw=scene_png({"Software": "Stable Diffusion"}))
        r = self.run_cli("attest", str(self.root), rec["document_id"], "--who", "Sam",
                         "--statement", "I shot this; the tag came from a preset.")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--override-declared", r.stderr)
        r = self.run_cli("attest", str(self.root), rec["document_id"], "--who", "Sam",
                         "--statement", "I shot this; the tag came from a preset.", "--override-declared")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("rescan", str(self.root))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "nothing to change"))


if __name__ == "__main__":
    unittest.main()
