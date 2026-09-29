import json

from base import Base, G, failures
from mplpb_image import learn as L
from mplpb_image.corpus import catalog, rebuild_manifest
from mplpb_image.lifecycle import attest

STATEMENT = "I took this photograph myself; original on my camera card."


class LearnTests(Base):
    def populate(self, n=8, captured_as_jpeg_shell=False):
        for i in range(n):
            self.gen(f"render {i}", raw=G.png(40, 40, G.smooth_scene(100 + i, 40, 40)))
        for i in range(n):
            raw = G.jpeg_shell(w=4000 + i, h=3000, exif=G.tiff_exif()) if captured_as_jpeg_shell \
                else G.png(40, 40, G.noisy_scene(200 + i, 40, 40))
            rec, _ = self.found(raw)
            attest(self.root, rec["document_id"], "Photographer", STATEMENT)

    def test_refuses_without_enough_attested_labels(self):
        self.gen()
        with self.assertRaises(L.TrainingError):
            L.train_seed(self.root)

    def test_learns_from_pixels_when_format_is_equal(self):
        self.populate()
        m = L.train_seed(self.root)
        self.assertEqual(m["counts"], {"generated": 8, "captured": 8})
        self.assertGreaterEqual(m["cv"]["content"]["balanced_accuracy"], 0.9)
        self.assertLess(m["cv"]["format_metadata"]["balanced_accuracy"], 0.8)
        self.assertFalse(any("format and metadata alone" in w for w in m["warnings"]))
        self.assertValid()

    def test_warns_when_it_only_learned_the_file_format(self):
        self.populate(captured_as_jpeg_shell=True)
        m = L.train_seed(self.root)
        self.assertTrue(any("format and metadata alone" in w for w in m["warnings"]), m["warnings"])

    def test_advice_never_changes_kind(self):
        self.populate()
        L.train_seed(self.root)
        raw = G.png(40, 40, G.noisy_scene(999, 40, 40))
        rec, _ = self.found(raw)
        rec_adv = L.advise(self.root, raw)
        self.assertEqual(rec["kind"], "unknown")
        self.assertTrue(rec_adv["advisory"])
        self.assertLess(rec_adv["p_generated"], 0.5)
        smooth = G.png(40, 40, G.smooth_scene(998, 40, 40))
        rec2, _ = self.found(smooth)
        self.assertEqual(rec2["kind"], "unknown")
        self.assertGreater(L.advise(self.root, smooth)["p_generated"], 0.5)

    def test_unattested_pages_are_never_labels(self):
        self.populate()
        for i in range(4):  # unknown imports and declared-metadata imports
            self.found(G.png(40, 40, G.noisy_scene(300 + i, 40, 40)))
        self.found(G.jpeg_shell(exif=G.tiff_exif(software="Midjourney v6")))
        m = L.train_seed(self.root)
        labelled = {t["document_id"] for t in m["training"]}
        for p in catalog(self.root):
            self.assertEqual(p["document_id"] in labelled, L.training_label(p) is not None)

    def test_forged_training_label_detected(self):
        self.populate()
        L.train_seed(self.root)
        path = self.root / "_net" / "learner.json"
        model = json.loads(path.read_text())
        unknown, _ = self.found()
        model["training"].append({"document_id": unknown["document_id"], "file_sha256": unknown["file_sha256"], "label": 0})
        model["sha256"] = L.model_hash(model)
        path.write_text(json.dumps(model))
        rebuild_manifest(self.root)
        f = failures(self.root)
        self.assertIn("not an attested label", f["I.12"])
        self.assertIn("learner signature", f["I.9"])

    def test_changed_bytes_are_skipped_not_learned(self):
        self.populate()
        victim = catalog(self.root)[0]
        (self.root / victim["file"]).write_bytes(G.png(40, 40, G.noisy_scene(1, 40, 40)))
        m = L.train_seed(self.root)
        self.assertTrue(any(victim["document_id"] in s for s in m["skipped"]))
        self.assertNotIn(victim["document_id"], {t["document_id"] for t in m["training"]})
