import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeIntake
from tests.helpers import make_config
from voice_portal import prayer_intake
from voice_portal.db import connect


class PrayerIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.conn = connect(self.cfg.db_path)
        self.intake = FakeIntake()
        self.url = self.intake.url

    def tearDown(self):
        self.intake.stop()
        self.conn.close()
        self.tmp.cleanup()

    def _upload(self, cid, idx, data, claim=None):
        return prayer_intake.upload_segment(self.conn, self.url, capture_id=cid, segment_index=idx, data=data, client_sha256=claim if claim is not None else hashlib.sha256(data).hexdigest(), filename=f"{idx}.wav", content_type="audio/wav")

    def test_segments_then_finalize_with_verified_receipts(self):
        cid = "capture-abc_1"
        seg0, seg1 = b"RIFF0000WAVEfmt " + b"\x00" * 100, b"RIFF0000WAVEfmt " + b"\x01" * 120
        r0 = self._upload(cid, 0, seg0)
        self.assertEqual(r0["status"], "uploaded")
        self.assertTrue(r0["verified"])
        r1 = self._upload(cid, 1, seg1)
        self.assertEqual(prayer_intake.capture_status(self.conn, self.url, capture_id=cid)["status"], "segments_uploaded")
        # repeated upload of the same identity is accepted (reused), never a new capture
        again = self._upload(cid, 0, seg0)
        self.assertTrue(again["receipt"]["reused"])
        self.assertEqual(len([c for c in self.intake.calls if c.endswith("/capture-segments")]), 3)
        segments = [{"segment_index": 0, "audio_sha256": r0["audio_sha256"], "size_bytes": r0["size_bytes"]}, {"segment_index": 1, "audio_sha256": r1["audio_sha256"], "size_bytes": r1["size_bytes"]}]
        fin = prayer_intake.finalize(self.conn, self.url, capture_id=cid, segments=segments, client_captured_at=1790000000.0)
        self.assertEqual(fin["status"], "finalized")
        self.assertTrue(fin["recording_id"].startswith("rec"))
        status = prayer_intake.capture_status(self.conn, self.url, capture_id=cid)
        self.assertEqual(status["status"], "finalized")  # finalize is not publication
        self.assertEqual(status["recording"]["status"], "queued")
        # publication becomes visible only when the intake reports a prayer receipt
        self.intake.recordings[fin["recording_id"]]["prayer_receipt"] = {"transcript_id": "prayer-xyz"}
        status = prayer_intake.capture_status(self.conn, self.url, capture_id=cid)
        self.assertEqual(status["status"], "published")
        self.assertEqual(status["prayer_id"], "prayer-xyz")
        # idempotent finalize
        fin2 = prayer_intake.finalize(self.conn, self.url, capture_id=cid, segments=segments, client_captured_at=None)
        self.assertEqual(fin2["status"], "finalized")
        self.assertTrue(fin2["receipt"]["reused"])

    def test_client_hash_mismatch_is_not_forwarded(self):
        r = self._upload("cap2", 0, b"abc", claim="0" * 64)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["reason"], "client_hash_mismatch")
        self.assertFalse(any(c.endswith("/capture-segments") for c in self.intake.calls))

    def test_finalize_refuses_unverified_segments(self):
        self._upload("cap3", 0, b"segment zero")
        fin = prayer_intake.finalize(self.conn, self.url, capture_id="cap3", segments=[{"segment_index": 0, "audio_sha256": hashlib.sha256(b"segment zero").hexdigest(), "size_bytes": 12}, {"segment_index": 1, "audio_sha256": "f" * 64, "size_bytes": 5}], client_captured_at=None)
        self.assertEqual(fin["status"], "segments_pending")
        self.assertEqual(fin["missing_or_unverified"], [1])
        self.assertFalse(any(c.endswith("/capture-finalize") for c in self.intake.calls))

    def test_intake_unreachable_keeps_segment_pending(self):
        dead = "http://127.0.0.1:1"
        r = prayer_intake.upload_segment(self.conn, dead, capture_id="cap4", segment_index=0, data=b"xyz", client_sha256=hashlib.sha256(b"xyz").hexdigest(), filename="0.wav", content_type="audio/wav")
        self.assertEqual(r["status"], "intake_unreachable")
        status = prayer_intake.capture_status(self.conn, dead, capture_id="cap4", refresh=False)
        self.assertEqual(status["status"], "intake_unreachable")
        self.assertEqual(status["segments"][0]["status"], "pending")
        # later retry against a reachable intake uses the same identity
        r = self._upload("cap4", 0, b"xyz")
        self.assertEqual(r["status"], "uploaded")

    def test_invalid_capture_id_rejected_locally(self):
        with self.assertRaises(ValueError):
            prayer_intake.ensure_capture(self.conn, "bad id with spaces")

    def test_discard(self):
        self._upload("cap5", 0, b"zzz")
        out = prayer_intake.discard(self.conn, self.url, capture_id="cap5")
        self.assertEqual(out["status"], "discarded")
        self.assertTrue(out["receipt"]["discarded"])


if __name__ == "__main__":
    unittest.main()
