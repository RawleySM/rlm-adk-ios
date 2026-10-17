"""CLI: serve, import, search, cite, devotional, turn, readiness, work, prayer, tool, policy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import conversation, outbox, prayer_intake, retrieval
from .config import load_config
from .db import connect
from .importer import family_counts, import_family, latest_receipts
from .reasoning import probe_once, readiness
from .sources import SourceUnavailable
from .sources import reference_teaching
from .sources.prayers import read_prayers
from .sources.scripture import read_scripture, theographic_csv_bodies
from .sources.teaching import DOC_PREFIX as TEACHING_DOC_PREFIX
from .sources.teaching import read_teaching
from .sources.theographic import FILES as THEO_FILES
from .sources.theographic import load_theographic


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_import(cfg, families: list[str]) -> int:
    conn = connect(cfg.db_path)
    rc = 0
    for family in families:
        started = time.monotonic()
        try:
            if family == "prayer":
                records, selection = read_prayers(cfg.prayer_store)
                counts = import_family(conn, "prayer", records, selection=selection, source_path=cfg.prayer_store)
            elif family == "teaching":
                records, selection = read_teaching(cfg.youtube_store, limit_per_provider=cfg.teaching_limit_per_publisher, min_chars=cfg.teaching_min_chars)
                counts = import_family(conn, "teaching", records, selection=selection, source_path=cfg.youtube_store, membership_prefix=TEACHING_DOC_PREFIX)
            elif family == "reference_teaching":
                # Edwards ThML and BibleProject transcripts from the scripture_reference store, into the
                # teaching family under their own membership prefix and receipt name.
                records, selection = reference_teaching.read_reference_teaching(cfg.scripture_store, cfg.reference_teaching_sources, cfg.reader_path or None)
                counts = import_family(conn, reference_teaching.FAMILY, records, selection=selection, source_path=cfg.scripture_store, membership_prefix=reference_teaching.DOC_PREFIX, receipt_family="teaching:scripture_reference")
            elif family == "scripture":
                records, selection = read_scripture(cfg.scripture_store, cfg.scripture_source_id, cfg.scripture_representation, cfg.reader_path or None)
                counts = import_family(conn, "scripture", records, selection=selection, source_path=cfg.scripture_store)
            elif family == "theographic":
                bodies, identity = theographic_csv_bodies(cfg.scripture_store, cfg.theographic_source_id, cfg.reader_path or None, THEO_FILES)
                counts = load_theographic(conn, bodies, identity)
            else:
                print(f"unknown family {family}", file=sys.stderr)
                rc = 2
                continue
            _print({"family": family, "counts": counts, "seconds": round(time.monotonic() - started, 2)})
        except SourceUnavailable as exc:
            _print({"family": family, "error": "source_unavailable", "detail": str(exc)})
            rc = 1
    _print({"counts": family_counts(conn)})
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voice_portal")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("config")
    p = sub.add_parser("import")
    p.add_argument("families", nargs="*", default=["prayer", "teaching", "reference_teaching", "scripture", "theographic"])
    sub.add_parser("sources")
    sub.add_parser("fixture")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--family", default=None)
    p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("cite")
    p.add_argument("cite_id")
    p = sub.add_parser("devotional")
    p.add_argument("--passage", default=None)
    p.add_argument("--date", default=None)
    p = sub.add_parser("turn")
    p.add_argument("text")
    sub.add_parser("readiness")
    sub.add_parser("probe")
    p = sub.add_parser("work")
    p.add_argument("action", choices=["create", "send", "show", "list", "packet"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--kind", default="research")
    p = sub.add_parser("prayer")
    p.add_argument("action", choices=["status", "list", "health", "test-run"])
    p.add_argument("arg", nargs="?")
    p = sub.add_parser("tool", help="run one skill-service tool locally and print its JSON")
    p.add_argument("name", choices=["scripture", "word", "interpretations", "project"])
    p.add_argument("text", nargs="?", default="", help="reference (scripture), description (project) or topic (interpretations)")
    p.add_argument("--reference", default=None)
    p.add_argument("--topic", default=None)
    p.add_argument("--which", default="latest")
    p.add_argument("--confirm", default=None)
    p.add_argument("--refresh", action="store_true")
    sub.add_parser("policy")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "config":
        print(cfg.to_json())
        return 0
    if args.cmd == "policy":
        from .tools.policy import load_policy

        _print(load_policy(cfg))
        return 0
    if args.cmd == "serve":
        from .server import make_server

        cfg.ensure_dirs()
        server = make_server(cfg)
        logging.getLogger("voice_portal").info("serving on %s:%s state_root=%s commit=%s", cfg.listen_host, cfg.listen_port, cfg.root, cfg.source_commit or "?")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == "import":
        return cmd_import(cfg, args.families)
    conn = connect(cfg.db_path)
    if args.cmd == "fixture":
        from .fixture import load_fixture

        _print({"fixture": load_fixture(conn), "counts": family_counts(conn)})
        return 0
    if args.cmd == "sources":
        _print({"counts": family_counts(conn), "imports": latest_receipts(conn)})
    elif args.cmd == "search":
        _print(retrieval.search(conn, args.query, families=args.family.split(",") if args.family else None, limit=args.limit))
    elif args.cmd == "cite":
        _print(retrieval.resolve_citation(conn, args.cite_id))
    elif args.cmd == "devotional":
        _print(conversation.build_devotional(conn, cfg, passage=args.passage, date=args.date))
    elif args.cmd == "readiness":
        _print(readiness(cfg.inference_base_url, cfg.inference_status_url, cfg.inference_model))
    elif args.cmd == "probe":
        _print(probe_once(cfg.inference_base_url, cfg.inference_model, timeout=cfg.inference_timeout_s, extra_body=cfg.inference_extra_body))
    elif args.cmd == "turn":
        import threading
        import uuid

        def emit(ev):
            if ev.get("type") == "assistant_text_delta":
                sys.stdout.write(ev["text"])
                sys.stdout.flush()
            else:
                print()
                _print(ev)

        _print(conversation.run_turn(conn, cfg, turn_id=uuid.uuid4().hex, session_id=None, transcript=args.text, context=None, emit=emit, cancel=threading.Event()))
    elif args.cmd == "work":
        if args.action == "create":
            _print(outbox.create_operation(conn, kind=args.kind, request_text=args.arg or ""))
        elif args.action == "send":
            # Files the operation with Intake (an intake ticket; staged or mailed by intake_mail_mode).
            _print(outbox.send_to_intake(conn, cfg, args.arg))
        elif args.action == "show":
            _print(outbox.get_operation(conn, args.arg))
        elif args.action == "packet":
            _print(outbox.build_source_packet(conn, cfg, args.arg))
        else:
            _print({"operations": outbox.list_operations(conn), "allocator": outbox.allocator_status(cfg)})
    elif args.cmd == "tool":
        from .tools.interpretations import interpretations_tool
        from .tools.project import project_tool
        from .tools.scripture import scripture_tool
        from .tools.word import word_tool

        if args.name == "scripture":
            _print(scripture_tool(conn, args.text))
        elif args.name == "word":
            _print(word_tool(conn, cfg, args.which, refresh=args.refresh))
        elif args.name == "interpretations":
            _print(interpretations_tool(conn, args.reference, args.topic or args.text or None))
        else:
            _print(project_tool(cfg, args.text, args.confirm))
    elif args.cmd == "prayer":
        if args.action == "health":
            _print(prayer_intake.intake_health(cfg.prayer_intake_url))
        elif args.action == "status":
            _print(prayer_intake.capture_status(conn, cfg.prayer_intake_url, capture_id=args.arg))
        elif args.action == "test-run":
            _print(prayer_intake.mint_test_run(cfg.prayer_intake_url))
        else:
            _print([dict(r) for r in conn.execute("SELECT * FROM prayer_captures ORDER BY created_at DESC")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
