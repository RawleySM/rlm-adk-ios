"""Bounded Theographic adapter: People, Places and Verses loaded verbatim with original identifiers,
joined to retained WEB verse documents by OSIS reference. Unresolved mappings stay visible."""

from __future__ import annotations

import csv
import io
import json
import sqlite3

from ..db import now

FILES = ("People.csv", "Places.csv", "Verses.csv")


def _split_ids(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def load_theographic(conn: sqlite3.Connection, bodies: dict[str, str], identity: dict) -> dict:
    """Replace the three tables from the verified CSV bodies. Returns counts."""
    counts: dict = {}
    with conn:
        conn.execute("DELETE FROM theographic_people")
        conn.execute("DELETE FROM theographic_places")
        conn.execute("DELETE FROM theographic_verses")
        people = list(csv.DictReader(io.StringIO(bodies["People.csv"])))
        for row in people:
            conn.execute(
                "INSERT OR REPLACE INTO theographic_people(person_id, lookup, display_title, name, status, raw_json) VALUES (?,?,?,?,?,?)",
                (row.get("personID"), row.get("personLookup"), row.get("displayTitle"), row.get("name"), row.get("status"), json.dumps(row, ensure_ascii=False)),
            )
        places = list(csv.DictReader(io.StringIO(bodies["Places.csv"])))
        for row in places:
            conn.execute(
                "INSERT OR REPLACE INTO theographic_places(place_id, lookup, display_title, status, raw_json) VALUES (?,?,?,?,?)",
                (row.get("placeID"), row.get("placeLookup"), row.get("displayTitle"), row.get("status"), json.dumps(row, ensure_ascii=False)),
            )
        verse_count = 0
        for row in csv.DictReader(io.StringIO(bodies["Verses.csv"])):
            osis = row.get("osisRef") or ""
            if not osis:
                continue
            verse_count += 1
            conn.execute(
                "INSERT OR REPLACE INTO theographic_verses(osis_ref, verse_id, book, chapter, verse_num, people_json, places_json, event, year_num) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    osis,
                    row.get("verseID"),
                    row.get("book"),
                    int(row["chapter"]) if (row.get("chapter") or "").isdigit() else None,
                    int(row["verseNum"]) if (row.get("verseNum") or "").isdigit() else None,
                    json.dumps(_split_ids(row.get("people", ""))),
                    json.dumps(_split_ids(row.get("places", ""))),
                    row.get("event"),
                    row.get("yearNum"),
                ),
            )
        counts = {"people": len(people), "places": len(places), "verses": verse_count}
        conn.execute(
            "INSERT INTO import_receipts(source_family, started_at, finished_at, source_path, selection_json, counts_json) VALUES (?,?,?,?,?,?)",
            ("theographic", now(), now(), identity.get("source_id"), json.dumps(identity), json.dumps(counts)),
        )
    return counts


def passage_entities(conn: sqlite3.Connection, osis_refs: list[str]) -> dict:
    """People and places Theographic attaches to the given OSIS verse ids, with unresolved ids listed.

    `people_json`/`places_json` hold Theographic's own lookup keys (Airtable record names). They are
    resolved against `lookup` first, then `person_id`/`place_id`; anything else is reported unresolved.
    """
    people: dict[str, dict] = {}
    places: dict[str, dict] = {}
    unresolved_people: set[str] = set()
    unresolved_places: set[str] = set()
    matched_verses: list[str] = []
    missing_verses: list[str] = []
    for osis in osis_refs:
        row = conn.execute("SELECT * FROM theographic_verses WHERE osis_ref = ?", (osis,)).fetchone()
        if row is None:
            missing_verses.append(osis)
            continue
        matched_verses.append(osis)
        for key in json.loads(row["people_json"]):
            person = conn.execute(
                "SELECT person_id, lookup, display_title, name, status FROM theographic_people WHERE lookup = ? OR person_id = ?",
                (key, key),
            ).fetchone()
            if person is None:
                unresolved_people.add(key)
            else:
                entry = people.setdefault(person["person_id"], {**dict(person), "verses": []})
                entry["verses"].append(osis)
        for key in json.loads(row["places_json"]):
            place = conn.execute(
                "SELECT place_id, lookup, display_title, status FROM theographic_places WHERE lookup = ? OR place_id = ?",
                (key, key),
            ).fetchone()
            if place is None:
                unresolved_places.add(key)
            else:
                entry = places.setdefault(place["place_id"], {**dict(place), "verses": []})
                entry["verses"].append(osis)
    return {
        "requested": osis_refs,
        "matched_verses": matched_verses,
        "missing_verses": missing_verses,
        "people": list(people.values()),
        "places": list(places.values()),
        "unresolved_people_keys": sorted(unresolved_people),
        "unresolved_place_keys": sorted(unresolved_places),
        "source": "github:robertrouse/theographic-bible-metadata (original identifiers; not a theological answer engine)",
    }
