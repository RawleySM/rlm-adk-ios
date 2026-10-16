"""Tickets mailed to the Intake resident: the plain-text mail, the `mailed` event, idempotence, the
replies folded back as `status` events, and the work outbox's `--to` with the mailbox receipt."""

import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.helpers import make_config
from voice_portal import issues, outbox
from voice_portal.db import connect

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Fake allocatorctl: `inbox post --to X --file F` and `inbox list --to-operator --json --in-reply-to ID`."""
    import json, os, sys
    state = os.environ["FAKE_STATE"]
    args = sys.argv[1:]
    def load():
        return json.load(open(state)) if os.path.exists(state) else {"posts": [], "replies": []}
    data = load()
    if args[:2] == ["inbox", "post"]:
        to = args[args.index("--to") + 1] if "--to" in args else None
        if to is None:
            print("allocatorctl inbox post: error: the following arguments are required: --to", file=sys.stderr); sys.exit(2)
        text = open(args[args.index("--file") + 1]).read()
        mid = f"mail-{len(data['posts']) + 1}"
        data["posts"].append({"message_id": mid, "to": to, "text": text, "args": args})
        json.dump(data, open(state, "w"))
        print(f"posted message_id={mid} to={to}")
    elif args[:2] == ["inbox", "list"]:
        wanted = args[args.index("--in-reply-to") + 1]
        if wanted not in [p["message_id"] for p in data["posts"]]:
            print("mail_bad_reply", file=sys.stderr); sys.exit(1)
        msgs = [r for r in data["replies"] if r["in_reply_to"] == wanted]
        print(json.dumps({"schema": "rlm_adk.inbox_list.v1", "instance_id": "i1", "filters": {}, "messages": msgs}))
    else:
        print("unexpected", args, file=sys.stderr); sys.exit(2)
''')

REPORT = {
    "report_id": "r-mail",
    "transcript": {"segments": [{"t": 1, "text": "I'd like an update banner that shows when a new build is ready, so I can tap it to install.", "source": "voice"}]},
    "source": {"app_build": "18"},
    "context": {"mode_before": "available", "passage": {"reference": "Romans 8", "reading": False}},
}


class IntakeMail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config(self.tmp)
        fake = Path(self.cfg.allocatorctl)
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.state = self.tmp / "fake_state.json"
        os.environ["FAKE_STATE"] = str(self.state)
        self.cfg.intake_recipient = "intk01"

    def tearDown(self):
        os.environ.pop("FAKE_STATE", None)

    def posts(self):
        return json.loads(self.state.read_text())["posts"]

    def test_unset_recipient_mails_nothing(self):
        self.cfg.intake_recipient = ""
        ticket, _ = issues.record_report(self.cfg, REPORT)
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"]), {"mailed": False, "reason": "intake_recipient_unset"})
        self.assertFalse(self.state.exists())

    def test_mail_is_plain_text_with_the_ticket_line_and_is_recorded(self):
        ticket, _ = issues.record_report(self.cfg, REPORT)
        result = issues.mail_ticket(self.cfg, ticket["ticket_id"], note="Show it only when a newer build is published.")
        self.assertEqual(result, {"mailed": True, "message_id": "mail-1", "recipient": "intk01"})
        post = self.posts()[0]
        self.assertEqual(post["to"], "intk01")
        self.assertTrue(post["text"].startswith(f"voice-portal ticket_id={ticket['ticket_id']} kind="))
        self.assertIn("update banner", post["text"])
        self.assertIn("Show it only when a newer build is published.", post["text"])
        self.assertIn("Scripture pane: Romans 8", post["text"])
        with self.assertRaises(json.JSONDecodeError):
            json.loads(post["text"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["mail"][0]["message_id"], "mail-1")
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"])["reason"], "already_mailed")
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"], force=True)["message_id"], "mail-2")

    def test_replies_become_status_events_once(self):
        ticket, _ = issues.record_report(self.cfg, REPORT)
        issues.mail_ticket(self.cfg, ticket["ticket_id"])
        data = json.loads(self.state.read_text())
        data["replies"] = [{"message_id": "mail-9", "in_reply_to": "mail-1", "from": "resident:intk01", "text": "status: accepted Delegating to Claude Code on branch intake/bug-1.", "sent_at": 5}]
        self.state.write_text(json.dumps(data))
        recorded = issues.poll_replies(self.cfg)
        self.assertEqual([(e["status"], e["reply_message_id"]) for e in recorded], [("accepted", "mail-9")])
        self.assertEqual(issues.poll_replies(self.cfg), [], "a reply is recorded once")
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["status"], "accepted")
        self.assertEqual(folded["replies"][0]["text"], "status: accepted Delegating to Claude Code on branch intake/bug-1.")
        self.assertEqual(issues.list_tickets(self.cfg)[0]["replies"], 1)

    def test_reply_status_words(self):
        self.assertEqual(issues._reply_status("status: escalated to rsch01"), "escalated")
        self.assertEqual(issues._reply_status("Status = done. Build 19 is on the page."), "done")
        self.assertEqual(issues._reply_status("Here is what I found."), "answered")

    def test_triage_mails_tickets_of_the_listed_dispositions(self):
        self.cfg.inference_base_url = "http://127.0.0.1:9/v1"  # owner down: keyword triage
        ticket, _ = issues.record_report(self.cfg, REPORT)
        issues._triage_logged(self.cfg, ticket["ticket_id"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["triage"]["state"], "done")
        self.assertIn(folded["triage"]["disposition"], self.cfg.intake_mail_dispositions)
        self.assertEqual(len(self.posts()), 1)
        empty, _ = issues.record_report(self.cfg, {"report_id": "empty", "transcript": {"segments": [{"t": 1, "text": "Um."}]}})
        issues._triage_logged(self.cfg, empty["ticket_id"])
        self.assertEqual(len(self.posts()), 1, "a no_report ticket is not mailed")


class WorkOutboxRecipient(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config(self.tmp)
        fake = Path(self.cfg.allocatorctl)
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.state = self.tmp / "fake_state.json"
        os.environ["FAKE_STATE"] = str(self.state)
        self.conn = connect(self.cfg.db_path)

    def tearDown(self):
        os.environ.pop("FAKE_STATE", None)
        self.conn.close()

    def test_work_directive_goes_to_the_recipient_and_the_mailbox_receipt_is_parsed(self):
        self.cfg.work_recipient = "intk01"
        op = outbox.create_operation(self.conn, kind="research", request_text="Compare two reading voices")
        result = outbox.dispatch(self.conn, self.cfg, op["operation_id"])
        self.assertEqual(result["status"], "directive_posted")
        self.assertEqual(result["message_id"], "mail-1")
        self.assertEqual(result["target"], "intk01")
        self.assertIn("--to", json.loads(self.state.read_text())["posts"][0]["args"])


if __name__ == "__main__":
    unittest.main()
