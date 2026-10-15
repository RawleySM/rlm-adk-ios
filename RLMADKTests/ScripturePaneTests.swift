import XCTest
@testable import RLMADK

/// The scripture pane: `passage` / `reading` messages, client messages out, reading progress and
/// word shading, the book catalog and chapter stepping, and the model's handling of the events.
final class ScripturePaneTests: XCTestCase {
    private let psalm23 = #"{"type":"passage","reference":"Psalm 23","title":"Psalm 23 (World English Bible)","edition":"World English Bible","book":"PSA","chapter":23,"verses":[{"verse":1,"text":"Yahweh is my shepherd; I shall lack nothing.","osis":"Ps.23.1"},{"verse":2,"text":"He makes me lie down in green pastures. He leads me beside still waters.","osis":"Ps.23.2"},{"verse":3,"text":"He restores my soul.","osis":"Ps.23.3"}],"spoken_count":0,"source":"tool"}"#

    func testPassageMessageParses() throws {
        let events = TalkMessageParser.events(fromText: psalm23)
        guard case .passage(let p)? = events.first else { return XCTFail("no passage event: \(events)") }
        XCTAssertEqual(p.reference, "Psalm 23")
        XCTAssertEqual(p.book, "PSA")
        XCTAssertEqual(p.chapter, 23)
        XCTAssertEqual(p.verses.count, 3)
        XCTAssertEqual(p.verses[1].verse, 2)
        XCTAssertEqual(p.shortReference, "Ps 23")
        XCTAssertEqual(p.source, "tool")
    }

    func testPassageArrivesAsServerMessageToo() {
        let wrapped = #"{"label":"rtvi-ai","type":"server-message","data":"# + psalm23 + "}"
        guard case .passage(let p)? = TalkMessageParser.events(fromText: wrapped).first else { return XCTFail("no passage via server-message") }
        XCTAssertEqual(p.reference, "Psalm 23")
    }

