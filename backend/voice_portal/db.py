"""Consumer-owned SQLite schema: versioned documents, deterministic chunks, FTS5, Theographic tables,
prayer capture ledger, durable work outbox. All of it lives in the state root, outside Git."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
  doc_id INTEGER PRIMARY KEY,
  source_family TEXT NOT NULL,
  source_doc_id TEXT NOT NULL,
  revision_hash TEXT NOT NULL,
  title TEXT,
  body TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  locators_json TEXT NOT NULL DEFAULT '[]',
  source_locator TEXT,
  observed_at TEXT,
  recorded_at TEXT,
  recorded_at_basis TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  imported_at REAL NOT NULL,
  current INTEGER NOT NULL DEFAULT 1,
  removed_at REAL,
  UNIQUE(source_family, source_doc_id, revision_hash)
);
CREATE INDEX IF NOT EXISTS idx_documents_current ON documents(source_family, current);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_family, source_doc_id);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES documents(doc_id),
  chunk_index INTEGER NOT NULL,
  start INTEGER NOT NULL,
  end INTEGER NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(doc_id, chunk_index)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, chunk_id UNINDEXED, doc_id UNINDEXED, source_family UNINDEXED,
  tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS import_receipts (
  receipt_id INTEGER PRIMARY KEY,
  source_family TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  source_path TEXT,
  selection_json TEXT NOT NULL DEFAULT '{}',
  counts_json TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
CREATE TABLE IF NOT EXISTS theographic_people (
  person_id TEXT PRIMARY KEY, lookup TEXT, display_title TEXT, name TEXT, status TEXT, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theographic_places (
  place_id TEXT PRIMARY KEY, lookup TEXT, display_title TEXT, status TEXT, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theographic_verses (
  osis_ref TEXT PRIMARY KEY, verse_id TEXT, book TEXT, chapter INTEGER, verse_num INTEGER,
  people_json TEXT NOT NULL DEFAULT '[]', places_json TEXT NOT NULL DEFAULT '[]', event TEXT, year_num TEXT
);
CREATE TABLE IF NOT EXISTS work_operations (
  operation_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  request_text TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  status TEXT NOT NULL,
  last_error TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS work_attempts (
  attempt_id INTEGER PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES work_operations(operation_id),
  started_at REAL NOT NULL,
  finished_at REAL,
  command_json TEXT NOT NULL,
  stdout TEXT,
  stderr TEXT,
  returncode INTEGER,
  outcome TEXT NOT NULL,
  message_id TEXT,
  run_id TEXT,
  target TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS work_artifacts (
  artifact_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES work_operations(operation_id),
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  bytes INTEGER,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS prayer_captures (
  client_capture_id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  status TEXT NOT NULL,
  recording_id TEXT,
  prayer_id TEXT,
  last_error TEXT,
  finalize_receipt_json TEXT,
  recording_json TEXT
);
CREATE TABLE IF NOT EXISTS prayer_segments (
  client_capture_id TEXT NOT NULL,
  segment_index INTEGER NOT NULL,
  client_sha256 TEXT NOT NULL,
  client_size INTEGER NOT NULL,
  intake_sha256 TEXT,
  intake_size INTEGER,
  durable INTEGER,
  status TEXT NOT NULL,
  uploaded_at REAL,
  receipt_json TEXT,
  last_error TEXT,
  PRIMARY KEY(client_capture_id, segment_index)
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, client_mode TEXT, remote TEXT
);
CREATE TABLE IF NOT EXISTS turns (
  turn_id TEXT PRIMARY KEY,
  session_id TEXT,
  created_at REAL NOT NULL,
  transcript TEXT NOT NULL,
  status TEXT NOT NULL,
  answer TEXT,
  citations_json TEXT NOT NULL DEFAULT '[]',
  model TEXT,
  first_delta_ms INTEGER,
  done_ms INTEGER,
  usage_json TEXT,
  error TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (and migrate) the consumer database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def connect_source(path: Path | str) -> sqlite3.Connection:
    """Open a producer-owned SQLite store strictly read-only."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"source store not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def now() -> float:
    return time.time()
