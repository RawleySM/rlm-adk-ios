"""The Work tab's operation list: local records, source packets that never become results, and the
read-only allocator status. Sending an operation to Intake is in test_issues_mail (WorkTabThroughIntake)."""

import hashlib
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
  slow_ok:post)
    # read the payload only after a delay, so any concurrent writer would be observed
    sleep 1; text="$(cat "$2")"; id="m$(date +%s%N)"; printf '%s\n' "$(python3 -c 'import json,sys; print(json.dumps({"message_id":sys.argv[1],"run_id":"run-A","text":sys.argv[2],"file":sys.argv[3]}))' "$id" "$text" "$2")" >> "$inbox"
    echo "posted message_id=$id run=run-A target=root"; exit 0 ;;
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

    def test_operation_is_recorded_once_per_id(self):
        op = self._op()
        self.assertEqual(op["status"], "queued_locally")
        again = outbox.create_operation(self.conn, kind="research", request_text="other words", operation_id=op["operation_id"])
        self.assertEqual(again["request_text"], "Connect Psalm 23 with Penrose twistors")
        self.assertEqual([o["operation_id"] for o in outbox.list_operations(self.conn)], [op["operation_id"]])
        self.assertFalse(hasattr(outbox, "dispatch"), "the direct inbox post is retired")
        self.assertFalse(hasattr(outbox, "reconcile"))

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

    def test_repeated_source_packets_keep_earlier_bytes(self):
        op = self._op()
        first = outbox.build_source_packet(self.conn, self.cfg, op["operation_id"])
        second = outbox.build_source_packet(self.conn, self.cfg, op["operation_id"])
        self.assertNotEqual(first["path"], second["path"])
        arts = outbox.get_operation(self.conn, op["operation_id"])["artifacts"]
        self.assertEqual(len(arts), 2)
        for art in arts:
            data = Path(art["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), art["sha256"])
            self.assertTrue(art["available"])
            self.assertEqual(art["kind"], "source_packet")

    def test_allocator_status_is_read_only(self):
        status = outbox.allocator_status(self.cfg)
        self.assertEqual((status["available"], status["launch_instance"]), (True, "cycle14d-20261008"))
        self.cfg.allocatorctl = str(Path(self.tmp.name) / "nope")
        self.assertEqual(outbox.allocator_status(self.cfg)["outcome"], "missing")
        self.assertFalse(self.inbox.exists(), "nothing was posted")


if __name__ == "__main__":
    unittest.main()
