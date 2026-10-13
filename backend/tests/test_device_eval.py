"""scripts/device_eval: WDA attach without relaunch, UI smoke evidence against a scripted fake WDA,
finite play/record orchestration with a fake process runner, WAV measurements and envelope
alignment on synthetic signals, phone segment accounting, fixture excerpts (real ffmpeg when
present), and the CLI receipts. Nothing here talks to a phone, a Mac, the desktop or Spark-B; a
passing run is evidence about the harness, not about the hardware."""

import base64
import hashlib
import http.server
import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from array import array
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from device_eval import audio, cli, fixture, phone, remote_audio, scoring, ui, wda  # noqa: E402
from device_eval.receipt import Receipt  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"


# ----------------------------------------------------------------------------- fake WDA server

class FakeWdaState:
    def __init__(self):
        self.locked = False
        self.app_state = 4
        self.prayer_label = "Begin prayer"
        self.quiet_label = "Quiet"
        self.keyboard = False
        self.typed = ""
        self.tab = "Portal"
        self.alert = None
        self.sessions = []
        self.activated = []
        self.sent = 0
        self.prayer_taps = 0
        self.alert_actions = []
        self.elements = {}  # id -> locator
        self.backend_url = "http://100.90.105.30:8765"
        self.applied = []
        self.focused = None
        self.apply_clicks_allowed = 99
        self.placeholder = "Type a message"
        self.fail_locate = set()  # identifiers whose lookup returns HTTP 500
        self.fail_source_when_quiet_toggled = False

    def source(self):
        if self.tab == "Portal":
            body = (
                f'<XCUIElementTypeOther type="XCUIElementTypeOther" name="statusHeader" label="status"/>'
                f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="prayerButton" label="{self.prayer_label}"/>'
                f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="quietButton" label="{self.quiet_label}"/>'
                f'<XCUIElementTypeTextField type="XCUIElementTypeTextField" name="typedInput" value="{self.typed}"/>'
                f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="sendButton" label="Send"/>'
            )
            if self.keyboard:
                body += '<XCUIElementTypeKeyboard type="XCUIElementTypeKeyboard" visible="true"><XCUIElementTypeButton name="keyboardDone" label="Done"/></XCUIElementTypeKeyboard>'
        else:
            body = (
                '<XCUIElementTypeNavigationBar type="XCUIElementTypeNavigationBar" name="Diagnostics"/>'
                '<XCUIElementTypeTextField type="XCUIElementTypeTextField" name="backendURLField" value="http://x"/>'
            )
        tabs = "".join(f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="{t}" label="{t}"/>' for t in ui.TABS)
        return f'<?xml version="1.0"?><XCUIElementTypeApplication name="RLM-ADK">{body}<XCUIElementTypeTabBar>{tabs}</XCUIElementTypeTabBar></XCUIElementTypeApplication>'

    def locate(self, using, value):
        present = set()
        if self.app_state != 4:
            return None
        if self.tab == "Portal":
            present |= {"statusHeader", "prayerButton", "quietButton", "typedInput", "sendButton"}
            if self.keyboard:
                present.add("keyboardDone")
        else:
            present |= {"backendURLField", "keyboardDoneDiagnostics"}
            if self.apply_clicks_allowed > 0:
                present.add("Apply")
        present |= set(ui.TABS)
        if using == "accessibility id" and value in present:
            return value
        if using == "class chain" and "XCUIElementTypeNavigationBar" in value and self.tab == "Diagnostics" and '"Diagnostics"' in value:
            return "nav:Diagnostics"
        if using == "predicate string":
            for t in ui.TABS:
                if f"label == '{t}'" in value:
                    return t
        return None

    def click(self, ident):
        if ident == "typedInput":
            self.keyboard = True
        elif ident == "keyboardDone":
            self.keyboard = False
        elif ident == "quietButton":
            self.keyboard = False
            self.quiet_label = "Wake" if self.quiet_label == "Quiet" else "Quiet"
        elif ident == "sendButton":
            self.sent += 1
            self.keyboard = False
        elif ident == "prayerButton":
            self.prayer_taps += 1
            self.prayer_label = "End prayer" if self.prayer_label == "Begin prayer" else "Begin prayer"
        elif ident in ui.TABS:
            self.tab = ident
            self.keyboard = False
            self.focused = None
        elif ident == "backendURLField":
            self.focused = "backend"
            self.keyboard = True
        elif ident == "Apply":
            self.apply_clicks_allowed -= 1
            self.applied.append(self.backend_url)
            self.keyboard = False
            self.focused = None


def make_handler(state: FakeWdaState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, value, session=None):
            body = {"value": value}
            if session:
                body["sessionId"] = session
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            p = self.path
            if p == "/status":
                return self._send(200, {"ready": True, "build": {"version": "fake"}, "os": {"version": "26.6.2"}})
            if p == "/wda/locked":
                return self._send(200, state.locked)
            if p.endswith("/source"):
                if state.fail_source_when_quiet_toggled and state.quiet_label == "Wake":
                    return self._send(500, {"error": "unknown error", "message": "simulated WDA failure after the quiet toggle"})
                return self._send(200, state.source())
            if p.endswith("/screenshot"):
                return self._send(200, base64.b64encode(PNG).decode())
            if p.endswith("/alert/text"):
                if state.alert:
                    return self._send(200, state.alert)
                return self._send(404, {"error": "no such alert", "message": "no alert"})
            if "/element/" in p and "/attribute/" in p:
                eid = p.split("/element/")[1].split("/")[0]
                attr = p.rsplit("/", 1)[1]
                ident = state.elements.get(eid)
                if attr == "label":
                    return self._send(200, {"prayerButton": state.prayer_label, "quietButton": state.quiet_label}.get(ident, ident))
                if attr == "value":
                    return self._send(200, {"typedInput": state.typed or state.placeholder, "backendURLField": state.backend_url}.get(ident, ""))
                if attr == "placeholderValue":
                    return self._send(200, state.placeholder if ident == "typedInput" else None)
                return self._send(200, None)
            if p.endswith("/wda/activeAppInfo"):
                return self._send(200, {"bundleId": cli.BUNDLE, "pid": 1403})
            return self._send(404, {"error": "unknown command", "message": p})

        def do_POST(self):
            p = self.path
            body = self._body()
            if p == "/session":
                state.sessions.append(body)
                return self._send(200, {"sessionId": "S1", "capabilities": {}}, session="S1")
            if p.endswith("/wda/apps/state"):
                return self._send(200, state.app_state)
            if p.endswith("/wda/apps/activate"):
                state.activated.append(body.get("bundleId"))
                if state.app_state >= 2:
                    state.app_state = 4
                else:
                    state.app_state = 4  # a real WDA would launch here; attach() must never reach this branch
                return self._send(200, None)
            if p.endswith("/element"):
                ident = state.locate(body.get("using"), body.get("value"))
                if ident in state.fail_locate or (ident is None and body.get("value") in state.fail_locate):
                    return self._send(500, {"error": "unknown error", "message": "simulated WDA failure"})
                if ident is None:
                    return self._send(404, {"error": "no such element", "message": body.get("value")})
                eid = f"E{len(state.elements) + 1}"
                state.elements[eid] = ident
                return self._send(200, {"ELEMENT": eid, wda.W3C_ELEMENT_KEY: eid})
            if "/element/" in p and p.endswith("/click"):
                eid = p.split("/element/")[1].split("/")[0]
                state.click(state.elements[eid])
                return self._send(200, None)
            if p.endswith("/wda/keys"):
                if state.keyboard and state.focused == "backend":
                    state.backend_url += "".join(body.get("value") or [])
                elif state.keyboard:
                    state.typed += "".join(body.get("value") or [])
                return self._send(200, None)
            if "/element/" in p and p.endswith("/clear"):
                eid = p.split("/element/")[1].split("/")[0]
                if state.elements[eid] == "backendURLField":
                    state.backend_url = ""
                elif state.elements[eid] == "typedInput":
                    state.typed = ""
                return self._send(200, None)
            if "/alert/" in p:
                state.alert_actions.append(p)
                return self._send(200, None)
            return self._send(404, {"error": "unknown command", "message": p})

        def do_DELETE(self):
            return self._send(200, None)

    return Handler


class FakeWda:
    """Context manager: scripted WDA on an ephemeral localhost port."""

    def __init__(self, **state):
        self.state = FakeWdaState()
        for k, v in state.items():
            setattr(self.state, k, v)

    def __enter__(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class WdaAttachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_attach_never_sends_bundle_or_launch_capabilities(self):
        with FakeWda(app_state=3) as f:
            client = wda.Wda(f.url)
            rc = Receipt("test")
            sid = ui.attach(client, cli.BUNDLE, rc, self.tmp)
            self.assertEqual(sid, "S1")
            caps = f.state.sessions[0]["capabilities"]["alwaysMatch"]
            self.assertNotIn("bundleId", caps)
            self.assertIs(caps["shouldTerminateApp"], False)
            self.assertIs(caps["forceAppLaunch"], False)
            self.assertEqual(f.state.activated, [cli.BUNDLE])
            self.assertEqual(rc.finish(), "pass", rc.data["reasons"])
            names = {a["name"] for a in rc.data["actions"]}
            self.assertIn("app_state_before", names)
            self.assertTrue(any(e["kind"] == "screenshot" for e in rc.data["evidence"]))

    def test_attach_refuses_to_activate_an_app_that_is_not_running(self):
        with FakeWda(app_state=1) as f:
            rc = Receipt("test")
            ui.attach(wda.Wda(f.url), cli.BUNDLE, rc, self.tmp)
            self.assertEqual(f.state.activated, [])
            self.assertEqual(rc.finish(), "fail")
            self.assertTrue(any("was_running_before" in c["name"] and not c["ok"] for c in rc.data["checks"]))

    def test_client_refuses_bundle_id_capability(self):
        with self.assertRaises(wda.WdaError):
            wda.Wda("http://127.0.0.1:1").attach_session({"bundleId": cli.BUNDLE})
        with self.assertRaises(wda.WdaError):
            wda.Wda("http://127.0.0.1:1").attach_session({"forceAppLaunch": True})

    def test_preflight_blocked_when_locked_and_when_prayer_open(self):
        with FakeWda(locked=True) as f:
            with self.assertRaises(ui.Blocked):
                ui.preflight(wda.Wda(f.url), cli.BUNDLE, Receipt("t"), self.tmp)
            self.assertEqual(f.state.sessions, [], "no session while locked")
        with FakeWda(prayer_label="End prayer") as f:
            with self.assertRaises(ui.Blocked):
                ui.preflight(wda.Wda(f.url), cli.BUNDLE, Receipt("t"), self.tmp)
            self.assertEqual(f.state.prayer_taps, 0)

    def test_preflight_pass_records_labels(self):
        with FakeWda() as f:
            rc = Receipt("t")
            res = ui.preflight(wda.Wda(f.url), cli.BUNDLE, rc, self.tmp)
            self.assertEqual(res["labels"], {"prayer": "Begin prayer", "quiet": "Quiet"})
            self.assertEqual(rc.finish(), "pass", rc.data["reasons"])


class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_smoke_passes_with_evidence_and_restores_quiet(self):
        with FakeWda() as f:
            client = wda.Wda(f.url, session_id="S1")
            rc = Receipt("t")
            res = ui.smoke(client, cli.BUNDLE, rc, self.tmp, type_text="hello there")
            self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
            self.assertEqual(f.state.sent, 0)
            self.assertEqual(f.state.prayer_taps, 0)
            self.assertEqual(f.state.quiet_label, "Quiet")
            self.assertEqual(f.state.typed, "", "own draft cleared before returning")
            self.assertTrue(any(a["name"] == "type_text" and a["text"] == "hello there" for a in rc.data["actions"]))
            self.assertFalse(f.state.keyboard)
            self.assertEqual(f.state.tab, "Portal")
            self.assertNotIn("accessibility id=sendButton", client.clicked_locators())
            tags = {e["tag"] for e in rc.data["evidence"] if e["kind"] == "screenshot"}
            self.assertTrue({"01-portal", "02-keyboard", "03-after-done", "04-diagnostics", "06-quiet-toggled", "07-quiet-restored"} <= tags)
            self.assertTrue(res["quiet_restored"])

    def test_smoke_clears_only_its_own_draft(self):
        with FakeWda() as f:
            rc = Receipt("t")
            ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp, type_text="draft by harness")
            self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
            self.assertEqual(f.state.typed, "", "own draft cleared before returning")
            self.assertFalse(f.state.keyboard)
            names = {c["name"]: c["ok"] for c in rc.data["checks"]}
            self.assertTrue(names["typed_value_matches"] and names["own_typed_draft_cleared"] and names["quiet_restored"])

    def test_smoke_blocks_on_existing_draft_when_asked_to_type(self):
        with FakeWda(typed="user's unsent words") as f:
            rc = Receipt("t")
            client = wda.Wda(f.url, session_id="S1")
            with self.assertRaises(ui.Blocked):
                ui.smoke(client, cli.BUNDLE, rc, self.tmp, type_text="hello")
            self.assertEqual(f.state.typed, "user's unsent words")
            self.assertNotIn("accessibility id=typedInput", client.clicked_locators(), "field never tapped")
            self.assertFalse(any("cleared" in e for e in client.log))

    def test_smoke_preserves_existing_draft_when_not_typing(self):
        with FakeWda(typed="keep me") as f:
            rc = Receipt("t")
            ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp)
            self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
            self.assertEqual(f.state.typed, "keep me")
            self.assertTrue(any(c["name"] == "preexisting_draft_preserved" and c["ok"] for c in rc.data["checks"]))

    def test_placeholder_is_not_a_draft(self):
        with FakeWda() as f:
            client = wda.Wda(f.url, session_id="S1")
            field = client.by_id("typedInput")
            self.assertEqual(client.value(field), "Type a message")
            self.assertEqual(client.draft_text(field), "")

    def test_error_after_typing_still_clears_draft_and_dismisses_keyboard(self):
        with FakeWda() as f:
            f.state.fail_locate = {"Diagnostics"}  # tab lookup raises mid-sequence, after typing
            rc = Receipt("t")
            with self.assertRaises(wda.WdaError):
                ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp, type_text="half done")
            self.assertEqual(f.state.typed, "", "own draft cleared despite the failure")
            self.assertFalse(f.state.keyboard)
            self.assertEqual(f.state.quiet_label, "Quiet")
            self.assertEqual(f.state.sent, 0)
            names = {c["name"]: c["ok"] for c in rc.data["checks"]}
            self.assertTrue(names["own_typed_draft_cleared"] and names["quiet_restored"] and names["send_prayer_backend_never_tapped"])
            self.assertEqual(rc.finish(), "fail")

    def test_error_after_quiet_toggle_restores_quiet(self):
        with FakeWda(quiet_label="Quiet") as f:
            f.state.fail_source_when_quiet_toggled = True  # the snapshot right after the toggle raises
            rc = Receipt("t")
            with self.assertRaises(wda.WdaError):
                ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp, type_text="x")
            self.assertEqual(f.state.quiet_label, "Quiet", "quiet put back although the toggle step blew up")
            self.assertEqual(f.state.typed, "")
            self.assertTrue(any(a["name"] == "restore_quiet" and a["from_label"] == "Wake" for a in rc.data["actions"]))
            self.assertTrue(any(c["name"] == "quiet_restored" and c["ok"] for c in rc.data["checks"]))
            self.assertEqual(f.state.prayer_taps, 0)

    def test_smoke_without_text_types_nothing(self):
        with FakeWda(quiet_label="Wake") as f:
            rc = Receipt("t")
            ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp)
            self.assertEqual(f.state.typed, "")
            self.assertEqual(f.state.quiet_label, "Wake", "quiet flag restored to its original (set) state")
            self.assertEqual(rc.finish(), "pass", rc.data["reasons"])

    def test_smoke_blocked_by_alert_and_never_accepts_it(self):
        with FakeWda(alert="“RLM-ADK” Would Like to Access the Microphone") as f:
            with self.assertRaises(ui.Blocked):
                ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, Receipt("t"), self.tmp)
            self.assertEqual(f.state.alert_actions, [])

    def test_smoke_blocked_by_open_prayer(self):
        with FakeWda(prayer_label="End prayer") as f:
            with self.assertRaises(ui.Blocked):
                ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, Receipt("t"), self.tmp)
            self.assertEqual(f.state.prayer_taps, 0)

    def test_smoke_fails_when_keyboard_does_not_appear(self):
        with FakeWda() as f:
            original = f.state.click

            def no_keyboard(ident):
                original(ident)
                if ident == "typedInput":
                    f.state.keyboard = False
            f.state.click = no_keyboard
            rc = Receipt("t")
            ui.smoke(wda.Wda(f.url, session_id="S1"), cli.BUNDLE, rc, self.tmp, step_timeout=0.3)
            self.assertEqual(rc.finish(), "fail")
            self.assertIn("check failed: keyboard_appeared", "\n".join(rc.data["reasons"]))


