import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from tests.fakes import FakeInference
from tests.helpers import make_config, write_prayer_store
from voice_portal.db import connect
from voice_portal.importer import import_family
from voice_portal.server import make_server
from voice_portal.sources.prayers import read_prayers
from voice_portal.ws import ClientWebSocket, client_handshake


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.inference = FakeInference(delay=0.05)
        self.cfg.inference_base_url = self.inference.base_url
        self.cfg.inference_status_url = f"http://127.0.0.1:{self.inference.port}/status"
        self.cfg.listen_host, self.cfg.listen_port = "127.0.0.1", 0
        write_prayer_store(Path(self.cfg.prayer_store), [("p1", "Lord, you are my shepherd; I shall not want. Thank you for green pastures.", 1.0)])
        conn = connect(self.cfg.db_path)
        records, sel = read_prayers(self.cfg.prayer_store)
        import_family(conn, "prayer", records, selection=sel, source_path=self.cfg.prayer_store)
        conn.close()
        self.server = make_server(self.cfg)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.inference.stop()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read())

    def _post(self, path, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def _ws(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        client_handshake(sock, "127.0.0.1", "/ws")
        ws = ClientWebSocket(sock)
        hello = json.loads(ws.receive())
        self.assertEqual(hello["type"], "session")
        return ws, hello

    def test_http_search_cite_status(self):
        self.assertTrue(self._get("/healthz")["ok"])
        hits = self._get("/search?q=green%20pastures")["hits"]
        self.assertEqual(len(hits), 1)
        cite = self._get(f"/citations/{hits[0]['cite_id']}")
        self.assertTrue(cite["verified"])
        status = self._get("/status")
        self.assertTrue(status["inference"]["ready"])
        self.assertEqual(status["counts"]["prayer"]["current"], 1)
        self.assertFalse(status["allocator"]["available"])  # stub path does not exist here

    def test_turn_streams_deltas_with_citations(self):
        ws, hello = self._ws()
        ws.send_json({"type": "hello", "mode": {"quiet": False, "activity": "idle"}})
        self.assertEqual(json.loads(ws.receive())["type"], "hello_ack")
        ws.send_json({"type": "partial_transcript", "turn_id": "t1", "text": "what does"})
        self.assertEqual(json.loads(ws.receive())["type"], "partial_ack")
        ws.send_json({"type": "final_transcript", "turn_id": "t1", "text": "What did I pray about green pastures?"})
        events = []
        while True:
            ev = json.loads(ws.receive())
            events.append(ev)
            if ev["type"] in ("assistant_done", "error"):
                break
        self.assertEqual(events[0]["type"], "turn_accepted")
        deltas = [e for e in events if e["type"] == "assistant_text_delta"]
        self.assertGreater(len(deltas), 3)
        self.assertEqual([d["seq"] for d in deltas], list(range(1, len(deltas) + 1)))
        done = events[-1]
        self.assertEqual(done["type"], "assistant_done")
        self.assertIn("shepherd", done["text"])
        self.assertEqual(done["citations"][0]["source_family"], "prayer")
        self.assertIsNotNone(done["first_delta_ms"])
        self.assertEqual(done["usage"]["completion_tokens"], len(self.inference.reply.split()))
        # partials never triggered a model call; only the final did
        self.assertEqual(len(self.inference.requests), 1)
        self.assertIn("PRAYER", self.inference.requests[0]["messages"][1]["content"])
        # duplicate final for the same turn id is refused
        ws.send_json({"type": "final_transcript", "turn_id": "t1", "text": "again"})
        self.assertEqual(json.loads(ws.receive())["type"], "duplicate_turn")
        self.assertEqual(len(self.inference.requests), 1)
        ws.close()

    def test_cancel_stops_stream_and_closes_upstream(self):
        self.inference.reply = " ".join(["word"] * 60)
        ws, _ = self._ws()
        ws.send_json({"type": "final_transcript", "turn_id": "t2", "text": "long answer please"})
        self.assertEqual(json.loads(ws.receive())["type"], "turn_accepted")
        first = json.loads(ws.receive())
        self.assertEqual(first["type"], "assistant_text_delta")
        ws.send_json({"type": "cancel", "turn_id": "t2"})
        types = []
        while True:
            ev = json.loads(ws.receive())
            types.append(ev["type"])
            if ev["type"] in ("assistant_cancelled", "assistant_done", "error"):
                break
        self.assertIn("cancel_ack", types)
        self.assertEqual(types[-1], "assistant_cancelled")
        self.assertLess(types.count("assistant_text_delta"), 50)
        time.sleep(0.5)
        self.assertTrue(self.inference.requests[0].get("client_disconnected"))
        row = self._get("/turns/t2")
        self.assertEqual(row["status"], "cancelled")
        ws.close()

    def test_disconnect_settles_turn(self):
        self.inference.reply = " ".join(["word"] * 80)
        ws, _ = self._ws()
        ws.send_json({"type": "final_transcript", "turn_id": "t3", "text": "long answer please"})
        json.loads(ws.receive())
        json.loads(ws.receive())
        ws.sock.close()
        time.sleep(1.5)
        self.assertTrue(self.inference.requests[0].get("client_disconnected"))
        self.assertEqual(self._get("/turns/t3")["status"], "cancelled")

    def test_inference_error_is_reported_not_faked(self):
        self.inference.status = 503
        result = self._post("/turns", {"turn_id": "t4", "text": "hello"})
        self.assertEqual(result["status"], "error")
        self.assertIn("503", result["error"])
        self.assertIn("insufficient_memory", result["error"])

    def test_binary_frames_rejected(self):
        ws, _ = self._ws()
        ws._send_frame(0x2, b"\x00\x01")
        ev = json.loads(ws.receive())
        self.assertEqual(ev["type"], "error")
        self.assertIn("PCM", ev["message"])
        ws.close()


if __name__ == "__main__":
    unittest.main()
