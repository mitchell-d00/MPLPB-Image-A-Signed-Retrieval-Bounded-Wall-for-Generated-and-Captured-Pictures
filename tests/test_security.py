import json
import os
import shutil
import tempfile
from pathlib import Path

from base import Base, G, failures
from mplpb_image import keys as K
from mplpb_image.corpus import catalog, rebuild_manifest, sign_record, write_page, save_catalog, refresh
from mplpb_image.lifecycle import LifecycleError, attest, ratify
from mplpb_image.profiles import PROFILES, serve_check
from mplpb_image.validate import record_trust


def foreign_key():
    return K.NodeKey(bytes(range(32)))


class SignatureTests(Base):
    def test_every_write_is_signed_and_verifies(self):
        self.gen()
        self.found()
        f = {x["code"]: x for x in __import__("mplpb_image.validate", fromlist=["validate"]).validate(
            self.root, trusted=K.trusted_ids(), strict=True)}
        self.assertTrue(all(v["ok"] for v in f.values()), f)

    def test_catalog_edit_without_key_breaks_signature(self):
        rec = self.gen()
        self.edit_catalog(lambda pages: pages[0].update(model="a camera, honest"))
        rebuild_manifest(self.root)
        self.assertIn("signature does not verify", failures(self.root)["I.9"])

    def test_laundering_to_captured_without_key_fails_twice(self):  # F4
        rec = self.gen()

        def launder(pages):
            pages[0].update(kind="captured", kind_basis="attested", origin="human", origin_depth=0,
                            attested_by="nobody", attested_at="2026-01-01T00:00:00Z")
        self.edit_catalog(launder)
        write_page(self.root, catalog(self.root)[0])
        rebuild_manifest(self.root)
        f = failures(self.root)
        self.assertIn("signature does not verify", f["I.9"])
        self.assertIn("no matching lineage record", f["I.5"])

    def test_lineage_tamper_breaks_chain(self):
        self.gen()
        path = self.root / "_net" / "lineage.json"
        data = json.loads(path.read_text())
        data["events"][0]["corpus_id"] = "C-IMAGE-FORGED"
        path.write_text(json.dumps(data))
        rebuild_manifest(self.root)
        f = failures(self.root)["I.9"]
        self.assertIn("breaks the hash chain", f)

    def test_swapped_signer_key_detected(self):
        self.gen()
        path = self.root / "_net" / "signers.json"
        data = json.loads(path.read_text())
        nid = next(iter(data["keys"]))
        data["keys"][nid] = foreign_key().public_hex
        path.write_text(json.dumps(data))
        self.assertIn("does not match its public key", failures(self.root)["I.9"])
        from mplpb_image.corpus import TamperError
        with self.assertRaises(TamperError):  # a write must not silently repair it
            self.gen()

    def test_foreign_key_forgery_is_valid_but_untrusted(self):
        """An attacker with write access re-signs with their own key: valid, not trusted."""
        rec = self.gen()
        evil = foreign_key()
        ratify(self.root, rec["document_id"], "Mallory", key=evil)
        self.assertEqual(failures(self.root, strict=False), {})
        self.assertIn("does not trust", failures(self.root, strict=True)["I.9"])
        done = catalog(self.root)[0]
        valid, trusted = record_trust(self.root, done)
        self.assertEqual((valid, trusted), (True, False))
        self.assertFalse(serve_check("external", done, valid, trusted)["would_serve_as_source"])
        self.assertTrue(serve_check("lab", done, valid, trusted)["would_serve_as_source"])
        K.trust(evil.public_hex, "Mallory, now trusted")
        self.assertEqual(failures(self.root, strict=True), {})

    def test_fingerprint_forgery_detected(self):
        self.gen()
        path = self.root / "_net" / "fingerprint.json"
        data = json.loads(path.read_text())
        data["sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        f = failures(self.root)
        self.assertIn("fingerprint", f["I.9"] + f["I.8"])


class KeyHygieneTests(Base):
    def test_key_lives_outside_the_seed_with_tight_permissions(self):
        k = K.load_or_create()
        self.assertTrue((self.home / "node.key").is_file())
        self.assertFalse(list(self.root.rglob("node.key")))
        self.assertTrue(K.key_file_permissions_ok())
        self.assertEqual(json.loads((self.root / "_net" / "signers.json").read_text())["keys"],
                         {k.node_id: k.public_hex})

    def test_secret_copied_into_seed_fails(self):
        (self.root / "notes.txt").write_text("backup: " + K.load_or_create().secret.hex())
        rebuild_manifest(self.root)
        self.assertIn("secret key", failures(self.root)["I.10"])

    def test_key_file_in_seed_fails(self):
        shutil.copy(self.home / "node.key", self.root / "node.key")
        rebuild_manifest(self.root)
        self.assertIn("key material", failures(self.root)["I.10"])

    def test_forged_evidence_detected_even_when_resigned(self):
        """The key holder cannot make a file say something its bytes do not."""
        rec, _ = self.found()
        pages = catalog(self.root)
        pages[0]["evidence"]["camera"] = "Leica M11"
        pages[0]["evidence"]["signals"].append("camera named in EXIF: Leica M11 [captured, hint]")
        sign_record(self.root, pages[0])
        write_page(self.root, pages[0])
        save_catalog(self.root, pages)
        refresh(self.root)
        f = failures(self.root)
        self.assertEqual(list(f), ["I.11"])


class AttestTests(Base):
    STATEMENT = "I took this on my phone at the harbour on 3 May."

    def test_attest_unknown_to_captured(self):
        rec, _ = self.found()
        done = attest(self.root, rec["document_id"], "Mitchell D. McPhetridge", self.STATEMENT)
        self.assertEqual((done["kind"], done["kind_basis"], done["origin"], done["origin_depth"]),
                         ("captured", "attested", "human", 0))
        self.assertValid()

    def test_generated_cannot_be_attested(self):
        for rec in (self.gen(), self.found(G.jpeg_shell(w=1024, h=1024, exif=G.tiff_exif(software="DALL-E 3")))[0]):
            self.assertEqual(rec["kind"], "generated")
            with self.assertRaises(LifecycleError):
                attest(self.root, rec["document_id"], "M", self.STATEMENT)

    def test_attest_needs_name_statement_and_profile(self):
        rec, _ = self.found()
        with self.assertRaises(LifecycleError):
            attest(self.root, rec["document_id"], "", self.STATEMENT)
        with self.assertRaises(LifecycleError):
            attest(self.root, rec["document_id"], "M", "real")
        for name in ("studio", "external"):
            with self.assertRaises(LifecycleError):
                attest(self.root, rec["document_id"], "M", self.STATEMENT, profile=name)

    def test_camera_exif_alone_is_not_a_capture(self):
        rec, v = self.found(G.jpeg_shell(exif=G.tiff_exif()))
        self.assertEqual(rec["kind"], "unknown")
        self.assertTrue(v["capture_hints"])


class ServeTests(Base):
    def serve(self, profile, rec):
        valid, trusted = record_trust(self.root, rec)
        return serve_check(profile, rec, valid, trusted)

    def test_unratified_generated_is_never_source(self):  # F6
        rec = self.gen()
        for name in PROFILES:
            out = self.serve(name, rec)
            self.assertFalse(out["would_serve_as_source"], name)
            self.assertEqual(out["serve_as"], "generated, labelled")

    def test_ratified_generated_is_illustration_never_photograph(self):
        rec = ratify(self.root, self.gen()["document_id"], "M")
        for name in PROFILES:
            out = self.serve(name, rec)
            self.assertEqual(out["serve_as"], "approved illustration")
            self.assertTrue(out["would_serve_as_source"])
            self.assertFalse(out["may_serve_as_photograph"])

    def test_attested_capture_is_a_photograph(self):
        rec, _ = self.found()
        rec = attest(self.root, rec["document_id"], "M", AttestTests.STATEMENT)
        out = self.serve("external", rec)
        self.assertTrue(out["may_serve_as_photograph"])

    def test_unknown_withheld_externally(self):
        rec, _ = self.found()
        self.assertEqual(self.serve("external", rec)["serve_as"], "withheld")
        self.assertEqual(self.serve("lab", rec)["serve_as"], "unverified image")

    def test_external_requires_checked_trust(self):
        rec = ratify(self.root, self.gen()["document_id"], "M")
        self.assertFalse(serve_check("external", rec)["would_serve_as_source"])

    def test_bad_signature_is_never_shown(self):
        rec = self.gen()
        rec = dict(rec, sig="00" * 64)
        out = self.serve("lab", rec)
        self.assertFalse(out["may_show"])