# ----------------------------------------------------------------------------- synthetic audio

def speech_like(rate, seconds, pattern, level=0.3, f0=220.0, start=0.0):
    """Bursts of a harmonic tone following an on/off pattern (0.25 s per slot) after ``start`` seconds."""
    n = int(rate * seconds)
    out = array("h", [0] * n)
    slot = int(rate * 0.25)
    offset = int(rate * start)
    amp = 32767 * level
    for k, on in enumerate(pattern):
        if not on:
            continue
        for i in range(slot):
            t = k * slot + i
            idx = offset + t
            if 0 <= idx < n:
                env = min(1.0, i / (rate * 0.02), (slot - i) / (rate * 0.02))
                v = amp * env * (math.sin(2 * math.pi * f0 * i / rate) + 0.5 * math.sin(2 * math.pi * 2 * f0 * i / rate)) / 1.5
                out[idx] = int(max(-32767, min(32767, v)))
    return out


def add_noise(samples, level_dbfs, seed=1):
    import random
    rng = random.Random(seed)
    amp = 32767 * 10 ** (level_dbfs / 20)
    return array("h", (int(max(-32767, min(32767, v + rng.uniform(-amp, amp)))) for v in samples))


PATTERN = [1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 0]


class AudioMeasureTests(unittest.TestCase):
    def test_alignment_finds_offset_across_sample_rates(self):
        src = speech_like(48000, 5.0, PATTERN)
        room = add_noise(speech_like(44100, 8.0, PATTERN, level=0.08, start=1.5), -55)
        a = audio.align(src, 48000, room, 44100)
        self.assertTrue(a["credible"], a)
        self.assertAlmostEqual(a["lag_s"], 1.5, delta=0.06)

    def test_flat_room_is_not_evidence(self):
        src = speech_like(48000, 5.0, PATTERN)
        silent = add_noise(array("h", [0] * (44100 * 6)), -75)
        m = audio.measure(silent, 44100)
        self.assertIsNotNone(audio.flat_or_missing(m))
        a = audio.align(src, 48000, silent, 44100)
        self.assertFalse(a["credible"])
        self.assertIsNone(audio.flat_or_missing(audio.measure(src, 48000)))

    def test_clipping_and_levels(self):
        clipped = array("h", [32767, -32767] * 1000 + [0] * 46000)
        m = audio.measure(clipped, 48000)
        self.assertGreater(m["clipping_ratio"], 0.03)
        self.assertEqual(m["peak"], 32767)
        quiet = audio.measure(speech_like(48000, 2.0, [1, 1, 1, 1, 0, 0, 0, 0], level=0.1), 48000)
        self.assertEqual(quiet["clipping_ratio"], 0.0)
        self.assertGreater(quiet["frame_p90_p10_range_db"], 20)
        self.assertNotIn("speech_level_dbfs", quiet)
        self.assertNotIn("noise_floor_dbfs", quiet)

    def test_alignment_never_reports_an_incredible_lag(self):
        src = speech_like(48000, 5.0, PATTERN)
        noise = add_noise(array("h", [0] * (44100 * 8)), -40)
        a = audio.align(src, 48000, noise, 44100)
        self.assertFalse(a["credible"])
        self.assertIsNone(a["lag_s"])
        self.assertIsNotNone(a["rejected_candidate_lag_s"])
        self.assertEqual(a["search_lag_s"][0], 0.0, "negative lags are never searched")
        short = audio.align(src, 48000, speech_like(44100, 1.0, [1, 0, 1, 0]), 44100, min_lag_s=5.0)
        self.assertFalse(short["credible"])
        self.assertIsNone(short["lag_s"])

    def test_wav_round_trip_and_stereo_mix(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            mono = speech_like(16000, 1.0, [1, 0, 1, 0])
            audio.write_mono(tmp / "m.wav", mono, 16000)
            info, back = audio.read_mono(tmp / "m.wav")
            self.assertEqual((info.rate, info.channels, info.sampwidth, info.frames), (16000, 1, 2, len(mono)))
            self.assertEqual(back, mono)
            import wave
            with wave.open(str(tmp / "s.wav"), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(array("h", [100, 300, -100, -300]).tobytes())
            info, mixed = audio.read_mono(tmp / "s.wav")
            self.assertEqual(list(mixed), [200, -200])
            (tmp / "bad.wav").write_bytes(b"RIFF????WAVEjunk")
            with self.assertRaises(audio.WavError):
                audio.read_mono(tmp / "bad.wav")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- phone segments

APPLE = phone.APPLE_EPOCH_OFFSET


def write_capture(root: Path, capture_id: str, seg_samples, rate=48000, started_unix=1_790_000_000.0, tamper=None, first_index=0, manifest_prefix_missing=0):
    """Segment files first_index.. on disk; the manifest also lists ``manifest_prefix_missing``
    earlier closed segments that were never pulled (an ambient capture running for hours)."""
    d = root / "captures" / capture_id
    d.mkdir(parents=True)
    segs = []
    t = started_unix - 60.0 * manifest_prefix_missing
    for i in range(manifest_prefix_missing):
        segs.append({"index": i, "filename": f"{i:08d}.wav", "bytes": 5764, "sha256": "ab" * 32, "startedAt": t - APPLE, "endedAt": t + 60 - APPLE})
        t += 60
    for k, samples in enumerate(seg_samples):
        i = first_index + k
        name = f"{i:08d}.wav"
        audio.write_mono(d / name, samples, rate)
        data = (d / name).read_bytes()
        dur = len(samples) / rate
        segs.append({"index": i, "filename": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "startedAt": t - APPLE, "endedAt": t + dur - APPLE})
        t += dur
    manifest = {"captureId": capture_id, "kind": "ambient", "createdAt": started_unix - APPLE, "segments": segs, "gaps": [], "status": {"savedLocally": {}}, "expectedSegmentCount": None}
    if tamper:
        tamper(manifest, d)
    (root / "captures.json").write_text(json.dumps([manifest]))
    return d


class PhoneSegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.segs = [speech_like(48000, 1.0, [1, 0, 1, 0]), speech_like(48000, 0.5, [0, 1])]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def verify(self, capture_id="ambient-1-abc"):
        caps = phone.load_captures(self.tmp / "captures.json")
        return phone.verify_segments(phone.find_capture(caps, capture_id), self.tmp / "captures" / capture_id)

    def test_exact_accounting_passes_and_concatenates_in_order(self):
        write_capture(self.tmp, "ambient-1-abc", self.segs)
        rep = self.verify()
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["total_frames"], 72000)
        self.assertEqual(rep["segments"][0]["duration_delta_s"], 0.0)
        self.assertAlmostEqual(rep["created_at_unix"], 1_790_000_000.0)
        info, samples = phone.concatenate(rep)
        self.assertEqual(len(samples), 72000)
        self.assertEqual(samples[:48000], self.segs[0])

    def test_hash_mismatch_fails(self):
        def tamper(m, d):
            m["segments"][1]["sha256"] = "0" * 64
        write_capture(self.tmp, "ambient-1-abc", self.segs, tamper=tamper)
        rep = self.verify()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("sha256 mismatch" in e for e in rep["errors"]))
        with self.assertRaises(audio.WavError):
            phone.concatenate(rep)

    def test_non_contiguous_missing_and_unaccounted(self):
        def tamper(m, d):
            m["segments"][1]["index"] = 2
            m["segments"][1]["filename"] = "00000002.wav"
            (d / "00000001.wav").rename(d / "00000002.wav")
            (d / "00000003.wav").write_bytes((d / "00000000.wav").read_bytes())
        write_capture(self.tmp, "ambient-1-abc", self.segs, tamper=tamper)
        rep = self.verify()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("not contiguous" in e for e in rep["errors"]))
        self.assertEqual([u["filename"] for u in rep["unaccounted_files"]], ["00000003.wav"])

    def test_format_change_and_open_segment_fail(self):
        def tamper(m, d):
            audio.write_mono(d / "00000001.wav", self.segs[1], 16000)
            data = (d / "00000001.wav").read_bytes()
            m["segments"][1]["bytes"] = len(data)
            m["segments"][1]["sha256"] = hashlib.sha256(data).hexdigest()
            m["segments"][1]["endedAt"] = None
        write_capture(self.tmp, "ambient-1-abc", self.segs, tamper=tamper)
        rep = self.verify()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("PCM format" in e for e in rep["errors"]))
        self.assertTrue(any("still open" in e for e in rep["errors"]))

    def test_window_scope_accepts_a_closed_contiguous_window_from_a_long_capture(self):
        write_capture(self.tmp, "ambient-long", self.segs, first_index=185, manifest_prefix_missing=185)
        caps = phone.load_captures(self.tmp / "captures.json")
        capture = phone.find_capture(caps, "ambient-long")
        before = json.dumps(capture, sort_keys=True)
        whole = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long")
        self.assertFalse(whole["ok"], "all-mode must fail when earlier segments were not pulled")
        win = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(185, 186))
        self.assertTrue(win["ok"], win["errors"])
        self.assertEqual([s["index"] for s in win["segments"]], [185, 186])
        self.assertEqual(win["scope"]["manifest_total_segments"], 187)
        self.assertEqual(json.dumps(capture, sort_keys=True), before, "manifest object untouched, no reindexing")
        info, samples = phone.concatenate(win)
        self.assertEqual(len(samples), 72000)
        single = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(186, 186))
        self.assertTrue(single["ok"])
        self.assertEqual([o["index"] for o in single["outside_window_files"]], [185])
        missing_file = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(184, 186))
        self.assertFalse(missing_file["ok"])
        self.assertTrue(any("file missing: 00000184.wav" in e for e in missing_file["errors"]))

    def test_window_rejects_gaps_open_segments_and_hash_mismatch(self):
        def tamper(m, d):
            m["segments"][-1]["endedAt"] = None  # index 186 still open
        write_capture(self.tmp, "ambient-long", self.segs, first_index=185, manifest_prefix_missing=185, tamper=tamper)
        capture = phone.find_capture(phone.load_captures(self.tmp / "captures.json"), "ambient-long")
        rep = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(185, 186))
        self.assertFalse(rep["ok"])
        self.assertTrue(any("still open" in e for e in rep["errors"]))
        self.assertTrue(phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(185, 185))["ok"])
        capture["segments"] = [s for s in capture["segments"] if s["index"] != 185]
        gap = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(184, 186))
        self.assertTrue(any("not contiguous" in e and "missing [185]" in e for e in gap["errors"]))
        capture["segments"][-1]["sha256"] = "0" * 64
        bad = phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(186, 186))
        self.assertTrue(any("sha256 mismatch" in e for e in bad["errors"]))
        with self.assertRaises(ValueError):
            phone.verify_segments(capture, self.tmp / "captures" / "ambient-long", window=(186, 185))

    def test_pull_receipt_is_read_like_roots_script_writes_it(self):
        write_capture(self.tmp, "ambient-long", self.segs, first_index=185, manifest_prefix_missing=185)
        raw = (self.tmp / "captures.json").read_bytes()
        capture = phone.find_capture(json.loads(raw), "ambient-long")
        files = [{**s, "started_unix": phone.to_unix(s["startedAt"]), "ended_unix": phone.to_unix(s["endedAt"]), "path": str(self.tmp / "captures" / "ambient-long" / s["filename"]), "hash_verified": True} for s in capture["segments"] if s["index"] >= 185]
        (self.tmp / "pull-receipt.json").write_text(json.dumps({"capture_id": "ambient-long", "udid": "X", "proof_scope": "selected closed segments", "since_unix": 1.0, "until_unix": 2.0, "manifest_sha256": hashlib.sha256(raw).hexdigest(), "files": files, "gaps": [], "capture_closed": False}))
        pulled = phone.load_pull_receipt(self.tmp / "pull-receipt.json")
        self.assertEqual(pulled["window"], (185, 186))
        self.assertEqual(pulled["capture_id"], "ambient-long")
        self.assertEqual(pulled["captures_json"], self.tmp / "captures.json")
        self.assertEqual(pulled["directory"], self.tmp / "captures" / "ambient-long", "directory taken from the receipt's file paths")
        (self.tmp / "bare.json").write_text(json.dumps({"capture_id": "ambient-long", "files": [{"index": 185}, {"index": 186}]}))
        self.assertEqual(phone.load_pull_receipt(self.tmp / "bare.json")["directory"], self.tmp / "ambient-long", "root's <out>/<capture_id> layout without paths")

    def test_mode_json(self):
        (self.tmp / "mode.json").write_text(json.dumps({"quiet": True, "activity": {"prayer": {"captureId": "prayer-9-x"}}}))
        m = phone.read_mode(self.tmp / "mode.json")
        self.assertEqual((m["quiet"], m["praying"], m["prayer_capture_id"]), (True, True, "prayer-9-x"))
        (self.tmp / "idle.json").write_text(json.dumps({"quiet": False, "activity": {"idle": {}}}))
        self.assertFalse(phone.read_mode(self.tmp / "idle.json")["praying"])