    func testReadingAndErrorMessagesParse() {
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"reading","state":"verse","reference":"Psalm 23","verse":2,"index":1,"spoken_count":1}"#),
                       [.reading(ReadingEvent(state: "verse", reference: "Psalm 23", verse: 2, index: 1, spokenCount: 1))])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"reading","state":"verse_done","reference":"Psalm 23","verse":2,"index":1,"spoken_count":2,"duration_s":5.2}"#),
                       [.reading(ReadingEvent(state: "verse_done", reference: "Psalm 23", verse: 2, index: 1, spokenCount: 2, durationS: 5.2))])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"passage_error","reference":"Hezekiah 3","reason":"not a book"}"#),
                       [.passageError(reference: "Hezekiah 3", reason: "not a book")])
    }

    func testClientMessagesOnTheWire() {
        XCTAssertEqual(TalkClientMessage.clientPassage(reference: "Romans 12", reason: "select").json["type"] as? String, "client_passage")
        XCTAssertEqual(TalkClientMessage.clientPassage(reference: "Romans 12", reason: "select").json["reference"] as? String, "Romans 12")
        XCTAssertEqual(TalkClientMessage.clientRead(action: "pause").json["type"] as? String, "client_read")
        XCTAssertEqual(TalkClientMessage.clientRead(action: "pause").json["action"] as? String, "pause")
    }

    func testReadingProgressFollowsTheVerses() {
        var progress = ReadingProgress()
        let t0 = Date(timeIntervalSince1970: 1000)
        progress.apply(ReadingEvent(state: "started", reference: "Psalm 23", spokenCount: 0), now: t0)
        XCTAssertTrue(progress.isReading)
        progress.apply(ReadingEvent(state: "verse", reference: "Psalm 23", verse: 1, index: 0), now: t0)
        XCTAssertEqual(progress.activeIndex, 0)
        let text = "Yahweh is my shepherd; I shall lack nothing."
        XCTAssertEqual(progress.fraction(of: text, at: t0), 0, accuracy: 0.001)
        XCTAssertGreaterThan(progress.fraction(of: text, at: t0.addingTimeInterval(1.5)), 0.3)
        XCTAssertEqual(progress.fraction(of: text, at: t0.addingTimeInterval(60)), 0.97, accuracy: 0.001, "never shown complete before the server says so")
        progress.apply(ReadingEvent(state: "verse_done", reference: "Psalm 23", verse: 1, index: 0, spokenCount: 1, durationS: 2.0), now: t0.addingTimeInterval(2)) { _ in text }
        XCTAssertEqual(progress.spokenCount, 1)
        XCTAssertNil(progress.activeIndex)
        XCTAssertGreaterThan(progress.charactersPerSecond, 14, "calibrated toward the measured 22 characters per second")
        progress.apply(ReadingEvent(state: "paused", reference: "Psalm 23", spokenCount: 1))
        XCTAssertFalse(progress.isReading)
        XCTAssertEqual(progress.spokenCount, 1)
    }

    func testBookCatalogAndChapterSteps() throws {
        XCTAssertEqual(BibleBook.all.count, 66)
        XCTAssertEqual(BibleBook.all.reduce(0) { $0 + $1.chapters }, 1189)
        XCTAssertEqual(BibleBook.all.filter(\.newTestament).count, 27)
        XCTAssertEqual(BibleBook.byCode["PSA"]?.reference(chapter: 23), "Psalm 23")
        XCTAssertEqual(BibleBook.byCode["ROM"]?.reference(chapter: 12), "Romans 12")
        let next = try XCTUnwrap(BibleBook.neighbour(of: "MAL", chapter: 4, step: 1))
        XCTAssertEqual(next.0.code, "MAT"); XCTAssertEqual(next.1, 1)
        let previous = try XCTUnwrap(BibleBook.neighbour(of: "EXO", chapter: 1, step: -1))
        XCTAssertEqual(previous.0.code, "GEN"); XCTAssertEqual(previous.1, 50)
        XCTAssertNil(BibleBook.neighbour(of: "GEN", chapter: 1, step: -1))
        XCTAssertNil(BibleBook.neighbour(of: "REV", chapter: 22, step: 1))
    }

    func testParagraphsCloseAtSentenceEnds() {
        let verses = (1...12).map { ScriptureVerse(verse: $0, text: String(repeating: "word ", count: 20) + ($0 % 3 == 0 ? "end." : "and"), osis: nil) }
        let paragraphs = ScriptureParagraphs.split(verses)
        XCTAssertEqual(paragraphs.flatMap { $0 }, Array(0..<12), "every verse once, in order")
        for paragraph in paragraphs.dropLast() {
            XCTAssertTrue(verses[paragraph.last!].text.hasSuffix("."), "a paragraph ends on a sentence end")
        }
    }

    @MainActor
    func testModelKeepsThePaneAndProgress() throws {
        let model = PortalModel()
        guard case .passage(let p)? = TalkMessageParser.events(fromText: psalm23).first else { return XCTFail() }
        model.talkEventReceived(.passage(p))
        XCTAssertEqual(model.passage?.reference, "Psalm 23")
        XCTAssertNil(model.passageRequested)
        model.talkEventReceived(.reading(ReadingEvent(state: "started", reference: "Psalm 23")))
        model.talkEventReceived(.reading(ReadingEvent(state: "verse", reference: "Psalm 23", verse: 1, index: 0)))
        XCTAssertTrue(model.reading.isReading)
        XCTAssertEqual(model.reading.activeIndex, 0)
        model.talkEventReceived(.reading(ReadingEvent(state: "verse_done", reference: "Psalm 23", verse: 1, index: 0, spokenCount: 1, durationS: 3)))
        model.talkEventReceived(.reading(ReadingEvent(state: "paused", reference: "Psalm 23", spokenCount: 1)))
        XCTAssertFalse(model.reading.isReading)
        XCTAssertEqual(model.passage?.spokenCount, 1)
        // Progress of another passage (a late message) is ignored.
        model.talkEventReceived(.reading(ReadingEvent(state: "started", reference: "John 1")))
        XCTAssertFalse(model.reading.isReading)
    }
}
