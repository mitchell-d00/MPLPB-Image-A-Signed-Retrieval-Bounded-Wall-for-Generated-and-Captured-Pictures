import shutil
from pathlib import Path

from base import Base, G, failures
from mplpb_image.__main__ import main as cli
from mplpb_image.cite import cite
from mplpb_image.corpus import catalog, init_seed, lineage, rebuild_manifest, write_page
from mplpb_image.lifecycle import LifecycleError, ratify, supersede
from mplpb_image.retrieve import ask, search


class SeedTests(Base):
    def test_empty_seed_validates(self):
        self.assertValid()

    def test_init_is_idempotent_and_keeps_the_catalog(self):
        a, b = self.gen("red fox"), self.gen("blue whale")
        init_seed(self.root)
        self.assertEqual([p["document_id"] for p in catalog(self.root)], [a["document_id"], b["document_id"]])
        self.assertEqual([e["op"] for e in lineage(self.root)], ["origin", "ingest", "ingest"])
        self.assertValid()

    def test_ingest_is_signed_generated_depth_one(self):
        rec = self.gen(style="wabi-sabi")
        self.assertEqual((rec["kind"], rec["kind_basis"], rec["origin"], rec["origin_depth"]),
                         ("generated", "signed-ingest", "machine", 1))
        self.assertTrue(rec["sig"] and rec["signer"].startswith("N-"))
        self.assertIn("kind=generated", cite(self.root, rec["document_id"]))
        self.assertTrue(rec["dhash"])
        self.assertValid()

    def test_bytes_are_not_reencoded(self):  # F5
        raw = G.png(16, 16, G.smooth_scene(2, 16, 16), text={"Comment": "keep me"})
        rec = self.gen(raw=raw)
        self.assertEqual((self.root / rec["file"]).read_bytes(), raw)

    def test_extension_follows_the_bytes(self):
        rec = self.gen(raw=G.jpeg_shell(), ext="png")
        self.assertTrue(rec["file"].endswith(".jpg"))
        self.assertValid()

    def test_non_image_refused(self):
        from mplpb_image.corpus import IngestError
        with self.assertRaises(IngestError):
            self.gen(raw=b"<html>not a picture</html>")

    def test_many_ingests_all_listed(self):
        ids = {self.gen(f"study {i}")["document_id"] for i in range(8)}
        wall = (self.root / "wall" / "_index.html").read_text()
        self.assertTrue(all(i in wall for i in ids))
        self.assertValid()

    def test_copy_validates_elsewhere(self):  # F2
        self.gen()
        other = Path(self.tmp.name) / "elsewhere" / "copy"
        shutil.copytree(self.root, other)
        self.assertEqual(failures(other), {})


class StructureTests(Base):
    def rebuild(self):
        rebuild_manifest(self.root)

    def test_relabelled_page_fails(self):
        rec = self.gen()
        page = self.root / rec["path"]
        page.write_text(page.read_text().replace('kind" content="generated', 'kind" content="captured'))
        self.rebuild()
        self.assertIn("I.3", failures(self.root))

    def test_anonymous_image_fails(self):
        (self.root / "wall" / "found.png").write_bytes(G.png(8, 8, G.smooth_scene(1, 8, 8)))
        self.rebuild()
        self.assertIn("has no page", failures(self.root)["I.6"])

    def test_orphan_picture_page_fails(self):
        rec = self.gen()
        shutil.copy(self.root / rec["path"], self.root / "wall" / "IMG-20260101-ABCDEF.html")
        self.rebuild()
        self.assertIn("outside the catalog", failures(self.root)["I.5"])

    def test_tampered_bytes_fail(self):
        rec = self.gen()
        (self.root / rec["file"]).write_bytes(G.png(32, 32, G.noisy_scene(9, 32, 32)))
        self.rebuild()
        f = failures(self.root)
        self.assertIn("changed since ingest", f["I.6"])
        self.assertIn("I.11", f)

    def test_unmanifested_file_fails(self):
        (self.root / "notes.txt").write_text("x")
        self.assertIn("not in manifest", failures(self.root)["I.8"])

    def test_link_outside_root_fails(self):
        idx = self.root / "index.html"
        idx.write_text(idx.read_text().replace("wall/_index.html", "../../elsewhere.html"))
        self.rebuild()
        self.assertIn("escapes the root", failures(self.root)["I.7"])