# ----------------------------------------------------------------------------- scoring end to end

class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = speech_like(48000, 5.0, PATTERN)
        audio.write_mono(self.tmp / "source.wav", self.src, 48000)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_room_and_phone_evidence(self):
        audio.write_mono(self.tmp / "room.wav", add_noise(speech_like(44100, 8.0, PATTERN, level=0.08, start=1.5), -55), 44100)
        phone_sig = add_noise(speech_like(48000, 9.0, PATTERN, level=0.15, start=3.0), -50)
        write_capture(self.tmp, "ambient-7-xyz", [phone_sig[:48000 * 4], phone_sig[48000 * 4:]])
        rc = Receipt("t")
        res = scoring.score(source=self.tmp / "source.wav", room=self.tmp / "room.wav", receipt=rc, phone_captures=self.tmp / "captures.json", phone_dir=self.tmp / "captures" / "ambient-7-xyz", capture_id="ambient-7-xyz", reference_text="lord thank you for the gift", reference_verified=False, hypotheses={"phone_journal": "Lord, thank you for the gift.", "pipeline": "lord thank you for a gift"}, phrases=["thank you for the gift", "amen"])
        self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
        ev = res["acoustic_evidence"]
        self.assertTrue(ev["room_heard_source"] and ev["phone_heard_source"] and ev["phone_segments_verified"])
        self.assertAlmostEqual(res["alignments"]["source_in_room"]["lag_s"], 1.5, delta=0.06)
        self.assertAlmostEqual(res["alignments"]["source_in_phone"]["lag_s"], 3.0, delta=0.06)
        self.assertNotIn("wer", res["text"]["phone_journal"])
        self.assertEqual(res["text"]["phone_journal"]["candidate_error_ratio"], 0.0)
        self.assertFalse(res["text"]["phone_journal"]["reference_verified"])
        self.assertTrue(res["transport"]["phone_segments_verified"])
        self.assertEqual([p["found"] for p in res["text"]["pipeline"]["confirmed_phrases"]], [False, False])
        self.assertEqual([p["found"] for p in res["text"]["phone_journal"]["confirmed_phrases"]], [True, False])

    def test_flat_room_fails_and_is_not_evidence(self):
        audio.write_mono(self.tmp / "room.wav", add_noise(array("h", [0] * (44100 * 6)), -75), 44100)
        rc = Receipt("t")
        res = scoring.score(source=self.tmp / "source.wav", room=self.tmp / "room.wav", receipt=rc)
        self.assertEqual(rc.finish(), "fail")
        self.assertFalse(res["acoustic_evidence"]["room_heard_source"])
        self.assertNotIn("source_in_room", res["alignments"])
        self.assertTrue(any(c["name"] == "room_has_signal" and not c["ok"] for c in rc.data["checks"]))

    def test_wer_only_with_verified_reference(self):
        v = scoring.compare_text("the quick brown fox", "the quick brown dog jumps", verified=True)
        self.assertEqual((v["substitutions"], v["insertions"], v["deletions"]), (1, 1, 0))
        self.assertEqual(v["wer"], 0.5)
        u = scoring.compare_text("the quick brown fox", "the quick brown fox", verified=False)
        self.assertNotIn("wer", u)
        self.assertEqual(u["candidate_error_ratio"], 0.0)
        self.assertNotIn("agreement", json.dumps(u))
        worse = scoring.compare_text("amen", "lord thank you amen", verified=False)
        self.assertEqual(worse["candidate_error_ratio"], 3.0, "an error ratio against a candidate can exceed 1 and is reported as is")

    def test_phone_window_scope_scores_only_the_window(self):
        audio.write_mono(self.tmp / "room.wav", add_noise(speech_like(44100, 8.0, PATTERN, level=0.08, start=1.5), -55), 44100)
        phone_sig = add_noise(speech_like(48000, 9.0, PATTERN, level=0.15, start=3.0), -50)
        write_capture(self.tmp, "ambient-long", [phone_sig[:48000 * 4], phone_sig[48000 * 4:]], first_index=185, manifest_prefix_missing=185)
        rc = Receipt("t")
        res = scoring.score(source=self.tmp / "source.wav", room=self.tmp / "room.wav", receipt=rc, phone_captures=self.tmp / "captures.json", phone_dir=self.tmp / "captures" / "ambient-long", capture_id="ambient-long", window=(185, 186))
        self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
        self.assertEqual(res["phone"]["scope"], {"mode": "window", "first_index": 185, "last_index": 186, "manifest_total_segments": 187})
        self.assertAlmostEqual(res["alignments"]["source_in_phone"]["lag_s"], 3.0, delta=0.06)


