"""Cancellation and error-ordering contract for the streaming client, with owners that stall."""

import http.client
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from voice_portal.reasoning import ReasoningError, stream_chat


class StallingOwner:
    """A fake owner that can withhold headers, withhold the next SSE line, or answer 503 late.

    It records whether the client connection was observed closed (write fails / read returns b'').
    """

    def __init__(self, *, headers_delay=0.0, line_delay=0.0, error_after=None, send_usage=True):
        self.headers_delay = headers_delay
        self.line_delay = line_delay
        self.error_after = error_after
        self.send_usage = send_usage
        self.requests = 0
        self.client_closed = threading.Event()
        self.handler_done = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _peer_closed(self) -> bool:
                # A closed peer shows up as readable with no data (FIN) on a non-blocking peek.
                self.connection.settimeout(0.05)
                try:
                    data = self.connection.recv(1, socket.MSG_PEEK)
                    return data == b""
                except socket.timeout:
                    return False
                except OSError:
                    return True

            def do_POST(self):
                owner.requests += 1
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                try:
                    # stall before headers (cold start)
                    end = time.monotonic() + owner.headers_delay
                    while time.monotonic() < end:
                        if self._peer_closed():
                            owner.client_closed.set()
                            return
                        time.sleep(0.02)
                    if owner.error_after is not None:
                        body = json.dumps({"error": {"message": "queue_timeout", "code": "queue_timeout"}}).encode()
                        self.send_response(503)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        self.wfile.flush()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()

                    def chunk(data: bytes):
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()

                    chunk(b"data: " + json.dumps({"choices": [{"delta": {"content": "First"}, "finish_reason": None}]}).encode() + b"\n\n")
                    # stall between lines
                    end = time.monotonic() + owner.line_delay
                    while time.monotonic() < end:
                        if self._peer_closed():
                            owner.client_closed.set()
                            return
                        time.sleep(0.02)
                    chunk(b"data: " + json.dumps({"choices": [{"delta": {"content": " second"}, "finish_reason": "stop"}], **({"usage": {"prompt_tokens": 3, "completion_tokens": 2}} if owner.send_usage else {})}).encode() + b"\n\n")
                    chunk(b"data: [DONE]\n\n")
                    chunk(b"")
                except (BrokenPipeError, ConnectionResetError):
                    owner.client_closed.set()
                finally:
                    owner.handler_done.set()

        return H