class AskTests(Base):
    def test_ask_refuses_when_absent(self):  # F3
        self.gen("cracked celadon bowl")
        out = ask(self.root, "photograph of the moon landing")
        self.assertEqual(out["kind"], "not_in_corpus")
        self.assertNotIn("citation", out)

    def test_ask_hits_prompt_and_labels_kind(self):
        rec = self.gen("cracked celadon bowl")
        out = ask(self.root, "celadon bowls")
        self.assertEqual(out["page"]["document_id"], rec["document_id"])
        self.assertEqual(out["picture_kind"], "generated")
        self.assertIn("not a photograph", out["picture_label"])

    def test_ask_import_title_is_labelled_unverified(self):
        rec, _ = self.found(title="harbour at dusk")
        out = ask(self.root, "harbour")
        self.assertEqual(out["picture_kind"], "unknown")
        self.assertIn("not a verified description", out["text_is"])

    def test_ask_matches_words_not_substrings(self):
        self.gen("a party in the square")
        self.assertEqual(ask(self.root, "art")["kind"], "not_in_corpus")

    def test_retired_leaves_search(self):
        rec = self.gen()
        supersede(self.root, rec["document_id"], "wrong light")
        self.assertEqual(ask(self.root, rec["document_id"].lower())["status"], "retired")
        self.assertEqual(search(self.root, "celadon"), [])


class LifecycleTests(Base):
    def test_ratify_keeps_it_generated(self):
        rec = self.gen()
        done = ratify(self.root, rec["document_id"], "Mitchell D. McPhetridge", "reviewed")
        self.assertEqual((done["kind"], done["origin"], done["origin_depth"]), ("generated", "ratified", 0))
        ev = lineage(self.root)[-1]
        self.assertEqual((ev["op"], ev["who"], ev["at"]), ("ratify", done["ratified_by"], done["ratified_at"]))
        self.assertValid()

    def test_ratify_rules(self):
        rec = self.gen()
        with self.assertRaises(LifecycleError):
            ratify(self.root, rec["document_id"], "   ")
        for name in ("studio", "external"):
            with self.assertRaises(LifecycleError):
                ratify(self.root, rec["document_id"], "M", profile=name)
        ratify(self.root, rec["document_id"], "M")
        with self.assertRaises(LifecycleError):
            ratify(self.root, rec["document_id"], "N")
        unknown, _ = self.found()
        with self.assertRaises(LifecycleError):
            ratify(self.root, unknown["document_id"], "M")

    def test_retire_moves_page_and_bytes_off_the_wall(self):
        rec = self.gen()
        raw = (self.root / rec["file"]).read_bytes()
        done = supersede(self.root, rec["document_id"], "wrong style")
        self.assertEqual((self.root / done["file"]).read_bytes(), raw)
        self.assertFalse((self.root / rec["file"]).exists())
        self.assertNotIn(rec["document_id"], (self.root / "wall" / "_index.html").read_text())
        self.assertIn("RETIRED", (self.root / done["path"]).read_text())
        self.assertValid()

    def test_retire_rules(self):
        a, b = self.gen("first"), self.gen("second")
        with self.assertRaises(LifecycleError):
            supersede(self.root, a["document_id"], "x", successor=a["document_id"])
        supersede(self.root, a["document_id"], "redo", successor=b["document_id"])
        with self.assertRaises(LifecycleError):
            supersede(self.root, a["document_id"], "again")
        self.assertIsNone(supersede(self.root, "IMG-20000101-000000", "x"))
        self.assertValid()
