"""Tickets mailed to the Intake resident: the plain-text mail per kind, the `mailed` event with its
instance id, idempotence, the one mailbox listing per poll that threads replies onto tickets as
`status` events and records unthreaded mail as `inbound` events (each message once), the phone's
feed, and the Work tab's dispatch filed through the issues log instead of posting itself."""

import json
import os
import socket
import stat
import urllib.request
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import make_config
from voice_portal import issues, outbox
from voice_portal.db import connect
from voice_portal.server import make_server
from voice_portal.ws import ClientWebSocket, client_handshake

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Fake allocatorctl: `inbox post --to X --file F` and `inbox list --to-operator --json [--in-reply-to ID]`."""
    import json, os, sys
    state = os.environ["FAKE_STATE"]
    args = sys.argv[1:]
    def load():
        return json.load(open(state)) if os.path.exists(state) else {"instance_id": "i1", "posts": [], "replies": [], "lists": 0}
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
        data["lists"] = data.get("lists", 0) + 1
        json.dump(data, open(state, "w"))
        msgs = data["replies"]
        if "--in-reply-to" in args:
            wanted = args[args.index("--in-reply-to") + 1]
            msgs = [r for r in msgs if r.get("in_reply_to") == wanted]
        print(json.dumps({"schema": "rlm_adk.inbox_list.v1", "instance_id": data.get("instance_id", "i1"), "filters": {"to_operator": "--to-operator" in args}, "messages": msgs}))
    else:
        print("unexpected", args, file=sys.stderr); sys.exit(2)
''')

REPORT = {
    "report_id": "r-mail",
    "transcript": {"segments": [{"t": 1, "text": "I'd like an update banner that shows when a new build is ready, so I can tap it to install.", "source": "voice"}]},
    "source": {"app_build": "18"},
    "context": {"mode_before": "available", "passage": {"reference": "Romans 8", "reading": False}},
}

INTAKE = {"kind": "resident", "resident_id": "intk01", "package_key": "intake_intk01", "name": "Intake"}


def _reply(message_id, text, in_reply_to=None, sent_at="2026-10-17T09:00:00+00:00"):
    return {"message_id": message_id, "direction": "to_operator", "from": INTAKE, "to": [{"kind": "operator"}], "recipients": [], "priority": "normal", "in_reply_to": in_reply_to, "sent_at": sent_at, "text": text}


class FakeAllocator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config(self.tmp)
        self.cfg.inference_base_url = "http://127.0.0.1:9/v1"  # never a real owner
        fake = Path(self.cfg.allocatorctl)
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.state = self.tmp / "fake_state.json"
        os.environ["FAKE_STATE"] = str(self.state)
        self.cfg.intake_recipient = "intk01"
        self.cfg.intake_mail_mode = "post"

    def tearDown(self):
        os.environ.pop("FAKE_STATE", None)

    def data(self):
        return json.loads(self.state.read_text())

    def posts(self):
        return self.data()["posts"]

    def add_replies(self, *replies, instance_id=None):
        data = self.data() if self.state.exists() else {"instance_id": "i1", "posts": [], "replies": [], "lists": 0}
        data["replies"] += list(replies)
        if instance_id:
            data["instance_id"] = instance_id
        self.state.write_text(json.dumps(data))

    def ticket(self, report=REPORT, **extra):
        ticket, _ = issues.record_report(self.cfg, {**report, **extra})
        return ticket


class IntakeMail(FakeAllocator):
    def test_unset_recipient_mails_nothing(self):
        self.cfg.intake_recipient = ""
        ticket = self.ticket()
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"]), {"mailed": False, "reason": "intake_recipient_unset"})
        self.assertFalse(self.state.exists())

    def test_mail_is_plain_text_with_the_ticket_line_and_is_recorded(self):
        ticket = self.ticket()
        result = issues.mail_ticket(self.cfg, ticket["ticket_id"], note="Show it only when a newer build is published.")
        self.assertEqual(result, {"mailed": True, "message_id": "mail-1", "recipient": "intk01", "instance_id": "i1"})
        post = self.posts()[0]
        self.assertEqual(post["to"], "intk01")
        self.assertTrue(post["text"].startswith(f"voice-portal ticket_id={ticket['ticket_id']} kind="))
        self.assertIn("update banner", post["text"])
        self.assertIn("Show it only when a newer build is published.", post["text"])
        self.assertIn("Scripture pane: Romans 8", post["text"])
        self.assertIn("from the phone's Bug chip (app build 18)", post["text"])
        self.assertIn(f"GET http://127.0.0.1:{self.cfg.listen_port}/issues/{ticket['ticket_id']}", post["text"])
        self.assertNotIn("100.", post["text"], "no tailnet address in the mail")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(post["text"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual((folded["mail"][0]["message_id"], folded["mail"][0]["instance_id"]), ("mail-1", "i1"))
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"])["reason"], "already_mailed")
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"], force=True)["message_id"], "mail-2")

    def test_reply_words_follow_the_kind(self):
        expected = {
            "bug": ("A bug report", "accepted, needs_detail, declined, done, failed", "merge commit"),
            "feature_request": ("A feature request", "accepted, needs_detail, declined, done, failed", "merge commit"),
            "message": ("A message for you.", "answered, needs_detail, declined", "record ids and dates"),
            "teaching": ("A teaching.", "learned, needs_detail, declined", "package version"),
            "project_proposal": ("A project proposal.", "proposed, started, needs_detail, declined", "only after he says go"),
            "question": ("A message for you.", "answered, needs_detail, declined", "record ids and dates"),
        }
        for kind, (lead, words, guide) in expected.items():
            ticket = {"ticket_id": "bug-20261017-01", "text": "words", "origin": "conversation", "triage": {"kind": kind, "title": "t"}}
            text = issues.mail_text(ticket)
            self.assertIn(lead, text, kind)
            self.assertIn(f"<word> is one of {words}.", text, kind)
            self.assertIn(guide, text, kind)
            self.assertIn("from the conversation", text)
            self.assertNotIn("Full record", text, "no record line without a URL")

    def test_auto_escalated_mail_says_so_and_asks_for_a_tracked_fix(self):
        ticket = {"ticket_id": "bug-20261017-02", "text": "Set a timer for ten minutes.", "origin": "auto_escalation", "assistant_reply": "I can't set a timer from here.", "triage": {"kind": "feature_request", "title": "Timer from the portal", "summary": "He wants a timer."}}
        text = issues.mail_text(ticket)
        self.assertIn("the voice assistant could not meet it and passed it to you by itself", text)
        self.assertIn("This request was auto-escalated. The voice assistant could not meet this; its reply was:", text)
        self.assertIn("\"I can't set a timer from here.\"", text)
        self.assertIn("A tracked fix is expected", text)
        self.assertIn("open a GitHub issue in RawleySM/rlm-adk-ios", text)
        self.assertIn("intake and capability-gap", text)
        self.assertIn("Fixes #<n>", text)
        plain = issues.mail_text({"ticket_id": "bug-20261017-03", "text": "Tell Intake hello.", "origin": "conversation", "triage": {"kind": "message", "title": "t"}})
        self.assertNotIn("auto-escalated", plain)
        self.assertNotIn("GitHub issue", plain, "the tracking convention is for bugs and features")
        item = issues.feed_item({"event": "status", "ticket_id": "bug-20261017-02", "reply_message_id": "mail-9", "status": "accepted", "text": "status: accepted. Taking it; issue to follow."}, {"bug-20261017-02": ticket})
        self.assertEqual((item["origin"], item["ticket_kind"], item["display"]), ("auto_escalation", "feature_request", "Taking it; issue to follow."))

    def test_stage_mode_writes_the_mail_and_command_but_never_calls_the_allocator(self):
        self.cfg.intake_mail_mode = "stage"
        ticket = self.ticket()
        result = issues.mail_ticket(self.cfg, ticket["ticket_id"], note="Answers to the open questions.")
        self.assertTrue(result["staged"])
        self.assertFalse(result["mailed"])
        self.assertEqual(result["command"][1:5], ["inbox", "post", "--to", "intk01"])
        self.assertTrue(Path(result["body"]).read_text().startswith(f"voice-portal ticket_id={ticket['ticket_id']}"))
        self.assertFalse(self.state.exists(), "the allocator was never called")
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["mail_staged"][0]["recipient"], "intk01")
        self.assertNotIn("mail", folded)
        self.assertEqual(issues.mail_ticket(self.cfg, ticket["ticket_id"])["reason"], "already_staged")
        self.assertIsNone(issues.start_reply_poller(self.cfg), "nothing to poll while mail is only staged")

    def test_operator_status_event(self):
        ticket = self.ticket()
        event = issues.record_status(self.cfg, ticket["ticket_id"], "done", text="Build 19 adds the banner.", artifact="https://host/build19/")
        self.assertEqual(event["from"], "operator-session")
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["status"], "done")
        self.assertIsNone(issues.record_status(self.cfg, "bug-19990101-01", "done"))
        self.assertEqual(issues.feed(self.cfg), [], "an operator's status is not mail from Intake")

    def test_reply_status_words(self):
        self.assertEqual(issues._reply_status("status: escalated to rsch01"), "escalated")
        self.assertEqual(issues._reply_status("Status = done. Build 19 is on the page."), "done")
        self.assertEqual(issues._reply_status("Here is what I found."), "answered")
        for word in ("learned", "proposed", "started", "needs_detail", "declined", "answered"):
            self.assertEqual(issues._reply_status(f"status: {word}. More words."), word)
        self.assertEqual(issues.display_text("status: learned. Rest the voice after each verse."), "Rest the voice after each verse.")
        self.assertEqual(issues.display_text("No status word here."), "No status word here.")

    def test_triage_mails_tickets_of_the_listed_dispositions(self):
        ticket = self.ticket()  # owner down (127.0.0.1:9): keyword triage
        issues._triage_logged(self.cfg, ticket["ticket_id"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["triage"]["state"], "done")
        self.assertIn(folded["triage"]["disposition"], self.cfg.intake_mail_dispositions)
        self.assertEqual(len(self.posts()), 1)
        empty, _ = issues.record_report(self.cfg, {"report_id": "empty", "transcript": {"segments": [{"t": 1, "text": "Um."}]}})
        issues._triage_logged(self.cfg, empty["ticket_id"])
        self.assertEqual(len(self.posts()), 1, "a no_report ticket is not mailed")


class ReplyReader(FakeAllocator):
    def test_one_listing_per_poll_threads_replies_once(self):
        ticket = self.ticket()
        issues.mail_ticket(self.cfg, ticket["ticket_id"])
        lists_after_mail = self.data()["lists"]
        self.add_replies(_reply("mail-9", "status: accepted Delegating to Claude Code on branch intake/bug-1.", in_reply_to="mail-1"))
        recorded = issues.poll_replies(self.cfg)
        self.assertEqual(self.data()["lists"], lists_after_mail + 1, "one inbox list per poll, whatever the number of tickets")
        self.assertEqual([(e["event"], e["status"], e["reply_message_id"], e["instance_id"]) for e in recorded], [("status", "accepted", "mail-9", "i1")])
        self.assertEqual(issues.poll_replies(self.cfg), [], "a reply is recorded once")
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["status"], "accepted")
        self.assertEqual(folded["replies"][0]["text"], "status: accepted Delegating to Claude Code on branch intake/bug-1.")
        self.assertEqual(issues.list_tickets(self.cfg)[0]["replies"], 1)

    def test_unthreaded_mail_is_inbound_and_names_its_tickets(self):
        ticket = self.ticket()
        issues.mail_ticket(self.cfg, ticket["ticket_id"])
        self.add_replies(
            _reply("mail-20", f"status: done. {ticket['ticket_id']} is in build 28; the banner shows replies."),
            _reply("mail-21", "A reply to something that is not a ticket.", in_reply_to="mail-elsewhere"),
        )
        recorded = issues.poll_replies(self.cfg)
        self.assertEqual([(e["event"], e["ticket_id"], e["message_id"]) for e in recorded], [("inbound", None, "mail-20"), ("inbound", None, "mail-21")])
        self.assertEqual(recorded[0]["mentions"], [ticket["ticket_id"]])
        self.assertEqual(recorded[0]["status"], "done")
        self.assertIsNone(recorded[1]["status"], "no status word, none claimed")
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["status"], "open", "inbound mail changes no ticket")
        self.assertEqual(issues.poll_replies(self.cfg), [])
        self.assertEqual(len(issues.read_tickets(issues.log_path(self.cfg))), 1)

    def test_mail_of_another_instance_is_never_threaded(self):
        ticket = self.ticket()
        issues.mail_ticket(self.cfg, ticket["ticket_id"])  # instance i1
        self.add_replies(_reply("mail-30", "status: done", in_reply_to="mail-1"), instance_id="i2")
        recorded = issues.poll_replies(self.cfg)
        self.assertEqual([(e["event"], e["instance_id"]) for e in recorded], [("inbound", "i2")])
        self.assertEqual(issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]["status"], "open")

    def test_mail_recorded_without_an_instance_threads_by_id(self):
        ticket = self.ticket()
        with issues._LogLock(issues.log_path(self.cfg)):
            issues._append(issues.log_path(self.cfg), {"schema": issues.SCHEMA, "event": "mailed", "ticket_id": ticket["ticket_id"], "at": 1.0, "message_id": "mail-d807c67b5f69492681898f568acb7154", "recipient": "intk01"})
        self.add_replies(_reply("mail-6bcd", "status: accepted. I am treating this as an app change.", in_reply_to="mail-d807c67b5f69492681898f568acb7154"))
        recorded = issues.poll_replies(self.cfg)
        self.assertEqual([(e["event"], e["ticket_id"]) for e in recorded], [("status", ticket["ticket_id"])])

    def test_refused_listing_records_nothing(self):
        self.cfg.allocatorctl = str(self.tmp / "missing-allocatorctl")
        self.assertEqual(issues.poll_replies(self.cfg), [])

    def test_feed_serves_replies_and_inbound_newest_last(self):
        ticket = self.ticket(kind="teaching")
        issues.mail_ticket(self.cfg, ticket["ticket_id"])
        self.add_replies(_reply("mail-40", "status: learned. A pause after each verse when reading Psalms.", in_reply_to="mail-1"))
        first = issues.poll_replies(self.cfg)
        self.add_replies(_reply("mail-41", "Intake is running and has your four open tickets."))
        second = issues.poll_replies(self.cfg)
        items = issues.feed(self.cfg)
        self.assertEqual([(i["kind"], i["id"]) for i in items], [("reply", "mail-40"), ("inbound", "mail-41")])
        reply = items[0]
        self.assertEqual(reply["type"], "intake_message")
        self.assertEqual((reply["ticket_id"], reply["status"], reply["from_name"], reply["ticket_kind"]), (ticket["ticket_id"], "learned", "Intake", "teaching"))
        self.assertEqual(reply["display"], "A pause after each verse when reading Psalms.")
        self.assertNotIn("message", reply, "the app decodes `message` as a string")
        self.assertNotIn("session_id", reply)
        self.assertEqual([i["id"] for i in issues.feed(self.cfg, since=first[0]["at"])], ["mail-41"])
        self.assertEqual([i["id"] for i in issues.feed(self.cfg, limit=1)], ["mail-41"])
        self.assertEqual([i["id"] for i in issues.feed_items(self.cfg, second)], ["mail-41"])

    def test_poller_hands_new_items_to_the_callback(self):
        self.cfg.intake_poll_s = 0.05
        ticket = self.ticket()
        issues.mail_ticket(self.cfg, ticket["ticket_id"])
        self.add_replies(_reply("mail-50", "status: accepted", in_reply_to="mail-1"))
        got = []
        stop = threading.Event()
        try:
            issues.start_reply_poller(self.cfg, on_recorded=got.extend, stop=stop)
            deadline = time.time() + 5
            while not got and time.time() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
        self.assertEqual([i["id"] for i in got], ["mail-50"])


class WorkTabThroughIntake(FakeAllocator):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.cfg.db_path)

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_dispatch_files_an_intake_ticket_and_never_posts_itself(self):
        self.cfg.intake_mail_mode = "stage"
        op = outbox.create_operation(self.conn, kind="research", request_text="Compare two reading voices for the Psalms")
        result = outbox.send_to_intake(self.conn, self.cfg, op["operation_id"])
        self.assertTrue(result["sent"])
        self.assertTrue(result["created"])
        ticket = issues.read_tickets(issues.log_path(self.cfg))[result["ticket_id"]]
        self.assertEqual((ticket["report_id"], ticket["origin"], ticket["text"]), (f"work-{op['operation_id']}", "work_tab", "Compare two reading voices for the Psalms"))
        self.assertIsNone(ticket["kind_hint"], "a work kind is not a ticket kind")
        shown = outbox.get_operation(self.conn, op["operation_id"])
        self.assertEqual((shown["status"], shown["note"]), ("sent_to_intake", f"ticket_id={result['ticket_id']}"))
        again = outbox.send_to_intake(self.conn, self.cfg, op["operation_id"])
        self.assertEqual((again["ticket_id"], again["created"]), (result["ticket_id"], False))
        self.assertEqual(len(issues.read_tickets(issues.log_path(self.cfg))), 1)
        self.assertFalse(self.state.exists(), "stage mode: the allocator is never called")

    def test_an_operation_posted_before_cannot_be_sent_again(self):
        op = outbox.create_operation(self.conn, kind="research", request_text="Gather sermons on rest")
        with self.conn:
            self.conn.execute("UPDATE work_operations SET status = 'directive_posted' WHERE operation_id = ?", (op["operation_id"],))
        result = outbox.send_to_intake(self.conn, self.cfg, op["operation_id"])
        self.assertEqual((result["sent"], result["reason"]), (False, "not_sendable_from_directive_posted"))
        self.assertEqual(issues.read_tickets(issues.log_path(self.cfg)), {})


class IntakeEndToEnd(FakeAllocator):
    """The service as deployed: a spoken request filed, triaged and mailed, the reply read and pushed."""

    def setUp(self):
        super().setUp()
        self.cfg.listen_host, self.cfg.listen_port = "127.0.0.1", 0
        self.cfg.intake_poll_s = 0  # no background poller; POST /issues/replies reads the mailbox
        self.server = make_server(self.cfg)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())

    def phone(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        client_handshake(sock, "127.0.0.1", "/ws")
        ws = ClientWebSocket(sock)
        self.assertEqual(json.loads(ws.receive())["type"], "session")
        ws.send_json({"type": "hello", "mode": {"quiet": False, "activity": "idle"}, "install_id": "phone-a", "app_version": "0.2.0", "build": "27"})
        self.assertEqual(json.loads(ws.receive())["type"], "hello_ack")
        return ws

    def test_request_to_mail_to_reply_to_the_phone(self):
        ws = self.phone()
        status, filed = self.call("POST", "/tools/work", {"request_text": "Remember that I read the Psalms at night before bed.", "kind": "teaching", "report_id": "talk-e2e"})
        self.assertEqual((status, filed["spoken"]), (201, "Sent to Intake."))
        deadline = time.time() + 10
        while time.time() < deadline and not (self.state.exists() and self.data()["posts"]):
            time.sleep(0.05)
        post = self.posts()[0]
        self.assertTrue(post["text"].startswith(f"voice-portal ticket_id={filed['ticket_id']} kind=teaching"))
        self.assertIn("<word> is one of learned, needs_detail, declined.", post["text"])
        self.add_replies(_reply("mail-77", "status: learned. Psalms at night; intake_intk01/v2 holds it.", in_reply_to="mail-1"))
        status, polled = self.call("POST", "/issues/replies")
        self.assertEqual((status, polled["pushed"]), (200, 1))
        pushed = json.loads(ws.receive())
        self.assertEqual((pushed["type"], pushed["id"], pushed["ticket_id"], pushed["status"]), ("intake_message", "mail-77", filed["ticket_id"], "learned"))
        self.assertEqual(pushed["display"], "Psalms at night; intake_intk01/v2 holds it.")
        status, feed = self.call("GET", "/intake/feed")
        self.assertEqual([i["id"] for i in feed["items"]], ["mail-77"])
        status, ticket = self.call("GET", f"/issues/{filed['ticket_id']}")
        self.assertEqual((ticket["status"], ticket["mail"][0]["instance_id"]), ("learned", "i1"))
        ws.close()


if __name__ == "__main__":
    unittest.main()
