import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_config
from voice_portal import outbox
from voice_portal.db import connect

STUB = r'''#!/usr/bin/env bash
# Stub allocatorctl: behaviour selected by $STUB_MODE; records posts into $STUB_INBOX (one JSON per line).
mode="${STUB_MODE:-refuse}"
inbox="${STUB_INBOX:?}"
verb="$1"; shift || true
if [ "$verb" = "status" ]; then echo '{"active":"inactive","main_pid":0,"launch":{"instance":{"id":"cycle14d-20261008"}},"runtime_control":{"service_runs":[]},"residents":[]}'; exit 0; fi
action="$1"; shift || true
case "$mode:$action" in
  refuse:*) echo "service controls refused by selected release: ServiceControlRefused: the allocator service has no live run; start it first"; exit 0 ;;
  ok:post)
    text="$(cat "$2")"; id="m$(date +%s%N)"; printf '%s\n' "$(python3 -c 'import json,sys; print(json.dumps({"message_id":sys.argv[1],"run_id":"run-A","text":sys.argv[2]}))' "$id" "$text")" >> "$inbox"
    echo "posted message_id=$id run=run-A target=root"; exit 0 ;;
  crash_after_post:post)
    text="$(cat "$2")"; id="m$(date +%s%N)"; printf '%s\n' "$(python3 -c 'import json,sys; print(json.dumps({"message_id":sys.argv[1],"run_id":"run-A","text":sys.argv[2]}))' "$id" "$text")" >> "$inbox"
    echo "partial output before crash" >&2; exit 137 ;;
  hang:post) sleep 30; exit 0 ;;
  *:list)
    python3 - "$inbox" <<'PY'
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()] if __import__("os").path.exists(sys.argv[1]) else []
for r in rows:
    print(f"message_id={r['message_id']} run={r['run_id']} target=root delivered=- cleared=false text={json.dumps(r['text'])}")
print(f"messages={len(rows)}")
PY
    exit 0 ;;
  *) echo "unexpected $mode:$action" >&2; exit 2 ;;
esac
'''


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = make_config(tmp)
        stub = Path(self.cfg.allocatorctl)
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.inbox = tmp / "stub_inbox.jsonl"
        os.environ["STUB_INBOX"] = str(self.inbox)
        self.conn = connect(self.cfg.db_path)

    def tearDown(self):
        os.environ.pop("STUB_MODE", None)
        os.environ.pop("STUB_INBOX", None)
        self.conn.close()
        self.tmp.cleanup()

    def _op(self):
        return outbox.create_operation(self.conn, kind="research", request_text="Connect Psalm 23 with Penrose twistors")

    def test_inactive_allocator_yields_backend_unavailable_and_is_retryable(self):
        os.environ["STUB_MODE"] = "refuse"
        op = self._op()
        self.assertEqual(op["status"], "queued_locally")
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "backend_unavailable")
        self.assertEqual(result["reason"], "no_live_run")
        shown = outbox.get_operation(self.conn, op["operation_id"])
        self.assertEqual(shown["status"], "backend_unavailable")
        self.assertEqual(len(shown["attempts"]), 1)
        self.assertIn("no live run", shown["attempts"][0]["stdout"])
        # retry allowed from backend_unavailable once a run exists
        os.environ["STUB_MODE"] = "ok"
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "directive_posted")
        self.assertTrue(result["message_id"].startswith("m"))
        self.assertEqual(result["run_id"], "run-A")
        # never re-posted from directive_posted
        again = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertFalse(again["dispatched"])
        self.assertEqual(len(self.inbox.read_text().splitlines()), 1)

    def test_attempt_row_exists_before_subprocess_runs(self):
        os.environ["STUB_MODE"] = "ok"
        op = self._op()
        outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        attempt = self.conn.execute("SELECT * FROM work_attempts WHERE operation_id = ?", (op["operation_id"],)).fetchone()
        self.assertIsNotNone(attempt["started_at"])
        self.assertIn("inbox", json.loads(attempt["command_json"]))
        self.assertIn("--file", json.loads(attempt["command_json"]))

    def test_ambiguous_post_is_reconciled_not_replayed(self):
        os.environ["STUB_MODE"] = "crash_after_post"
        op = self._op()
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "delivery_unknown")
        blocked = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertFalse(blocked["dispatched"])
        rec = outbox.reconcile(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(rec["after"], "directive_posted")
        self.assertEqual(outbox.get_operation(self.conn, op["operation_id"])["status"], "directive_posted")
        self.assertEqual(len(self.inbox.read_text().splitlines()), 1)

    def test_timeout_is_delivery_unknown_and_stays_unknown_when_listing_refused(self):
        self.cfg.allocatorctl_timeout_s = 1
        os.environ["STUB_MODE"] = "hang"
        op = self._op()
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(result["reason"], "post_timed_out")
        os.environ["STUB_MODE"] = "refuse"
        rec = outbox.reconcile(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(rec["after"], "delivery_unknown")
        self.assertEqual(rec["listing"], "unavailable")
        # listing that works but cannot prove the earlier run: still unknown (no run id recorded)
        os.environ["STUB_MODE"] = "ok"
        rec = outbox.reconcile(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(rec["after"], "delivery_unknown")
        self.assertIsNone(rec["proof"])

    def test_missing_cli_is_backend_unavailable(self):
        self.cfg.allocatorctl = str(Path(self.tmp.name) / "nope")
        op = self._op()
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "backend_unavailable")
        self.assertEqual(result["reason"], "allocatorctl_missing")

    def test_source_packet_is_not_a_result(self):
        op = self._op()
        info = outbox.build_source_packet(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(info["kind"], "source_packet")
        shown = outbox.get_operation(self.conn, op["operation_id"])
        self.assertEqual(shown["status"], "queued_locally")  # not promoted
        self.assertTrue(shown["artifacts"][0]["available"])
        data = json.loads(Path(info["path"]).read_text())
        self.assertEqual(data["artifact_kind"], "source_packet")
        Path(info["path"]).unlink()
        self.assertFalse(outbox.get_operation(self.conn, op["operation_id"])["artifacts"][0]["available"])

    def test_parse_listing(self):
        rows = outbox.parse_inbox_listing('message_id=abc run=run-1 target=root delivered=- cleared=false text="hello \\"x\\""\nmessages=1\n')
        self.assertEqual(rows[0]["message_id"], "abc")
        self.assertEqual(rows[0]["text"], 'hello "x"')

    def test_same_operation_recovered_after_reopen(self):
        op = self._op()
        self.conn.close()
        self.conn = connect(self.cfg.db_path)
        self.assertEqual(outbox.get_operation(self.conn, op["operation_id"])["request_text"], op["request_text"])
        self.assertEqual(outbox.create_operation(self.conn, kind="research", request_text="x", operation_id=op["operation_id"])["request_text"], op["request_text"])


if __name__ == "__main__":
    unittest.main()