# ----------------------------------------------------------------------------- remote audio with a fake runner

class FakeProc:
    def __init__(self, pid=4242, die_early=False):
        self.returncode = None
        self.die_early = die_early
        import io
        self.stdout = io.StringIO(f"PID={pid}\n" if not die_early else "")
        self.stderr = io.StringIO("" if not die_early else "pw-record: error: could not connect")
        if die_early:
            self.returncode = 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ssh", timeout)
        return self.returncode

    def communicate(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ssh", timeout)
        return "EXIT=0\n", self.stderr.getvalue()

    def kill(self):
        self.returncode = -9


class FakeRunner(remote_audio.Runner):
    def __init__(self, fixture: Path, out_room: Path, *, ready_after=2, sink_present=True, source_present=True, die_early=False):
        self.fixture = fixture
        self.out_room = out_room
        self.ready_after = ready_after
        self.sink_present = sink_present
        self.source_present = source_present
        self.die_early = die_early
        self.calls = []
        self.dumps = 0
        self.proc = None
        self.room_bytes = None
        self.app_name = None  # learned from the pw-record argv, like PipeWire would

    def _node(self, name, cls):
        return {"id": 99, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {"node.name": name, "media.class": cls}, "params": {"Props": [{"channelVolumes": [0.44, 0.44]}]}}}

    def run(self, argv, timeout, input_bytes=None):
        self.calls.append(argv)
        joined = " ".join(argv)
        def done(rc=0, out=b"", err=b""):
            return subprocess.CompletedProcess(argv, rc, out, err)
        if "pw-dump" in joined:
            self.dumps += 1
            nodes = []
            if "desktop" in argv[1] and self.sink_present:
                nodes.append(self._node(remote_audio.DESKTOP_SINK, "Audio/Sink"))
            if "spark-media" in joined and self.source_present:
                nodes.append(self._node(remote_audio.SPARKB_SOURCE, "Audio/Source"))
            if "spark-media" in joined and self.proc is not None and self.proc.returncode is None and self.ready_after is not None and self.dumps >= 2 + self.ready_after:
                n = self._node("pw-record", "Stream/Input/Audio")
                n["info"]["props"]["application.name"] = self.app_name
                nodes.append(n)
            return done(out=json.dumps(nodes).encode())
        if "mktemp" in joined:
            return done(out=b"/tmp/device-eval-run.ABC\n")
        if argv[0] == "scp":
            if argv[-1].startswith("rawleysm@") or argv[-1].startswith("desktop:"):
                return done()
            if self.room_bytes is None:
                return done(rc=1, err=b"no room file")
            Path(argv[-1]).write_bytes(self.room_bytes)
            return done()
        if "sha256sum" in joined:
            if self.fixture.name in joined:
                return done(out=(hashlib.sha256(self.fixture.read_bytes()).hexdigest() + "  x\n").encode())
            return done(out=(hashlib.sha256(self.room_bytes or b"").hexdigest() + "  x\n").encode())
        if "pw-play" in joined:
            self.room_bytes = self.out_room.read_bytes()
            return done()
        if "kill -INT" in joined or "kill -TERM" in joined:
            if self.proc is not None:
                self.proc.returncode = 0
            return done()
        if "rm -f" in joined or "rmdir" in joined:
            return done()
        return done(rc=127, err=b"unexpected")

    def popen(self, argv):
        self.calls.append(argv)
        import re
        m = re.search(r'application\.name = \\?"?([^"\\ ]+)', argv[-1])
        self.app_name = m.group(1) if m else None
        self.proc = FakeProc(die_early=self.die_early)
        return self.proc

    def sleep(self, seconds):
        pass


class RemoteAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        audio.write_mono(self.tmp / "fixture.wav", speech_like(48000, 2.0, PATTERN[:8]), 48000)
        audio.write_mono(self.tmp / "room-src.wav", speech_like(48000, 7.0, PATTERN[:8], level=0.1, start=2.0), 48000)
        self.cfg = remote_audio.PlayRecordConfig(fixture=self.tmp / "fixture.wav", out_dir=self.tmp / "out", fixture_duration_s=2.0, readiness_timeout_s=1.0, readiness_poll_s=0.01, pre_roll_s=2.0, tail_s=3.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def joined(self, runner):
        return [" ".join(c) for c in runner.calls]

    def test_happy_path_orders_record_ready_play_stop_copy_cleanup(self):
        runner = FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav")
        rc = Receipt("t")
        res = remote_audio.play_record(self.cfg, rc, runner, wav_duration=lambda p: audio.wav_info(p).duration_s)
        self.assertEqual(rc.finish(), "pass", [c for c in rc.data["checks"] if not c["ok"]])
        calls = self.joined(runner)
        i_rec = next(i for i, c in enumerate(calls) if "pw-record" in c)
        i_play = next(i for i, c in enumerate(calls) if "pw-play" in c)
        i_int = next(i for i, c in enumerate(calls) if "kill -INT 4242" in c)
        self.assertLess(i_rec, i_play)
        self.assertLess(i_play, i_int)
        self.assertIn(f"--target {remote_audio.DESKTOP_SINK} --volume 0.500", calls[i_play])
        self.assertIn("ssh -l rawleysm spark-media", calls[i_rec])
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", calls[i_rec])
        self.assertIn(f"--target {remote_audio.SPARKB_SOURCE}", calls[i_rec])
        self.assertIn("setsid timeout --signal=INT --kill-after=10 37 pw-record", calls[i_rec], "remote recorder has its own finite lifetime (2+2+3+30 s)")
        self.assertIn("setsid timeout --signal=INT --kill-after=5 17 pw-play", calls[i_play], "remote playback has its own finite lifetime (2+15 s)")
        unique = remote_audio.unique_app_name(rc.run_id)
        self.assertIn(unique, calls[i_rec])
        self.assertEqual(runner.app_name, unique)
        started = next(a for a in rc.data["actions"] if a["name"] == "pw_record_started")
        self.assertEqual(started["application_name"], unique)
        self.assertEqual(started["remote_lifetime_cap_s"], 37.0)
        self.assertTrue(Path(res["room_wav"]).is_file())
        self.assertTrue(any("rm -f /tmp/device-eval-run.ABC/room.wav" in c for c in calls))
        self.assertFalse(any("pkill" in c or "killall" in c for c in calls))
        self.assertEqual(res["room_duration_s"], 7.0)

    def test_readiness_never_seen_means_no_playback_and_pid_cleanup(self):
        runner = FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav", ready_after=None)
        rc = Receipt("t")
        remote_audio.play_record(self.cfg, rc, runner)
        self.assertEqual(rc.finish(), "fail")
        calls = self.joined(runner)
        self.assertFalse(any("pw-play" in c for c in calls))
        self.assertTrue(any("kill -INT 4242" in c for c in calls))
        self.assertTrue(any(c["name"] == "record_stream_running" and not c["ok"] for c in rc.data["checks"]))

    def test_record_process_dying_early_fails_before_playback(self):
        runner = FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav", die_early=True)
        rc = Receipt("t")
        with self.assertRaises(remote_audio.RemoteAudioError):
            remote_audio.play_record(self.cfg, rc, runner)
        self.assertFalse(any("pw-play" in c for c in self.joined(runner)))

    def test_missing_sink_blocks_before_anything_starts(self):
        runner = FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav", sink_present=False)
        rc = Receipt("t")
        remote_audio.play_record(self.cfg, rc, runner)
        self.assertEqual(rc.finish(), "blocked")
        self.assertFalse(any("pw-record" in c or "scp" in c for c in self.joined(runner)))

    def test_stalled_ssh_cannot_block_start_record(self):
        import os

        class StalledProc(FakeProc):
            def __init__(self):
                self.returncode = None
                self.die_early = False
                r, self.w = os.pipe()
                self.stdout = os.fdopen(r, "r")
                import io
                self.stderr = io.StringIO("")
                self.killed = False

            def kill(self):
                self.killed = True
                self.returncode = -9
                os.close(self.w)

        class StalledRunner(FakeRunner):
            def popen(self, argv):
                self.proc = StalledProc()
                return self.proc

        runner = StalledRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav")
        host = remote_audio.Host.parse("ssh -l rawleysm spark-media")
        import time as _t
        t0 = _t.monotonic()
        with self.assertRaises(remote_audio.RemoteAudioError) as ctx:
            remote_audio.start_record(host, runner, "src", "/tmp/x.wav", rate=48000, channels=1, app_name="n", max_seconds=10, pid_timeout=0.3)
        self.assertLess(_t.monotonic() - t0, 3.0)
        self.assertTrue(runner.proc.killed)
        self.assertIn("no PID line within 0.3s", str(ctx.exception))

    def test_host_parsing_and_volume_guard(self):
        h = remote_audio.Host.parse("ssh -l rawleysm spark-media")
        self.assertEqual(h.scp_target, "rawleysm@spark-media")
        self.assertEqual(remote_audio.Host.parse("ssh desktop").scp_target, "desktop")
        self.assertEqual(h.command(["pw-dump"])[-1], "env XDG_RUNTIME_DIR=/run/user/1000 pw-dump")
        with self.assertRaises(remote_audio.RemoteAudioError):
            remote_audio.play(h, FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav"), "sink", "/tmp/x.wav", volume=1.5, max_seconds=1)
        with self.assertRaises(remote_audio.RemoteAudioError):
            remote_audio.remote_remove(h, FakeRunner(self.tmp / "fixture.wav", self.tmp / "room-src.wav"), ["/home/rawleysm/important.wav"])


# ----------------------------------------------------------------------------- fixture excerpt

@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class FixtureExcerptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "older-prayer.wav"
        audio.write_mono(self.src, speech_like(48000, 6.0, PATTERN + [1, 0, 0, 1]), 48000)
        self.prov = self.tmp / "drive-provenance.json"
        self.prov.write_text(json.dumps([{"name": self.src.name, "id": "DRIVEID", "drive_url": "https://drive.google.com/x", "mime_type": "audio/wav", "bytes": self.src.stat().st_size, "sha256": hashlib.sha256(self.src.read_bytes()).hexdigest()}]))
        self.transcript = self.tmp / "t.json"
        self.transcript.write_text(json.dumps({"full_text": "Jesus I feel your presence", "model_id": "asr-x", "segments": []}))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_excerpt_layout_provenance_and_guards(self):
        before = self.src.read_bytes()
        prov = fixture.derive_excerpt(self.src, self.tmp / "ex.wav", offset_s=1.0, duration_s=2.0, markers=True, provenance_json=self.prov, transcripts=[self.transcript])
        self.assertEqual(self.src.read_bytes(), before, "source unchanged")
        self.assertEqual([p["part"] for p in prov["layout"]], ["lead_silence", "start_marker", "excerpt", "end_marker", "tail_silence"])
        self.assertAlmostEqual(prov["output"]["duration_s"], 1.0 + 0.5 + 2.0 + 0.5 + 1.0, delta=0.01)
        self.assertTrue(prov["source"]["drive"]["hash_matches_download"])
        self.assertEqual(prov["source"]["drive"]["id"], "DRIVEID")
        self.assertEqual(prov["reference_status"], "candidate_unverified")
        self.assertFalse(prov["transcript_candidates"][0]["verified"])
        self.assertLess(prov["excerpt"]["peak_dbfs"], -6.0)
        self.assertTrue(Path(prov["provenance_path"]).is_file())
        info, samples = audio.read_mono(self.tmp / "ex.wav")
        self.assertEqual(info.rate, 48000)
        self.assertTrue(all(v == 0 for v in samples[: 48000 - 1]), "lead silence is silent")
        with self.assertRaises(fixture.FixtureError):
            fixture.derive_excerpt(self.src, self.tmp / "b.wav", offset_s=0, duration_s=1, gain_db=3.0)
        boosted = fixture.derive_excerpt(self.src, self.tmp / "b.wav", offset_s=0, duration_s=1, gain_db=3.0, allow_boost=True)
        self.assertTrue(boosted["excerpt"]["boost_allowed"])
        self.assertEqual(boosted["excerpt"]["clipped_samples"], 0)
        self.assertGreater(boosted["excerpt"]["peak_dbfs"], -9.0)
        with self.assertRaises(fixture.FixtureError) as clip:
            fixture.derive_excerpt(self.src, self.tmp / "clip.wav", offset_s=0, duration_s=1, gain_db=20.0, allow_boost=True)
        self.assertIn("clips", str(clip.exception))
        self.assertFalse((self.tmp / "clip.wav").exists())
        with self.assertRaises(fixture.FixtureError):
            fixture.derive_excerpt(self.src, self.tmp / "huge.wav", offset_s=0, duration_s=1, gain_db=30.0, allow_boost=True)
        with self.assertRaises(fixture.FixtureError):
            fixture.derive_excerpt(self.src, self.tmp / "c.wav", offset_s=0, duration_s=400)
        with self.assertRaises(fixture.FixtureError):
            fixture.derive_excerpt(self.src, self.tmp / "d.wav", offset_s=5.5, duration_s=2)


# ----------------------------------------------------------------------------- CLI receipts

class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_phone_mode_exit_codes_and_receipt(self):
        (self.tmp / "praying.json").write_text(json.dumps({"quiet": False, "activity": {"prayer": {"captureId": "prayer-1-a"}}}))
        (self.tmp / "idle.json").write_text(json.dumps({"quiet": True, "activity": {"idle": {}}}))
        self.assertEqual(cli.run(["phone", "mode", "--mode-json", str(self.tmp / "praying.json"), "--out", str(self.tmp / "o1")]), 2)
        self.assertEqual(cli.run(["phone", "mode", "--mode-json", str(self.tmp / "idle.json"), "--out", str(self.tmp / "o2")]), 0)
        rc = json.loads((self.tmp / "o1" / "phone-mode-receipt.json").read_text())
        self.assertEqual(rc["outcome"], "blocked")
        self.assertEqual(rc["schema"], "rlm-adk-ios.device-eval.receipt/1")

    def test_wda_unreachable_is_a_failed_receipt_not_a_crash(self):
        code = cli.run(["preflight", "--wda", "http://127.0.0.1:9", "--out", str(self.tmp / "o")])
        self.assertEqual(code, 1)
        rc = json.loads((self.tmp / "o" / "preflight-receipt.json").read_text())
        self.assertEqual(rc["outcome"], "fail")
        self.assertIn("unreachable", rc["reasons"][0])

    def test_score_command_creates_out_dir_and_writes_score_json(self):
        audio.write_mono(self.tmp / "source.wav", speech_like(48000, 3.0, PATTERN[:12]), 48000)
        audio.write_mono(self.tmp / "room.wav", add_noise(speech_like(44100, 5.0, PATTERN[:12], level=0.08, start=1.0), -55), 44100)
        out = self.tmp / "new" / "deeper"
        code = cli.run(["audio", "score", "--source", str(self.tmp / "source.wav"), "--room", str(self.tmp / "room.wav"), "--out", str(out)])
        self.assertEqual(code, 0)
        score = json.loads((out / "score.json").read_text())
        self.assertTrue(score["acoustic_evidence"]["room_heard_source"])
        self.assertAlmostEqual(score["alignments"]["source_in_room"]["lag_s"], 1.0, delta=0.06)

    def test_score_with_pull_receipt_uses_the_pulled_window(self):
        audio.write_mono(self.tmp / "source.wav", speech_like(48000, 3.0, PATTERN[:12]), 48000)
        sig = add_noise(speech_like(48000, 6.0, PATTERN[:12], level=0.15, start=2.0), -50)
        write_capture(self.tmp, "ambient-9", [sig[:48000 * 3], sig[48000 * 3:]], first_index=185, manifest_prefix_missing=185)
        raw = (self.tmp / "captures.json").read_bytes()
        capture = phone.find_capture(json.loads(raw), "ambient-9")
        files = [{**s, "path": str(self.tmp / "captures" / "ambient-9" / s["filename"]), "hash_verified": True} for s in capture["segments"] if s["index"] >= 185]
        (self.tmp / "pull-receipt.json").write_text(json.dumps({"capture_id": "ambient-9", "manifest_sha256": hashlib.sha256(raw).hexdigest(), "files": files}))
        out = self.tmp / "score"
        code = cli.run(["audio", "score", "--source", str(self.tmp / "source.wav"), "--pull-receipt", str(self.tmp / "pull-receipt.json"), "--out", str(out)])
        self.assertEqual(code, 0)
        rc = json.loads((out / "audio-score-receipt.json").read_text())
        self.assertTrue(any(c["name"] == "pull_receipt_manifest_hash_matches" and c["ok"] for c in rc["checks"]))
        score = json.loads((out / "score.json").read_text())
        self.assertEqual(score["phone"]["scope"]["mode"], "window")
        self.assertEqual([s["index"] for s in score["phone"]["segments"]], [185, 186])
        self.assertAlmostEqual(score["alignments"]["source_in_phone"]["lag_s"], 2.0, delta=0.06)
        self.assertIn("scripts/device_eval/scoring.py", rc["code"]["files"])
        self.assertEqual(len(rc["code"]["combined_sha256"]), 64)

    def test_verify_segments_command(self):
        write_capture(self.tmp, "ambient-2-b", [speech_like(48000, 0.5, [1, 0])])
        code = cli.run(["phone", "verify-segments", "--captures", str(self.tmp / "captures.json"), "--dir", str(self.tmp / "captures" / "ambient-2-b"), "--capture-id", "ambient-2-b", "--out", str(self.tmp / "o")])
        self.assertEqual(code, 0)
        self.assertEqual(cli.run(["phone", "verify-segments", "--captures", str(self.tmp / "captures.json"), "--dir", str(self.tmp / "captures" / "ambient-2-b"), "--capture-id", "nope", "--out", str(self.tmp / "o2")]), 1)
        self.assertEqual(cli.run(["phone", "verify-segments", "--captures", str(self.tmp / "captures.json"), "--dir", str(self.tmp / "captures" / "ambient-2-b"), "--capture-id", "ambient-2-b", "--window", "0:3", "--out", str(self.tmp / "o3")]), 1)
        self.assertEqual(cli.run(["phone", "verify-segments", "--captures", str(self.tmp / "captures.json"), "--dir", str(self.tmp / "captures" / "ambient-2-b"), "--capture-id", "ambient-2-b", "--window", "0:0", "--out", str(self.tmp / "o4")]), 0)
        rc = json.loads((self.tmp / "o4" / "phone-verify-segments-receipt.json").read_text())
        self.assertEqual(rc["result"]["scope"]["mode"], "window")


class CancellationReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keyboard_interrupt_and_sigterm_still_write_a_receipt(self):
        from device_eval import receipt as receipt_mod
        original = cli.dispatch
        for exc in (KeyboardInterrupt(), receipt_mod.Cancelled("signal 15")):
            def boom(ns, name, receipt, out_dir, _exc=exc):
                receipt.action("started")
                raise _exc
            cli.dispatch = boom
            try:
                out = self.tmp / exc.__class__.__name__
                code = cli.run(["phone", "mode", "--mode-json", "/nonexistent.json", "--out", str(out)])
            finally:
                cli.dispatch = original
            self.assertEqual(code, 130)
            rc = json.loads((out / "phone-mode-receipt.json").read_text())
            self.assertTrue(rc["cancelled"])
            self.assertEqual(rc["outcome"], "fail")
            self.assertTrue(rc["reasons"][0].startswith("cancelled:"))
            self.assertEqual(rc["actions"][0]["name"], "started")

    def test_pending_outcome_is_not_pass(self):
        rc = Receipt("t")
        rc.check("a", True)
        rc.pending("publication not observed")
        self.assertEqual(rc.finish(), "pending")
        self.assertEqual(rc.exit_code, 4)
        rc2 = Receipt("t")
        rc2.pending("x")
        rc2.check("b", False, reason="broken")
        self.assertEqual(rc2.finish(), "fail", "a failed check outranks pending")

    def test_code_fingerprint_covers_root_scripts(self):
        files = Receipt("t").data["code"]["files"]
        for name in ("scripts/device_prayer_eval.py", "scripts/wda_session.py", "scripts/pull_device_capture.py", "scripts/device_eval/remote_audio.py"):
            self.assertIn(name, files)


# ----------------------------------------------------------------------------- physical prayer trial logic

class FakePlayer:
    def __init__(self, rc=0):
        self.returncode = rc
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)


class FakeBackend:
    """Mirrors the fake phone: snapshots reflect the WDA state's prayer label; intake status scripted."""

    def __init__(self, wda_state: FakeWdaState, intake_statuses, fail_fresh_after_begin=False, cancel_during_replay=False):
        self.s = wda_state
        self.intake = list(intake_statuses)
        self.fail_fresh_after_begin = fail_fresh_after_begin
        self.cancel_during_replay = cancel_during_replay
        self.client_time = 1.0
        self.calls = []
        self.capture_closed = False
        self.begun_seen = False

    def __call__(self, base, path, data=None):
        self.calls.append((base, path, data))
        praying = self.s.prayer_label == "End prayer"
        if path.endswith("/commands"):
            self.client_time += 1
            if praying and self.fail_fresh_after_begin:
                raise RuntimeError("backend down right after Begin")
            return {"command_id": "c"}
        if path.endswith("/snapshot"):
            captures = [{"capture_id": "ambient-1", "kind": "ambient", "closed": False, "segments": 5, "bytes": 10}]
            if praying or self.begun_seen:
                self.begun_seen = True
                captures.append({"capture_id": "prayer-test-1", "kind": "prayer", "closed": not praying, "segments": 3, "bytes": 300})
            return {"client_time": self.client_time, "connected": True, "snapshot": {"praying": praying, "engine_running": True, "last_buffer_age_s": 0.1, "captures": captures}}
        if "/prayer/captures/" in path:
            from device_eval.receipt import Cancelled
            if self.cancel_during_replay:
                raise Cancelled("signal 15")
            status = self.intake.pop(0) if len(self.intake) > 1 else self.intake[0]
            return {"status": status, "prayer_id": "01a53898" if status == "published" else None}
        raise AssertionError(path)


class PrayerTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import device_prayer_eval
        self.mod = device_prayer_eval
        (self.tmp / "fixture.wav").write_bytes(b"RIFF")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, url, **over):
        ns = self.mod.build_parser().parse_args(["--session-id", "S1", "--install-id", "inst", "--test-backend", "http://test:1", "--fixture", str(self.tmp / "fixture.wav"), "--out", str(self.tmp / "out"), "--wda", url, "--publish-timeout", "0.5", "--intake-poll-interval", "0.01", "--poll-interval", "0.01"])
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def run_trial(self, fake, backend_kwargs=None, **over):
        backend = FakeBackend(fake.state, **(backend_kwargs or {"intake_statuses": ["segments_uploaded", "finalized", "published"]}))
        code = self.mod.trial(self.args(fake.url, **over), http=backend, player_factory=lambda *a, **k: FakePlayer(), sleep=lambda s: None)
        receipt = json.loads((self.tmp / "out" / "prayer-trial-receipt.json").read_text())
        state = json.loads((self.tmp / "out" / "restore-state.json").read_text())
        return code, receipt, state, backend

    def test_full_trial_restores_everything_and_passes(self):
        with FakeWda() as f:
            original = f.state.backend_url
            code, rc, st, backend = self.run_trial(f)
            self.assertEqual(rc["outcome"], "pass", rc["reasons"])
            self.assertEqual(code, 0)
            self.assertEqual(f.state.backend_url, original)
            self.assertEqual(f.state.applied, ["http://test:1", original])
            self.assertEqual(f.state.quiet_label, "Quiet", "quiet restored to original (not quiet)")
            self.assertEqual(f.state.prayer_label, "Begin prayer")
            self.assertEqual(f.state.prayer_taps, 2, "begin and explicit end only")
            self.assertEqual(st["restored"], {"prayer": "ended_explicitly", "backend": True, "quiet": True})
            self.assertFalse(st["backend_changed"] or st["quiet_changed"] or st["prayer_owned"])
            names = {c["name"]: c["ok"] for c in rc["checks"]}
            self.assertTrue(names["real_intake_finalized"] and names["real_intake_published"] and names["original_backend_field_restored"] and names["quiet_restored"])
            self.assertEqual(rc["result"]["capture_id"], "prayer-test-1")
            self.assertIn("scripts/device_prayer_eval.py", rc["code"]["files"])

    def test_snapshot_failure_after_begin_still_ends_own_prayer_and_restores(self):
        with FakeWda() as f:
            code, rc, st, backend = self.run_trial(f, {"intake_statuses": ["published"], "fail_fresh_after_begin": True})
            self.assertEqual(rc["outcome"], "fail")
            self.assertEqual(f.state.prayer_label, "Begin prayer", "own prayer ended in cleanup although its id was never learned")
            self.assertIsNone(st["capture_id"])
            self.assertTrue(st["restored"]["prayer"])
            self.assertEqual(f.state.backend_url, "http://100.90.105.30:8765")
            self.assertEqual(f.state.quiet_label, "Quiet")
            self.assertTrue(any(a["name"] == "cleanup_end_owned_prayer" for a in rc["actions"]))

    def test_preexisting_prayer_is_never_touched(self):
        with FakeWda(prayer_label="End prayer") as f:
            code, rc, st, backend = self.run_trial(f)
            self.assertEqual(rc["outcome"], "fail")
            self.assertEqual(f.state.prayer_taps, 0)
            self.assertEqual(f.state.applied, [])
            self.assertFalse(st["prayer_owned"] or st["backend_changed"] or st["quiet_changed"])
            self.assertEqual(backend.calls, [])

    def test_unsettled_publication_is_pending_not_pass(self):
        with FakeWda() as f:
            code, rc, st, backend = self.run_trial(f, {"intake_statuses": ["segments_uploaded", "finalized"]})
            self.assertEqual(rc["outcome"], "pending")
            self.assertEqual(code, 4)
            names = {c["name"]: c["ok"] for c in rc["checks"]}
            self.assertTrue(names["real_intake_finalized"])
            self.assertNotIn("real_intake_published", names)
            self.assertTrue(any(r.startswith("pending:") for r in rc["reasons"]))
            self.assertEqual(f.state.prayer_label, "Begin prayer")
            self.assertEqual(f.state.backend_url, "http://100.90.105.30:8765")

    def test_cancellation_restores_and_records(self):
        with FakeWda() as f:
            code, rc, st, backend = self.run_trial(f, {"intake_statuses": ["published"], "cancel_during_replay": True})
            self.assertEqual(code, 130)
            self.assertTrue(rc["cancelled"])
            self.assertEqual(f.state.prayer_label, "Begin prayer")
            self.assertEqual(f.state.backend_url, "http://100.90.105.30:8765")
            self.assertEqual(f.state.quiet_label, "Quiet")
            self.assertEqual(st["restored"], {"prayer": "ended_explicitly", "backend": True, "quiet": True})
            self.assertEqual(f.state.prayer_taps, 2, "cancellation after the explicit End never taps the prayer button again")

    def test_backend_restore_failure_does_not_skip_quiet_restore(self):
        with FakeWda() as f:
            f.state.apply_clicks_allowed = 1  # Apply works for the test backend, vanishes for the restore
            code, rc, st, backend = self.run_trial(f)
            names = {c["name"]: c["ok"] for c in rc["checks"]}
            self.assertFalse(names["original_backend_field_restored"])
            self.assertTrue(names["quiet_restored"])
            self.assertEqual(f.state.quiet_label, "Quiet")
            self.assertEqual(f.state.prayer_label, "Begin prayer")
            self.assertTrue(st["backend_changed"], "durable record still says the backend needs restoring")
            self.assertEqual(st["original_backend"], "http://100.90.105.30:8765")
            self.assertEqual(rc["outcome"], "fail")


if __name__ == "__main__":
    unittest.main()