class RawFramingOwner:
    """A raw-socket owner that answers with HTTP/1.0 close-delimited SSE or HTTP/1.1 `Connection: close`,
    sends one delta, then stalls. It records remote EOF/reset on its side of the connection."""

    def __init__(self, framing="http10"):
        self.framing = framing
        self.remote_eof = threading.Event()
        self.handler_done = threading.Event()
        self.requests = 0
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.stop_flag = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.stop_flag.set()
        try:
            self.srv.close()
        except OSError:
            pass

    def _serve(self):
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        self.requests += 1
        try:
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                part = conn.recv(4096)
                if not part:
                    self.remote_eof.set()
                    return
                data += part
            head, _, body = data.partition(b"\r\n\r\n")
            length = int(next((l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")), b"0"))
            while len(body) < length:
                body += conn.recv(4096)
            delta = b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
            if self.framing == "http10":
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\n\r\n" + delta)
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n" + delta)
            # stall: wait for the client to go away (EOF or reset) or for the owner to be stopped
            conn.settimeout(0.1)
            while not self.stop_flag.is_set():
                try:
                    chunk = conn.recv(1)
                    if chunk == b"":
                        self.remote_eof.set()
                        return
                except socket.timeout:
                    continue
                except OSError:
                    self.remote_eof.set()
                    return
        finally:
            self.handler_done.set()
            try:
                conn.close()
            except OSError:
                pass


def live_reader_threads():
    return [t for t in threading.enumerate() if t.name == "reasoning-upstream" and t.is_alive()]


def collect(gen):
    out = []
    for item in gen:
        out.append(item)
    return out


class CancelTests(unittest.TestCase):
    def _run(self, owner, cancel, cancel_after=None, **kw):
        if cancel_after is not None:
            threading.Timer(cancel_after, cancel.set).start()
        t0 = time.monotonic()
        items = collect(stream_chat(owner.base_url, "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=10, tick_seconds=0.05, **kw))
        return items, time.monotonic() - t0

    def test_cancel_before_headers_settles_fast_and_closes_upstream(self):
        owner = StallingOwner(headers_delay=5.0)
        try:
            cancel = threading.Event()
            items, elapsed = self._run(owner, cancel, cancel_after=0.3)
            self.assertEqual(items[-1]["cancelled"], True)
            self.assertIsNone(items[-1]["usage"])
            self.assertLess(elapsed, 1.5, "cancel must settle without waiting for headers")
            self.assertTrue(owner.client_closed.wait(2.0), "owner must observe the client disconnect")
            self.assertEqual(owner.requests, 1)
        finally:
            owner.stop()

    def test_cancel_between_sse_lines_settles_fast_and_closes_upstream(self):
        owner = StallingOwner(line_delay=5.0)
        try:
            cancel = threading.Event()
            items, elapsed = self._run(owner, cancel, cancel_after=0.5)
            self.assertEqual([i.get("delta") for i in items if "delta" in i], ["First"])
            self.assertTrue(items[-1]["cancelled"])
            self.assertIsNone(items[-1]["usage"], "no usage was sent before the cancel: stays unknown")
            self.assertLess(elapsed, 1.5)
            self.assertTrue(owner.client_closed.wait(2.0))
        finally:
            owner.stop()

    def test_already_cancelled_sends_nothing(self):
        owner = StallingOwner()
        try:
            cancel = threading.Event()
            cancel.set()
            items, elapsed = self._run(owner, cancel)
            self.assertTrue(items[-1]["cancelled"])
            self.assertFalse(items[-1]["sent"])
            self.assertEqual(owner.requests, 0)
            self.assertLess(elapsed, 0.5)
        finally:
            owner.stop()

    def test_error_before_cancel_is_an_error(self):
        owner = StallingOwner(headers_delay=0.2, error_after=True)
        try:
            cancel = threading.Event()
            threading.Timer(2.0, cancel.set).start()
            with self.assertRaises(ReasoningError) as ctx:
                collect(stream_chat(owner.base_url, "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=10, tick_seconds=0.05))
            self.assertEqual(ctx.exception.status, 503)
            self.assertIn("queue_timeout", ctx.exception.body)
        finally:
            owner.stop()

    def test_cancel_before_late_error_is_cancelled_not_error(self):
        owner = StallingOwner(headers_delay=3.0, error_after=True)
        try:
            cancel = threading.Event()
            items, elapsed = self._run(owner, cancel, cancel_after=0.3)
            self.assertTrue(items[-1]["cancelled"])
            self.assertLess(elapsed, 1.5)
        finally:
            owner.stop()

    def test_completion_without_usage_keeps_usage_unknown(self):
        owner = StallingOwner(send_usage=False)
        try:
            items, _ = self._run(owner, threading.Event())
            self.assertEqual("".join(i.get("delta", "") for i in items), "First second")
            self.assertFalse(items[-1]["cancelled"])
            self.assertIsNone(items[-1]["usage"])
            self.assertEqual(items[-1]["finish_reason"], "stop")
        finally:
            owner.stop()

    def test_completion_with_usage(self):
        owner = StallingOwner()
        try:
            items, _ = self._run(owner, threading.Event())
            self.assertEqual(items[-1]["usage"], {"prompt_tokens": 3, "completion_tokens": 2})
        finally:
            owner.stop()

    def _framing_case(self, framing):
        owner = RawFramingOwner(framing)
        try:
            cancel = threading.Event()
            gen = stream_chat(owner.base_url, "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=10, tick_seconds=0.05)
            first = next(gen)
            self.assertEqual(first, {"delta": "Hello"})
            t0 = time.monotonic()
            cancel.set()
            rest = list(gen)
            elapsed = time.monotonic() - t0
            self.assertTrue(rest[-1]["cancelled"])
            self.assertTrue(rest[-1]["upstream_settled"], "reader thread must have terminated")
            self.assertLess(elapsed, 1.0)
            self.assertTrue(owner.remote_eof.wait(1.0), "owner must observe remote EOF/reset on its socket")
            self.assertEqual(live_reader_threads(), [])
            self.assertIsNone(rest[-1]["usage"])
        finally:
            owner.stop()

    def test_cancel_with_http10_close_delimited_response_tears_down_real_socket(self):
        self._framing_case("http10")

    def test_cancel_with_connection_close_response_tears_down_real_socket(self):
        self._framing_case("close")

    def test_cancel_during_connect_sends_no_request(self):
        owner = StallingOwner()
        try:
            cancel = threading.Event()
            gate = threading.Event()

            def slow_connect():  # runs on the reader thread inside the connect window
                gate.wait(5)

            gen = stream_chat(owner.base_url, "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=10, tick_seconds=0.05, _connect_hook=slow_connect)
            # Start the request on the reader thread, then cancel while it is still "connecting".
            threading.Timer(0.2, cancel.set).start()
            threading.Timer(0.6, gate.set).start()
            items = collect(gen)
            self.assertTrue(items[-1]["cancelled"])
            self.assertFalse(items[-1]["sent"], "no request bytes may be written after the cancel")
            self.assertTrue(items[-1]["upstream_settled"])
            time.sleep(0.3)
            self.assertEqual(owner.requests, 0)
            self.assertEqual(live_reader_threads(), [])
        finally:
            owner.stop()

    def test_cancel_observed_before_queued_line_wins(self):
        """The owner's next line and [DONE] are already queued when the cancel lands: no delta, no success.
        The same post-wait precedence check applies to a queued owner error."""
        owner = StallingOwner(line_delay=0.3)
        try:
            cancel = threading.Event()
            gen = stream_chat(owner.base_url, "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=10, tick_seconds=0.05)
            self.assertEqual(next(gen), {"delta": "First"})
            time.sleep(0.6)  # owner has sent " second", usage and [DONE]; they sit in the queue unread
            cancel.set()
            rest = list(gen)
            self.assertEqual([i for i in rest if "delta" in i], [], "queued delta must not surface after the cancel")
            self.assertTrue(rest[-1]["cancelled"])
            self.assertIsNone(rest[-1]["usage"], "usage that was never observed stays unknown")
        finally:
            owner.stop()

    def test_unreachable_owner_is_an_error_not_a_hang(self):
        cancel = threading.Event()
        t0 = time.monotonic()
        with self.assertRaises(ReasoningError):
            collect(stream_chat("http://127.0.0.1:1/v1", "m", [{"role": "user", "content": "hi"}], cancel=cancel, timeout=5, tick_seconds=0.05))
        self.assertLess(time.monotonic() - t0, 3)


if __name__ == "__main__":
    unittest.main()
