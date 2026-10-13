"""CI/dev fixture: a tiny synthetic corpus so the app and service can be exercised without the
retained stores. Everything here is labelled 'fixture'; it is never real-data evidence."""

from __future__ import annotations

import sqlite3

from .importer import import_family
from .sources import SourceRecord
from .sources.scripture import Verse, chapter_records
from .sources.theographic import load_theographic

PSALM_23 = [
    "Yahweh is my shepherd: I shall lack nothing.",
    "He makes me lie down in green pastures. He leads me beside still waters.",
    "He restores my soul. He guides me in the paths of righteousness for his name's sake.",
    "Even though I walk through the valley of the shadow of death, I will fear no evil, for you are with me. Your rod and your staff, they comfort me.",
    "You prepare a table before me in the presence of my enemies. You anoint my head with oil. My cup runs over.",
    "Surely goodness and loving kindness shall follow me all the days of my life, and I will dwell in Yahweh's house forever.",
]

EDITION = {"source_id": "fixture:WorldEnglishBible", "representation": "fixture", "edition_id": "fixture:WorldEnglishBible#eng", "name": "World English Bible (CI fixture copy)", "version": "fixture", "license": "Public Domain", "language": "eng", "catalog_version": "fixture", "store_manifest_id": "fixture"}

THEO = {
    "People.csv": "personLookup,status,personID,displayTitle,name\nDavid,accepted,fixture-p1,David,David\n",
    "Places.csv": "placeLookup,status,displayTitle,placeID\n",
    "Verses.csv": "osisRef,status,verseID,book,chapter,verseNum,verseText,people,places,event,yearNum\nPs.23.1,accepted,fx1,Psalms,23,1,,David,,,\n",
}


def load_fixture(conn: sqlite3.Connection) -> dict:
    prayers = [
        SourceRecord("prayer", "fixture-prayer-1", "Lord, you are my shepherd. Teach me to rest in you when the day is loud. (fixture)", title="Fixture prayer 1", recorded_at="1790000000", recorded_at_basis="fixture", metadata={"asr_model_id": "fixture"}),
        SourceRecord("prayer", "fixture-prayer-2", "Father, thank you for green pastures and still waters in a hard week. (fixture)", title="Fixture prayer 2", recorded_at="1790001000", recorded_at_basis="fixture", metadata={"asr_model_id": "fixture"}),
    ]
    teaching = [
        SourceRecord("teaching", "fixture://desiringgod/shepherd", "Fixture teaching text: the shepherd psalm describes rest as something God provides, not something we achieve. The still waters are his gift. (fixture)", title="Fixture: The Shepherd Who Gives Rest", recorded_at="2026-01-01", recorded_at_basis="fixture", metadata={"provider": "desiringgod", "publisher": "Desiring God (fixture)", "date": "2026-01-01"}),
    ]
    verses = (Verse("PSA", 23, n, text, f"Ps.23.{n}", "fixture-entry", "f" * 64, "fixture/psa.json", n * 100, n * 100 + len(text), str(n)) for n, text in enumerate(PSALM_23, 1))
    out = {
        "prayer": import_family(conn, "prayer", prayers, selection={"rule": "fixture"}, source_path="fixture"),
        "teaching": import_family(conn, "teaching", teaching, selection={"rule": "fixture"}, source_path="fixture"),
        "scripture": import_family(conn, "scripture", chapter_records(verses, EDITION), selection={"edition": EDITION, "rule": "fixture"}, source_path="fixture"),
        "theographic": load_theographic(conn, THEO, {"source_id": "fixture:theographic"}),
    }
    return out
