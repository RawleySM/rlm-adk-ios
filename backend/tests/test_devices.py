"""Phone inspection: snapshots with honest freshness, commands whose execution is only ever reported
by the phone, verified capture-file copies, manifest and prayer ASR sync, app-rendered images."""

import base64
import hashlib
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.helpers import make_config
from voice_portal import devices
from voice_portal.db import connect
from voice_portal.server import make_server
from voice_portal.ws import ClientWebSocket, client_handshake


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.cfg.listen_host, self.cfg.listen_port = "127.0.0.1", 0
        self.server = make_server(self.cfg)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _json(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        status, _, raw = self._request(method, path, body, {"Content-Type": "application/json"} if body else {})
        return status, json.loads(raw)

    def _phone(self, install_id="phone-a"):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        client_handshake(sock, "127.0.0.1", "/ws")
        ws = ClientWebSocket(sock)
        self.assertEqual(json.loads(ws.receive())["type"], "session")
        ws.send_json({"type": "hello", "mode": {"quiet": False, "activity": "idle"}, "install_id": install_id, "app_version": "0.2.0", "build": "6"})
        self.assertEqual(json.loads(ws.receive())["type"], "hello_ack")
        return ws

    def _wait(self, predicate, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        return predicate()

    # ----- snapshots and freshness -----

    def test_snapshot_freshness_is_never_guessed(self):
        status, body = self._json("GET", "/devices/nobody/snapshot")
        self.assertEqual((status, body["freshness"], body["snapshot"]), (200, "unknown_device", None))
        ws = self._phone()
        status, body = self._json("GET", "/devices/phone-a/snapshot")
        self.assertEqual(body["freshness"], "no_snapshot")
        self.assertTrue(body["connected"])
        ws.send_json({"type": "snapshot", "reason": "connect", "snapshot": {"client_time": 1.0, "selected_tab": "portal", "keyboard_visible": True}})
        self.assertEqual(json.loads(ws.receive())["type"], "snapshot_ack")
        status, body = self._json("GET", "/devices/phone-a/snapshot")
        self.assertEqual(body["freshness"], "live")
        self.assertEqual(body["snapshot"]["keyboard_visible"], True)
        self.assertEqual(body["reason"], "connect")
        self.assertLess(body["age_s"], 5)
        conn = connect(self.cfg.db_path)
        self.assertEqual(devices.latest_snapshot(conn, "phone-a", stale_after=0.0)["freshness"], "stale")
        ws.close()
        self._wait(lambda: not self._json("GET", "/devices/phone-a/snapshot")[1]["connected"])
        body = self._json("GET", "/devices/phone-a/snapshot")[1]
        self.assertEqual(body["freshness"], "offline")
        self.assertEqual(body["snapshot"]["selected_tab"], "portal")  # last known state kept, labelled offline
        listed = self._json("GET", "/devices")[1]["devices"]
        self.assertEqual([d["install_id"] for d in listed], ["phone-a"])

    # ----- commands -----

    def test_command_lifecycle_queued_delivered_applied_and_no_reexecution(self):
        status, cmd = self._json("POST", "/devices/phone-a/commands", {"kind": "select_tab", "args": {"tab": "diagnostics"}})
        self.assertEqual((status, cmd["status"], cmd["pushed"]), (201, "queued", False))
        status, bad = self._json("POST", "/devices/phone-a/commands", {"kind": "reboot"})
        self.assertEqual(status, 400)
        ws = self._phone()  # the queued command is pushed on hello
        pushed = json.loads(ws.receive())
        self.assertEqual((pushed["type"], pushed["command_id"], pushed["kind"], pushed["args"]), ("command", cmd["command_id"], "select_tab", {"tab": "diagnostics"}))
        self.assertEqual(self._json("GET", f"/devices/phone-a/commands/{cmd['command_id']}")[1]["status"], "delivered")
        ws.send_json({"type": "command_result", "command_id": cmd["command_id"], "status": "applied", "result": {"tab": "diagnostics"}})
        ack = json.loads(ws.receive())
        self.assertEqual((ack["type"], ack["status"]), ("command_result_ack", "applied"))
        stored = self._json("GET", f"/devices/phone-a/commands/{cmd['command_id']}")[1]
        self.assertEqual((stored["status"], stored["result"]), ("applied", {"tab": "diagnostics"}))
        first_result_at = stored["result_at"]
        # A repeated report (reconnect replay) keeps the first result; nothing is re-run.
        ws.send_json({"type": "command_result", "command_id": cmd["command_id"], "status": "failed", "result": {"reason": "later"}})
        json.loads(ws.receive())
        again = self._json("GET", f"/devices/phone-a/commands/{cmd['command_id']}")[1]
        self.assertEqual((again["status"], again["result_at"]), ("applied", first_result_at))
        # Live push while connected; the phone may report failure, and the server records exactly that.
        status, live = self._json("POST", "/devices/phone-a/commands", {"kind": "select_tab", "args": {"tab": "nowhere"}})
        self.assertEqual((live["status"], live["pushed"]), ("delivered", True))
        self.assertEqual(json.loads(ws.receive())["command_id"], live["command_id"])
        ws.send_json({"type": "command_result", "command_id": live["command_id"], "status": "failed", "result": {"reason": "unknown tab nowhere"}})
        json.loads(ws.receive())
        self.assertEqual(self._json("GET", f"/devices/phone-a/commands/{live['command_id']}")[1]["status"], "failed")
        ws.send_json({"type": "command_result", "command_id": live["command_id"], "status": "done"})
        self.assertEqual(json.loads(ws.receive())["type"], "error")
        ws.close()
        self._wait(lambda: not self._json("GET", "/devices/phone-a")[1]["connected"])
        status, offline = self._json("POST", "/devices/phone-a/commands", {"kind": "snapshot"})
        self.assertEqual((offline["status"], offline["pushed"]), ("queued", False))
        overview = self._json("GET", "/devices/phone-a")[1]
        self.assertEqual(overview["freshness"], "no_snapshot")
        self.assertEqual({c["status"] for c in overview["commands"]}, {"applied", "failed", "queued"})

    def test_failed_transport_never_counts_as_delivered(self):
        svc = self.server.service
        sent = []

        def broken(event):
            sent.append(event)
            raise OSError("socket gone")

        with svc.device_lock:
            svc.device_sessions["phone-b"] = ("dead-session", broken)
        self._json("POST", "/devices/phone-b/commands", {"kind": "snapshot"})  # registers nothing; device row absent
        conn = connect(self.cfg.db_path)
        devices.record_hello(conn, "phone-b", session_id="dead-session", app_version=None, build=None)
        status, cmd = self._json("POST", "/devices/phone-b/commands", {"kind": "snapshot"})
        self.assertEqual(len(sent), 1, "the transport was attempted")
        self.assertEqual((cmd["pushed"], cmd["status"]), (False, "queued"))
        self.assertEqual(self._json("GET", f"/devices/phone-b/commands/{cmd['command_id']}")[1]["status"], "queued")
        self.assertNotIn("phone-b", svc.live_device_ids())
        self.assertFalse(self._json("GET", "/devices/phone-b")[1]["connected"])
        # The real phone connects: both queued commands arrive, and the result lands on the right id.
        ws = self._phone("phone-b")
        got = sorted(json.loads(ws.receive())["command_id"] for _ in range(2))
        self.assertIn(cmd["command_id"], got)
        ws.send_json({"type": "command_result", "command_id": cmd["command_id"], "status": "applied", "result": {}})
        json.loads(ws.receive())
        self.assertEqual(self._json("GET", f"/devices/phone-b/commands/{cmd['command_id']}")[1]["status"], "applied")

    def test_restart_forgets_connections_and_background_is_not_live(self):
        ws = self._phone()
        ws.send_json({"type": "snapshot", "reason": "connect", "snapshot": {"scene_phase": "active"}})
        json.loads(ws.receive())
        self.assertEqual(self._json("GET", "/devices/phone-a/snapshot")[1]["freshness"], "live")
        ws.send_json({"type": "snapshot", "reason": "background", "snapshot": {"scene_phase": "background"}})
        json.loads(ws.receive())
        body = self._json("GET", "/devices/phone-a/snapshot")[1]
        self.assertEqual((body["freshness"], body["connected"], body["scene_phase"]), ("connected_background", True, "background"))
        # A new service on the same database (restart) has no sockets: nothing is connected or live.
        self.server.shutdown()
        self.server.server_close()
        self.server = make_server(self.cfg)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        body = self._json("GET", "/devices/phone-a/snapshot")[1]
        self.assertEqual((body["freshness"], body["connected"]), ("offline", False))
        self.assertFalse(self._json("GET", "/devices")[1]["devices"][0]["connected"])
        ws.close()

    def test_delivered_command_without_result_is_pushed_again_on_reconnect(self):
        ws = self._phone()
        status, cmd = self._json("POST", "/devices/phone-a/commands", {"kind": "image"})
        self.assertEqual((cmd["status"], cmd["pushed"]), ("delivered", True))
        self.assertEqual(json.loads(ws.receive())["command_id"], cmd["command_id"])
        ws.close()  # the phone lost the socket before reporting
        self._wait(lambda: not self._json("GET", "/devices/phone-a")[1]["connected"])
        self.assertEqual(self._json("GET", f"/devices/phone-a/commands/{cmd['command_id']}")[1]["status"], "delivered")
        ws = self._phone()
        again = json.loads(ws.receive())
        self.assertEqual((again["type"], again["command_id"]), ("command", cmd["command_id"]))
        ws.send_json({"type": "command_result", "command_id": cmd["command_id"], "status": "applied", "result": {"replayed": "yes"}})
        json.loads(ws.receive())
        stored = self._json("GET", f"/devices/phone-a/commands/{cmd['command_id']}")[1]
        self.assertEqual((stored["status"], stored["result"]), ("applied", {"replayed": "yes"}))
        ws.close()
        self._wait(lambda: not self._json("GET", "/devices/phone-a")[1]["connected"])
        ws = self._phone()
        ws.send_json({"type": "ping", "t": 1})
        self.assertEqual(json.loads(ws.receive())["type"], "pong", "a resolved command is not pushed again")

    # ----- capture file copies -----

    def test_capture_file_copy_verifies_digest_size_and_is_idempotent(self):
        wav = b"RIFF" + bytes(range(256)) * 4
        digest = hashlib.sha256(wav).hexdigest()
        headers = {"Content-Type": "audio/wav", "X-SHA256": digest, "X-Filename": "00000003.wav", "X-Command-Id": "cmd1"}
        status, _, raw = self._request("POST", "/devices/phone-a/captures/ambient-1/files/3", wav, headers)
        first = json.loads(raw)
        self.assertEqual((status, first["stored"], first["reused"], first["sha256"], first["size_bytes"]), (200, True, False, digest, len(wav)))
        path = Path(first["path"])
        self.assertTrue(path.is_relative_to(Path(self.cfg.state_root) / "evidence" / "devices" / "phone-a" / "captures" / "ambient-1"))
        self.assertEqual(path.read_bytes(), wav)
        again = json.loads(self._request("POST", "/devices/phone-a/captures/ambient-1/files/3", wav, headers)[2])
        self.assertEqual((again["stored"], again["reused"], again["path"]), (True, True, str(path)))
        other = json.loads(self._request("POST", "/devices/phone-a/captures/ambient-1/files/3", wav + b"x", {**headers, "X-SHA256": hashlib.sha256(wav + b"x").hexdigest()})[2])
        self.assertEqual((other["stored"], other["reason"]), (False, "different_bytes_already_stored"))
        self.assertEqual(path.read_bytes(), wav)  # never overwritten
        bad = json.loads(self._request("POST", "/devices/phone-a/captures/ambient-1/files/4", wav, {**headers, "X-SHA256": "0" * 64})[2])
        self.assertEqual((bad["stored"], bad["reason"]), (False, "client_hash_mismatch"))
        evidence = self._json("GET", "/devices/phone-a/captures/ambient-1")[1]
        self.assertEqual([(f["segment_index"], f["bytes"], f["sha256"], f["available"], f["command_id"]) for f in evidence["files"]], [(3, len(wav), digest, True, "cmd1")])
        self.assertIn("not part of the prayer intake", evidence["note"])

    # ----- manifest and prayer ASR -----

    def test_manifest_and_prayer_asr_sync_are_idempotent_by_sequence(self):
        manifest = {"client_time": 5.0, "captures": [{"capture_id": "prayer-1", "kind": "prayer", "segments": []}], "orphans": [{"capture_id": "ambient-0", "filename": "00000009.wav", "bytes": 10}]}
        status, stored = self._json("POST", "/devices/phone-a/manifest", manifest)
        self.assertEqual((status, stored["captures"], stored["orphans"]), (200, 1, 1))
        got = self._json("GET", "/devices/phone-a/manifest")[1]
        self.assertEqual(got["manifest"]["orphans"][0]["filename"], "00000009.wav")
        ws = self._phone()
        ws.send_json({"type": "prayer_asr", "capture_id": "prayer-1", "items": [{"sequence": 1, "client_time": 1.0, "text": "Lord"}, {"sequence": 2, "client_time": 2.0, "text": "thank you"}]})
        ack = json.loads(ws.receive())
        self.assertEqual((ack["type"], ack["inserted"], ack["total"], ack["max_sequence"]), ("prayer_asr_ack", 2, 2, 2))
        ws.send_json({"type": "prayer_asr", "capture_id": "prayer-1", "items": [{"sequence": 2, "client_time": 2.0, "text": "thank you"}, {"sequence": 3, "client_time": 3.0, "text": "amen"}]})
        ack = json.loads(ws.receive())
        self.assertEqual((ack["inserted"], ack["total"], ack["max_sequence"]), (1, 3, 3))
        evidence = self._json("GET", "/devices/phone-a/captures/prayer-1")[1]
        self.assertEqual([a["text"] for a in evidence["asr"]], ["Lord", "thank you", "amen"])
        self.assertEqual(evidence["files"], [])

    # ----- images -----

    def test_image_round_trip_with_digest_and_age(self):
        self.assertEqual(self._request("GET", "/devices/phone-a/images/latest")[0], 404)
        jpeg = b"\xff\xd8\xff" + b"fake" * 50
        digest = hashlib.sha256(jpeg).hexdigest()
        snap = base64.b64encode(json.dumps({"selected_tab": "work", "keyboard_visible": False, "note": "ünïcode"}).encode()).decode()
        status, _, raw = self._request("POST", "/devices/phone-a/images", jpeg, {"Content-Type": "image/jpeg", "X-SHA256": digest, "X-Scope": "app_window", "X-Snapshot-B64": snap, "X-Command-Id": "img1"})
        stored = json.loads(raw)
        self.assertEqual((status, stored["stored"], stored["sha256"], stored["bytes"]), (200, True, digest, len(jpeg)))
        mismatch = json.loads(self._request("POST", "/devices/phone-a/images", jpeg, {"X-SHA256": "1" * 64})[2])
        self.assertEqual(mismatch["stored"], False)
        status, headers, body = self._request("GET", "/devices/phone-a/images/latest")
        self.assertEqual((status, body, headers["Content-Type"], headers["X-Image-Scope"]), (200, jpeg, "image/jpeg", "app_window"))
        self.assertLess(float(headers["X-Image-Age-S"]), 5)
        meta = self._json("GET", "/devices/phone-a/images/latest/meta")[1]
        self.assertEqual((meta["command_id"], meta["snapshot"]["selected_tab"], meta["snapshot"]["note"], meta["available"]), ("img1", "work", "ünïcode", True))


if __name__ == "__main__":
    unittest.main()
