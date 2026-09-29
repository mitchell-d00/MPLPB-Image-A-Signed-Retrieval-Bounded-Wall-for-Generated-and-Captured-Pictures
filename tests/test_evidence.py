import random
import struct
import zlib

from base import Base, G
from mplpb_image import classify as C
from mplpb_image import evidence as E
from mplpb_image import pixels as P
from mplpb_image.lifecycle import attest, supersede

XMP_AI = (b'<x:xmpmeta><rdf:Description Iptc4xmpExt:DigitalSourceType='
          b'"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"/></x:xmpmeta>')


class EvidenceTests(Base):
    def test_exif_camera_parsed_as_hint_only(self):
        ev = E.read(G.jpeg_shell(exif=G.tiff_exif(make="Nikon", model="Z6")))
        self.assertEqual((ev["exif"]["make"], ev["exif"]["model"]), ("Nikon", "Z6"))
        self.assertTrue(ev["exif"]["gps"])
        sig = E.signals(ev)
        self.assertTrue(all(st == "hint" for _, to, st in sig if to == "captured"))
        self.assertFalse(E.declares_generated(ev))

    def test_jpeg_dimensions_quality_sampling(self):
        ev = E.read(G.jpeg_shell(w=640, h=480, quality_scale=0.5))
        self.assertEqual((ev["width"], ev["height"]), (640, 480))
        self.assertEqual(ev["jpeg"]["subsampling"], "2x2")
        self.assertGreater(ev["jpeg"]["quality_estimate"], 60)

    def test_iptc_generated_declared(self):
        ev = E.read(G.jpeg_shell(xmp=XMP_AI))
        self.assertEqual(ev["digital_source_type"], "trainedAlgorithmicMedia")
        self.assertTrue(E.declares_generated(ev))

    def test_png_diffusion_parameters_declared(self):
        ev = E.read(G.png(8, 8, G.smooth_scene(1, 8, 8),
                          text={"parameters": "castle\nSteps: 30, Sampler: Euler a, CFG scale: 7"}))
        self.assertTrue(ev["sd_parameters"])
        self.assertTrue(E.declares_generated(ev))

    def test_comfyui_workflow_declared(self):
        ev = E.read(G.png(8, 8, G.smooth_scene(1, 8, 8), text={"workflow": '{"nodes": []}'}))
        self.assertIn("ComfyUI", ev["generators"])

    def test_generator_software_tag(self):
        ev = E.read(G.jpeg_shell(exif=G.tiff_exif(make=None, model=None, software="Adobe Firefly", exposure=False, gps=False)))
        self.assertIn("Adobe Firefly", ev["generators"])

    def test_c2pa_png_detected_but_never_verified(self):
        box = b"jumb....c2pa....claim_generator\x78\x20OpenAI-API c2pa-rs/0.1....trainedAlgorithmicMedia"
        ev = E.read(G.png(8, 8, G.smooth_scene(1, 8, 8), extra_chunks=[(b"caBX", box)]))
        self.assertTrue(ev["c2pa"]["present"])
        self.assertTrue(ev["c2pa"]["ai_assertion"])
        self.assertFalse(ev["c2pa"]["verified"])
        self.assertIn("OpenAI", ev["generators"])

    def test_c2pa_jpeg_app11(self):
        ev = E.read(G.jpeg_shell(app11=b"JP\x00\x01jumbc2pa claim_generator\x60Camera Firmware"))
        self.assertTrue(ev["c2pa"]["present"])
        self.assertFalse(ev["c2pa"]["ai_assertion"])

    def test_webp_dimensions(self):
        vp8x = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (799).to_bytes(3, "little") + (599).to_bytes(3, "little")
        raw = b"RIFF" + struct.pack("<I", 4 + len(vp8x)) + b"WEBP" + vp8x
        ev = E.read(raw)
        self.assertEqual((ev["format"], ev["width"], ev["height"]), ("webp", 800, 600))

    def test_parser_survives_garbage(self):
        rnd = random.Random(7)
        seeds = [G.jpeg_shell(exif=G.tiff_exif(), xmp=XMP_AI),
                 G.png(8, 8, G.smooth_scene(1, 8, 8), text={"parameters": "Steps: 3"})]
        for base in seeds:
            for _ in range(300):
                b = bytearray(base)
                for _ in range(rnd.randint(1, 8)):
                    b[rnd.randrange(len(b))] = rnd.randrange(256)
                cut = bytes(b[:rnd.randint(4, len(b))])
                E.read(cut)
                P.analyse(cut)
        E.read(b"")
        E.read(b"\xff\xd8\xff")


