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

    def test_concurrent_dispatch_posts_exactly_once(self):
        """OUTBOX-1 regression: two threads, separate connections, simultaneous dispatch -> one post."""
        import threading

        os.environ["STUB_MODE"] = "slow_ok"
        op = self._op()
        results = []
        barrier = threading.Barrier(2)

        def worker():
            local = connect(self.cfg.db_path)
            barrier.wait()
            results.append(outbox.dispatch(local, self.cfg, op["operation_id"]))
            local.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dispatched = [r for r in results if r["dispatched"]]
        skipped = [r for r in results if not r["dispatched"]]
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(len(skipped), 1)
        self.assertIn(skipped[0]["reason"], ("not_retryable_from_posting", "not_retryable_from_directive_posted"))
        self.assertEqual(len(self.inbox.read_text().splitlines()), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_attempts WHERE operation_id = ?", (op["operation_id"],)).fetchone()[0], 1)
        self.assertEqual(outbox.get_operation(self.conn, op["operation_id"])["status"], "directive_posted")
        # OUTBOX-2: the payload the CLI read (one second after the claim) is the full directive,
        # written by the winner to a per-attempt file; the loser wrote no file at all.
        posted = json.loads(self.inbox.read_text().splitlines()[0])
        self.assertEqual(posted["text"].rstrip("\n"), outbox.directive_text(op).rstrip("\n"))
        self.assertIn(op["operation_id"], posted["text"])
        self.assertIn("Penrose", posted["text"])
        files = sorted(p.name for p in (self.cfg.root / "outbox").iterdir())
        self.assertEqual(files, [f"{op['operation_id']}-attempt{dispatched[0]['attempt_id']}.txt"])
        self.assertEqual(posted["file"], str(self.cfg.root / "outbox" / files[0]))

    def test_losing_request_cannot_mutate_winner_payload_under_forced_interleaving(self):
        """Force the loser to run its whole dispatch while the winner's CLI is still reading."""
        import threading

        os.environ["STUB_MODE"] = "slow_ok"
        op = self._op()
        winner_started = threading.Event()
        original_run = outbox._run

        def slow_run(cmd, timeout):
            winner_started.set()
            return original_run(cmd, timeout)

        outbox._run = slow_run
        try:
            results = {}

            def winner():
                local = connect(self.cfg.db_path)
                results["winner"] = outbox.dispatch(local, self.cfg, op["operation_id"])
                local.close()

            t = threading.Thread(target=winner)
            t.start()
            self.assertTrue(winner_started.wait(5))
            local = connect(self.cfg.db_path)
            results["loser"] = outbox.dispatch(local, self.cfg, op["operation_id"])  # runs entirely during the CLI read
            local.close()
            t.join()
        finally:
            outbox._run = original_run
        self.assertFalse(results["loser"]["dispatched"])
        self.assertTrue(results["winner"]["dispatched"])
        posted = json.loads(self.inbox.read_text().splitlines()[0])
        self.assertIn("Penrose", posted["text"])
        self.assertEqual(len(list((self.cfg.root / "outbox").iterdir())), 1)

    def test_directive_write_failure_is_local_and_retryable(self):
        os.environ["STUB_MODE"] = "ok"
        op = self._op()
        outbox_dir = self.cfg.root / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        blocker = outbox_dir / f"{op['operation_id']}-attempt1.txt"
        blocker.mkdir()  # a directory where the file must go -> write fails
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "local_write_failed")
        shown = outbox.get_operation(self.conn, op["operation_id"])
        self.assertEqual(shown["status"], "queued_locally")
        self.assertEqual(shown["attempts"][0]["outcome"], "local_write_failed")
        self.assertFalse(self.inbox.exists())
        blocker.rmdir()
        self.assertEqual(outbox.dispatch(self.conn, self.cfg, op["operation_id"])["status"], "directive_posted")

    def test_crash_stale_posting_becomes_delivery_unknown_not_replayed(self):
        op = self._op()
        # Simulate a crash after the atomic claim: row left in `posting`, attempt never finished.
        with self.conn:
            self.conn.execute("UPDATE work_operations SET status = 'posting' WHERE operation_id = ?", (op["operation_id"],))
            self.conn.execute("INSERT INTO work_attempts(operation_id, started_at, command_json, outcome) VALUES (?,?,?,?)", (op["operation_id"], 0.0, "[]", "posting"))
        os.environ["STUB_MODE"] = "ok"
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertFalse(self.inbox.exists())
        attempt = self.conn.execute("SELECT outcome, finished_at FROM work_attempts WHERE operation_id = ?", (op["operation_id"],)).fetchone()
        self.assertEqual(attempt["outcome"], "delivery_unknown")
        self.assertIsNotNone(attempt["finished_at"])
        # service start recovers every stale posting row regardless of age
        with self.conn:
            self.conn.execute("UPDATE work_operations SET status = 'posting' WHERE operation_id = ?", (op["operation_id"],))
        self.assertEqual(outbox.recover_stale_posting(self.conn, self.cfg, all_rows=True), [op["operation_id"]])

    def test_rlm_result_requires_matching_reference(self):
        os.environ["STUB_MODE"] = "ok"
        op = self._op()
        posted = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        result = Path(self.tmp.name) / "result.md"
        result.write_text("# draft")
        with self.assertRaises(ValueError):
            outbox.attach_artifact(self.conn, op["operation_id"], kind="rlm_result", path=result)
        with self.assertRaises(ValueError):
            outbox.attach_artifact(self.conn, op["operation_id"], kind="rlm_result", path=result, reference={"message_id": "other", "run_id": "run-A", "result_ref": "x"})
        self.assertEqual(outbox.get_operation(self.conn, op["operation_id"])["status"], "directive_posted")
        info = outbox.attach_artifact(self.conn, op["operation_id"], kind="rlm_result", path=result, reference={"message_id": posted["message_id"], "run_id": posted["run_id"], "result_ref": "projects/x/report.md"})
        self.assertEqual(outbox.get_operation(self.conn, op["operation_id"])["status"], "artifact_available")
        self.assertEqual(info["kind"], "rlm_result")

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

    def test_reconcile_lists_all_and_keeps_uncertainty(self):
        self.cfg.allocatorctl_timeout_s = 1
        os.environ["STUB_MODE"] = "hang"
        op = self._op()
        outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        os.environ["STUB_MODE"] = "ok"
        rec = outbox.reconcile(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(rec["command"][-1], "--all")
        self.assertEqual(rec["after"], "delivery_unknown")

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
