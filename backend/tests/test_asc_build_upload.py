"""Contract and failure tests for scripts/asc_build_upload.py against a fake App Store Connect."""

import hashlib
import importlib.util
import io
import json
import os
import plistlib
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "asc_build_upload.py"
spec = importlib.util.spec_from_file_location("asc_build_upload", SCRIPT)
asc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asc)


class FakeASC:
    """Implements just enough of buildUploads/buildUploadFiles/builds, recording every request."""

    def __init__(self, fail_part_once=False, part_count=2):
        self.requests = []
        self.uploads = {}
        self.files = {}
        self.parts = {}
        self.fail_part_once = fail_part_once
        self.part_count = part_count
        self.builds = []
        self.file_get_status = {}      # file_id -> forced HTTP status for GET
        self.upload_state_sequence = []  # successive states returned for GET buildUploads/{id}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                if self.path.startswith("/v1/builds"):
                    return self._json(200, {"data": fake.builds})
                if self.path.startswith("/v1/buildUploads/"):
                    uid = self.path.split("/")[3]
                    if uid not in fake.uploads:
                        return self._json(404, {"errors": [{"status": "404"}]})
                    if fake.upload_state_sequence:
                        fake.uploads[uid]["state"] = {"state": fake.upload_state_sequence.pop(0), "errors": [], "warnings": [], "infos": []}
                    return self._json(200, {"data": {"type": "buildUploads", "id": uid, "attributes": fake.uploads[uid]}})
                if self.path.startswith("/v1/buildUploadFiles/"):
                    fid = self.path.split("/")[3].split("?")[0]
                    if fid in fake.file_get_status:
                        return self._json(fake.file_get_status[fid], {"errors": [{"status": str(fake.file_get_status[fid]), "title": "forced"}]})
                    if fid not in fake.files:
                        return self._json(404, {"errors": [{"status": "404"}]})
                    return self._json(200, {"data": {"type": "buildUploadFiles", "id": fid, "attributes": fake.files[fid]}})
                return self._json(404, {})

            def do_POST(self):
                payload = json.loads(self._body())
                fake.requests.append(("POST", self.path, payload))
                if self.path == "/v1/buildUploads":
                    uid = f"upload-{len(fake.uploads) + 1}"
                    a = payload["data"]["attributes"]
                    assert set(a) == {"cfBundleShortVersionString", "cfBundleVersion", "platform"}
                    assert payload["data"]["relationships"]["app"]["data"] == {"type": "apps", "id": "6822129536"}
                    fake.uploads[uid] = {**a, "state": {"state": "AWAITING_UPLOAD", "errors": [], "warnings": [], "infos": []}}
                    return self._json(201, {"data": {"type": "buildUploads", "id": uid, "attributes": fake.uploads[uid]}})
                if self.path == "/v1/buildUploadFiles":
                    a = payload["data"]["attributes"]
                    assert set(a) == {"fileName", "fileSize", "uti", "assetType"}, a
                    assert a["uti"] == "com.apple.ipa" and a["assetType"] == "ASSET"
                    uid = payload["data"]["relationships"]["buildUpload"]["data"]["id"]
                    assert uid in fake.uploads
                    fid = f"file-{len(fake.files) + 1}"
                    size = a["fileSize"]
                    chunk = -(-size // fake.part_count)
                    ops = []
                    for i in range(fake.part_count):
                        off = i * chunk
                        if off >= size:
                            break
                        ops.append({"method": "PUT", "url": f"{fake.url}/upload/{fid}/{i + 1}", "offset": off, "length": min(chunk, size - off), "partNumber": i + 1, "requestHeaders": [{"name": "Content-Type", "value": "application/octet-stream"}, {"name": "X-Part", "value": str(i + 1)}]})
                    fake.files[fid] = {**a, "assetDeliveryState": {"state": "AWAITING_UPLOAD", "errors": [], "warnings": []}, "uploadOperations": ops}
                    return self._json(201, {"data": {"type": "buildUploadFiles", "id": fid, "attributes": fake.files[fid]}})
                return self._json(404, {})

            def do_PUT(self):
                data = self._body()
                fake.requests.append(("PUT", self.path, {"bytes": len(data), "x_part": self.headers.get("X-Part"), "ctype": self.headers.get("Content-Type")}))
                _, fid, part = self.path.strip("/").split("/")
                if fake.fail_part_once and part == "2":
                    fake.fail_part_once = False
                    return self._json(500, {"errors": [{"status": "500", "title": "simulated"}]})
                fake.parts.setdefault(fid, {})[int(part)] = data
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PATCH(self):
                payload = json.loads(self._body())
                fake.requests.append(("PATCH", self.path, payload))
                fid = self.path.split("/")[3]
                a = payload["data"]["attributes"]
                assert payload["data"]["type"] == "buildUploadFiles" and payload["data"]["id"] == fid
                assert a["uploaded"] is True
                assert set(a["sourceFileChecksums"]) == {"file"} and a["sourceFileChecksums"]["file"]["algorithm"] == "MD5"
                received = b"".join(fake.parts[fid][k] for k in sorted(fake.parts[fid]))
                ok = hashlib.md5(received).hexdigest() == a["sourceFileChecksums"]["file"]["hash"] and len(received) == fake.files[fid]["fileSize"]
                fake.files[fid]["assetDeliveryState"] = {"state": "UPLOAD_COMPLETE" if ok else "FAILED", "errors": [], "warnings": []}
                uid = next(u for u in fake.uploads)
                fake.uploads[uid]["state"] = {"state": "COMPLETE" if ok else "FAILED", "errors": [], "warnings": [], "infos": []}
                if ok:
                    fake.builds = [{"type": "builds", "id": "build-1", "attributes": {"version": fake.uploads[uid]["cfBundleVersion"], "processingState": "PROCESSING", "uploadedDate": "now", "expired": False}}]
                return self._json(200, {"data": {"type": "buildUploadFiles", "id": fid, "attributes": fake.files[fid]}})

        return H


def make_ipa(path: Path, version="0.2.0", build="3", size=5000) -> bytes:
    info = plistlib.dumps({"CFBundleIdentifier": "com.rawleystanhope.rlmadk", "CFBundleShortVersionString": version, "CFBundleVersion": build})
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Payload/RLMADK.app/Info.plist", info)
        z.writestr("Payload/RLMADK.app/RLMADK", os.urandom(size))
    return path.read_bytes()


class BuildUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ipa = self.dir / "RLMADK.ipa"
        self.data = make_ipa(self.ipa)

    def tearDown(self):
        self.tmp.cleanup()

    def _args(self, fake, **over):
        argv = ["--ipa", str(self.ipa), "--app-id", "6822129536", "--key-id", "K", "--issuer-id", "I", "--key-path", "/dev/null", "--api", fake.url, "--poll-seconds", "5", "--poll-interval", "0", "--receipt", str(self.dir / "receipt.json")]
        for k, v in over.items():
            argv += [f"--{k.replace('_', '-')}", v]
        return asc.parse(argv)

    def test_contract_payloads_and_receipt(self):
        fake = FakeASC()
        try:
            rc = asc.run(self._args(fake), asc.Client(None, "K", "I", api=fake.url, token="t"), log=lambda s: None)
            self.assertEqual(rc, 0)
            posts = [r for r in fake.requests if r[0] == "POST"]
            self.assertEqual(posts[0][1], "/v1/buildUploads")
            self.assertEqual(posts[0][2]["data"]["attributes"], {"cfBundleShortVersionString": "0.2.0", "cfBundleVersion": "3", "platform": "IOS"})
            self.assertEqual(posts[1][2]["data"]["attributes"]["fileSize"], len(self.data))
            puts = [r for r in fake.requests if r[0] == "PUT"]
            self.assertEqual([p[2]["x_part"] for p in puts], ["1", "2"])
            self.assertEqual(sum(p[2]["bytes"] for p in puts), len(self.data))
            patch = next(r for r in fake.requests if r[0] == "PATCH")
            self.assertEqual(patch[2]["data"]["attributes"]["sourceFileChecksums"], {"file": {"hash": hashlib.md5(self.data).hexdigest(), "algorithm": "MD5"}})
            self.assertNotIn("sourceFileChecksum", patch[2]["data"]["attributes"])
            receipt = json.load(open(self.dir / "receipt.json"))
            self.assertEqual(receipt["build_upload_id"], "upload-1")
            self.assertEqual(receipt["build_upload_file_id"], "file-1")
            self.assertEqual(receipt["final_state"]["state"], "COMPLETE")
            self.assertEqual(receipt["builds"][0]["version"], "3")
        finally:
            fake.stop()

    def test_failed_part_keeps_ids_and_rerun_reconciles_without_new_reservation(self):
        fake = FakeASC(fail_part_once=True)
        try:
            client = asc.Client(None, "K", "I", api=fake.url, token="t")
            with self.assertRaises(Exception):
                asc.run(self._args(fake), client, log=lambda s: None)  # part 2 fails with HTTP 500
            receipt = json.load(open(self.dir / "receipt.json"))
            self.assertEqual(receipt["build_upload_id"], "upload-1")
            self.assertEqual(receipt["build_upload_file_id"], "file-1")  # retained before the failing side effect
            self.assertEqual([s["part"] for s in receipt["steps"] if s["step"] == "part"], [1])
            rc = asc.run(self._args(fake), client, log=lambda s: None)
            self.assertEqual(rc, 0)
            self.assertEqual(sum(1 for r in fake.requests if r[0] == "POST"), 2)  # no second upload, no second reservation
            puts = [r for r in fake.requests if r[0] == "PUT"]
            self.assertEqual([p[2]["x_part"] for p in puts], ["1", "2", "2"])  # only the failed part was re-sent
            self.assertEqual(len(fake.uploads), 1)
            self.assertEqual(len(fake.files), 1)
        finally:
            fake.stop()

    def test_resumed_processing_upload_is_polled_not_reported_green(self):
        """UPLOAD-2: PROCESSING is not acceptance; the resumed run polls to FAILED/deadline or COMPLETE."""
        for sequence, expected in (("PROCESSING", "PROCESSING", "FAILED"), 1), (("PROCESSING", "PROCESSING"), 1), (("PROCESSING", "COMPLETE"), 0):
            fake = FakeASC()
            fake.uploads["upload-p"] = {"cfBundleShortVersionString": "0.2.0", "cfBundleVersion": "3", "state": {"state": "PROCESSING"}}
            fake.upload_state_sequence = list(sequence)
            try:
                args = self._args(fake, build_upload_id="upload-p")
                args.poll_seconds = 1
                rc = asc.run(args, asc.Client(None, "K", "I", api=fake.url, token="t"), log=lambda s: None)
                self.assertEqual(rc, expected, sequence)
                self.assertFalse(any(r[0] in ("POST", "PUT", "PATCH") for r in fake.requests), "resumed upload must not re-send")
                receipt = json.load(open(self.dir / "receipt.json"))
                self.assertEqual(receipt["final_state"]["state"], sequence[-1])
            finally:
                fake.stop()
                (self.dir / "receipt.json").unlink(missing_ok=True)

    def test_unreadable_file_record_fails_and_keeps_ids(self):
        """UPLOAD-3: a transient 500 on the recorded file must not create a replacement reservation."""
        fake = FakeASC(fail_part_once=True)
        try:
            client = asc.Client(None, "K", "I", api=fake.url, token="t")
            with self.assertRaises(Exception):
                asc.run(self._args(fake), client, log=lambda s: None)
            fake.file_get_status["file-1"] = 500
            rc = asc.run(self._args(fake), client, log=lambda s: None)
            self.assertEqual(rc, 1)
            receipt = json.load(open(self.dir / "receipt.json"))
            self.assertEqual(receipt["build_upload_id"], "upload-1")
            self.assertEqual(receipt["build_upload_file_id"], "file-1")
            self.assertEqual(sum(1 for r in fake.requests if r[0] == "POST"), 2)
            self.assertEqual(len(fake.files), 1)
            # once readable again, the same reservation is resumed and only the failed part is sent
            del fake.file_get_status["file-1"]
            rc = asc.run(self._args(fake), client, log=lambda s: None)
            self.assertEqual(rc, 0)
            self.assertEqual([p[2]["x_part"] for p in fake.requests if p[0] == "PUT"], ["1", "2", "2"])
        finally:
            fake.stop()

    def test_mismatched_reservation_is_replaced_without_inheriting_part_progress(self):
        fake = FakeASC(fail_part_once=True)
        try:
            client = asc.Client(None, "K", "I", api=fake.url, token="t")
            with self.assertRaises(Exception):
                asc.run(self._args(fake), client, log=lambda s: None)  # part 1 done, part 2 failed on file-1
            fake.files["file-1"]["fileSize"] = 1  # the recorded reservation no longer matches this IPA
            rc = asc.run(self._args(fake), client, log=lambda s: None)
            self.assertEqual(rc, 0)
            self.assertEqual(len(fake.files), 2)
            puts = [(p[1].split("/")[2], p[2]["x_part"]) for p in fake.requests if p[0] == "PUT"]
            self.assertEqual(puts, [("file-1", "1"), ("file-1", "2"), ("file-2", "1"), ("file-2", "2")])  # both parts re-sent for file-2
            receipt = json.load(open(self.dir / "receipt.json"))
            self.assertEqual(receipt["build_upload_file_id"], "file-2")
            self.assertIn("abandon_file", [s["step"] for s in receipt["steps"]])
        finally:
            fake.stop()

    def test_existing_build_number_is_never_resent(self):
        fake = FakeASC()
        fake.builds = [{"type": "builds", "id": "build-9", "attributes": {"version": "3", "processingState": "VALID"}}]
        try:
            rc = asc.run(self._args(fake), asc.Client(None, "K", "I", api=fake.url, token="t"), log=lambda s: None)
            self.assertEqual(rc, 3)
            self.assertFalse(any(r[0] == "POST" for r in fake.requests))
        finally:
            fake.stop()

    def test_reuse_given_upload_id_with_wrong_version_is_refused(self):
        fake = FakeASC()
        fake.uploads["upload-x"] = {"cfBundleShortVersionString": "0.1.0", "cfBundleVersion": "9", "state": {"state": "AWAITING_UPLOAD"}}
        try:
            rc = asc.run(self._args(fake, build_upload_id="upload-x"), asc.Client(None, "K", "I", api=fake.url, token="t"), log=lambda s: None)
            self.assertEqual(rc, 1)
            self.assertFalse(any(r[0] == "POST" for r in fake.requests))
        finally:
            fake.stop()

    def test_ipa_version_mismatch_refused(self):
        fake = FakeASC()
        try:
            rc = asc.run(self._args(fake, build="4"), asc.Client(None, "K", "I", api=fake.url, token="t"), log=lambda s: None)
            self.assertEqual(rc, 2)
        finally:
            fake.stop()


if __name__ == "__main__":
    unittest.main()