class PixelSafetyTests(Base):
    def test_header_bomb_refused(self):
        raw = G.png(4, 4, G.smooth_scene(1, 4, 4)).replace(struct.pack(">II", 4, 4), struct.pack(">II", 60000, 60000), 1)
        self.assertIn("error", P.analyse(raw))

    def test_idat_bomb_refused_by_stdlib_decoder(self):
        saved, P._PIL = P._PIL, None
        try:
            raw = G.png(4, 4, G.smooth_scene(1, 4, 4))
            i = raw.index(b"IDAT") - 4
            (n,) = struct.unpack(">I", raw[i:i + 4])
            huge = zlib.compress(b"\x00" * 50_000_000)
            chunk = struct.pack(">I", len(huge)) + b"IDAT" + huge + b"\x00\x00\x00\x00"
            bomb = raw[:i] + chunk + raw[i + 12 + n:]
            out = P.analyse(bomb)
            self.assertIn("larger than its header", out["error"])
        finally:
            P._PIL = saved

    def test_decoders_agree(self):
        if not P.pillow_available():
            self.skipTest("Pillow not installed")
        raw = G.png(90, 60, G.noisy_scene(4, 90, 60))
        a = P.analyse(raw)["dhash"]
        saved, P._PIL = P._PIL, None
        try:
            b = P.analyse(raw)["dhash"]
        finally:
            P._PIL = saved
        self.assertEqual(a, b)  # PNG decode is lossless and both paths share one downsampler


class ClassifyTests(Base):
    def test_resized_copy_of_generation_is_matched(self):  # FM-N7 detected, not only contained
        scene = G.pattern_scene(77, 96, 96)
        rec = self.gen(raw=G.png(96, 96, scene))
        copy = G.png(48, 48, G.downscaled(scene, 96, 96, 2), filt=1)  # half size, re-encoded
        imp, v = self.found(copy)
        self.assertEqual((imp["kind"], imp["kind_basis"], imp["matched_from"]),
                         ("generated", "perceptual-match", rec["document_id"]))
        self.assertGreaterEqual(imp["origin_depth"], 1)
        self.assertValid()

    def test_flat_pictures_do_not_match_each_other(self):
        self.gen(raw=G.png(48, 48, G.smooth_scene(1, 48, 48)))
        imp, v = self.found(G.png(48, 48, G.smooth_scene(2, 48, 48)))
        self.assertEqual(imp["kind"], "unknown")
        self.assertEqual(v["matched"], [])

    def test_different_detailed_pictures_do_not_match(self):
        self.gen(raw=G.png(48, 48, G.pattern_scene(1, 48, 48)))
        imp, v = self.found(G.png(48, 48, G.pattern_scene(2, 48, 48)))
        self.assertEqual(v["matched"], [])

    def test_exact_copy_of_retired_generation_is_generated(self):
        rec = self.gen()
        raw = (self.root / rec["file"]).read_bytes()
        supersede(self.root, rec["document_id"], "gone")
        imp, _ = self.found(raw)
        self.assertEqual((imp["kind"], imp["kind_basis"]), ("generated", "exact-match"))
        self.assertValid()

    def test_exact_copy_of_attested_capture_inherits(self):
        raw = G.png(32, 32, G.noisy_scene(3, 32, 32))
        rec, _ = self.found(raw)
        attest(self.root, rec["document_id"], "M", "I photographed this wall myself.")
        supersede(self.root, rec["document_id"], "moved")
        imp, _ = self.found(raw)
        self.assertEqual((imp["kind"], imp["kind_basis"], imp["attested_by"]), ("captured", "exact-copy-of", "M"))
        self.assertValid()

    def test_edited_copy_of_capture_stays_unknown(self):
        scene = G.noisy_scene(11, 48, 48)
        rec, _ = self.found(G.png(48, 48, scene))
        attest(self.root, rec["document_id"], "M", "I photographed this wall myself.")
        edited = G.png(48, 48, lambda x, y: [min(255, v + 12) for v in scene(x, y)])
        imp, v = self.found(edited)
        self.assertEqual(imp["kind"], "unknown")
        self.assertTrue(any("not a byte-identical copy" in c for c in v["conflicts"]))

    def test_declared_ai_with_camera_exif_is_generated_and_flagged(self):
        imp, v = self.found(G.jpeg_shell(exif=G.tiff_exif(), xmp=XMP_AI))
        self.assertEqual(imp["kind"], "generated")
        self.assertTrue(v["conflicts"])

    def test_duplicate_import_refused(self):
        from mplpb_image.corpus import IngestError
        raw = G.png(16, 16, G.noisy_scene(1, 16, 16))
        self.found(raw)
        with self.assertRaises(IngestError):
            self.found(raw)
